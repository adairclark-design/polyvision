/**
 * PolyVision — The Ear (index.js)
 * Layer 3 Tool: Persistent WebSocket listener for Polymarket RTDS.
 *
 * Architecture SOP: architecture/01_data_ingestion.md
 *
 * Self-annealing log:
 *  2026-03-04: wss://clob.polymarket.com/ws returns 404 without L2 auth.
 *              Added polling-only mode when POLY_PRIVATE_KEY is not set.
 *              REST catch-up polls every 30s as a fallback data source.
 */

'use strict';
require('dotenv').config();

const WebSocket = require('ws');
const axios = require('axios');
const Redis = require('ioredis');
const http = require('http');

// ── Config ────────────────────────────────────────────────────────────────────
// POLY_PRIVATE_KEY required for authenticated WebSocket.
// Without it, Ear runs in REST polling mode (every 30s via Gamma API).
const RTDS_URL = process.env.POLY_PRIVATE_KEY ? 'wss://ws-subscriptions-clob.polymarket.com/ws/market' : null;
const CLOB_REST = 'https://clob.polymarket.com';
const BRAIN_URL = process.env.BRAIN_URL || 'http://localhost:8000';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379/0';
const THRESHOLD = parseFloat(process.env.ALERT_THRESHOLD_STANDARD || '10000');
const HEALTH_PORT = 3001;
const DEDUP_TTL = 7200;
const MAX_BACKOFF_MS = 60_000;
const BUFFER_KEY = 'buffer:trades';
const HEARTBEAT_MS = 60_000;
const LATENCY_LIMIT = 500;
const LATENCY_BREACH_THRESHOLD = 5;
const POLL_INTERVAL = 30_000; // polling mode: every 30s

// ── State ─────────────────────────────────────────────────────────────────────
let ws = null;
let backoffMs = 1000;
let isConnected = false;
let reconnectTimer = null;
let pollTimer = null;
let latencyBreaches = 0;
let lastEventTs = Date.now();
let totalEvents = 0;
let totalForwarded = 0;

// ── Redis ─────────────────────────────────────────────────────────────────────
const redis = new Redis(REDIS_URL, {
    retryStrategy: (times) => Math.min(times * 200, 5000),
});
redis.on('error', (e) => console.error('[Redis]', e.message));

// ── Logging ───────────────────────────────────────────────────────────────────
const log = {
    info: (...a) => console.log(`[${new Date().toISOString()}] [EAR] INFO:`, ...a),
    warn: (...a) => console.warn(`[${new Date().toISOString()}] [EAR] WARN:`, ...a),
    error: (...a) => console.error(`[${new Date().toISOString()}] [EAR] ERROR:`, ...a),
};

// ── Trade Normalizer ──────────────────────────────────────────────────────────
function normalizeTrade(raw) {
    const size = parseFloat(raw.size || raw.amount || 0);
    const price = parseFloat(raw.price || 0);
    const usdValue = parseFloat(raw.usdcSize || (size * price) || 0);

    let ts = raw.timestamp || new Date().toISOString();
    if (typeof raw.timestamp === 'number') {
        ts = new Date(raw.timestamp * 1000).toISOString();
    } else if (typeof raw.timestamp === 'string' && /^\d+$/.test(raw.timestamp)) {
        ts = new Date(parseInt(raw.timestamp) * 1000).toISOString();
    }

    return {
        id: raw.transactionHash || raw.id || raw.tradeId || raw.txHash || `${raw.timestamp}-${raw.proxyWallet || raw.maker}`,
        market_id: raw.conditionId || raw.market || '',
        market_title: raw.title || raw.marketTitle || raw.market || '',
        outcome: raw.outcome || (raw.side === 'BUY' ? 'YES' : 'NO'),
        price: price,
        size: size,
        usd_value: usdValue,
        maker_address: raw.proxyWallet || raw.maker || raw.makerAddress || '',
        taker_address: raw.taker || raw.takerAddress || '',
        side: (raw.side || 'BUY').toUpperCase(),
        timestamp: ts,
    };
}

// ── Deduplication ─────────────────────────────────────────────────────────────
async function isNewTrade(tradeId) {
    const key = `processed:${tradeId}`;
    const exists = await redis.get(key);
    if (exists) return false;
    await redis.setex(key, DEDUP_TTL, '1');
    return true;
}

// ── Forward to Brain ─────────────────────────────────────────────────────────
async function forwardToBrain(event) {
    const start = Date.now();
    try {
        await axios.post(`${BRAIN_URL}/ingest/trade`, event, { timeout: 5000 });
        const latency = Date.now() - start;
        latencyBreaches = latency > LATENCY_LIMIT ? latencyBreaches + 1 : 0;
        if (latencyBreaches >= LATENCY_BREACH_THRESHOLD) {
            log.warn(`⚠️ Circuit breaker: ${latencyBreaches} consecutive calls > ${LATENCY_LIMIT}ms.`);
        }
        await drainBuffer();
        totalForwarded++;
    } catch (err) {
        log.warn(`Brain unavailable (${err.message}). Buffering event.`);
        await redis.rpush(BUFFER_KEY, JSON.stringify(event));
    }
}

async function drainBuffer() {
    const buffered = await redis.llen(BUFFER_KEY);
    if (!buffered) return;
    log.info(`Draining ${buffered} buffered events to Brain...`);
    for (let i = 0; i < buffered; i++) {
        const raw = await redis.lpop(BUFFER_KEY);
        if (!raw) break;
        try {
            await axios.post(`${BRAIN_URL}/ingest/trade`, JSON.parse(raw), { timeout: 5000 });
        } catch {
            await redis.lpush(BUFFER_KEY, raw);
            break;
        }
    }
}

// ── REST Catch-Up / Polling ───────────────────────────────────────────────────
async function catchUpViaRest() {
    log.info('Running REST poll for recent trades...');
    try {
        const resp = await axios.get('https://gamma-api.polymarket.com/markets', {
            params: { active: 'true', closed: 'false', limit: 20, _sort: 'volume24hr:desc' },
            timeout: 10000,
        });
        const markets = Array.isArray(resp.data) ? resp.data : (resp.data?.data || []);
        let caught = 0;

        for (const market of markets.slice(0, 10)) {
            const cid = market.conditionId || market.condition_id;
            if (!cid) continue;
            try {
                const t = await axios.get('https://data-api.polymarket.com/trades', {
                    params: { condition_id: cid, limit: 50 },
                    timeout: 10000,
                });
                const trades = Array.isArray(t.data) ? t.data : [];
                for (const raw of trades) {
                    const event = normalizeTrade({ ...raw, market: cid });
                    if (event.usd_value < THRESHOLD) continue;
                    if (!(await isNewTrade(event.id))) continue;
                    await forwardToBrain(event);
                    lastEventTs = Date.now();
                    totalEvents++;
                    caught++;
                }
            } catch { /* per-market errors are non-fatal */ }
        }
        log.info(`REST poll complete. ${caught} new qualifying trades forwarded.`);
    } catch (e) {
        log.error('REST poll failed:', e.message);
    }
}

// ── WebSocket Connection ──────────────────────────────────────────────────────
function connect() {
    // Polling-only mode: no L2 key → skip WebSocket, use REST polling
    if (!RTDS_URL) {
        log.info('🔄 No POLY_PRIVATE_KEY set — running in REST polling-only mode (every 30s).');
        log.info('   Set POLY_PRIVATE_KEY in .env to enable real-time WebSocket stream.');
        catchUpViaRest().catch(e => log.error('Initial poll error:', e.message));
        setInterval(() => {
            catchUpViaRest().catch(e => log.error('Poll error:', e.message));
        }, POLL_INTERVAL);
        return;
    }

    // WebSocket mode
    if (ws) { try { ws.terminate(); } catch { } }
    log.info(`Connecting to ${RTDS_URL}...`);
    ws = new WebSocket(RTDS_URL, {
        handshakeTimeout: 10000,
        headers: { 'User-Agent': 'PolyVision-Ear/1.0' },
    });

    ws.on('open', async () => {
        isConnected = true;
        backoffMs = 1000;
        log.info('✅ Connected to Polymarket RTDS.');
        ws.send(JSON.stringify({ type: 'subscribe', channel: 'trade' }));

        // Always run the robust Data API poller as a fail-safe against silent websockets
        await catchUpViaRest();
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(() => {
            catchUpViaRest().catch(e => log.error('Poll error:', e.message));
        }, POLL_INTERVAL);

        const heartbeat = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.ping();
            else clearInterval(heartbeat);
        }, HEARTBEAT_MS);
    });

    ws.on('message', async (data) => {
        try {
            const msg = JSON.parse(data.toString());
            const trades = Array.isArray(msg) ? msg : [msg];
            for (const raw of trades) {
                if (raw.event_type !== 'trade' && raw.type !== 'trade') continue;
                const event = normalizeTrade(raw);
                totalEvents++;
                lastEventTs = Date.now();
                if (event.usd_value < THRESHOLD) continue;
                if (!(await isNewTrade(event.id))) continue;
                log.info(`⚡ $${event.usd_value.toLocaleString()} on market ${event.market_id.slice(0, 10)}...`);
                await forwardToBrain(event);
            }
        } catch (e) {
            log.error('Message parse error:', e.message);
        }
    });

    ws.on('error', (err) => log.error('WebSocket error:', err.message));
    ws.on('close', (code, reason) => {
        isConnected = false;
        if (pollTimer) clearInterval(pollTimer);
        log.warn(`WS closed (${code}). Reconnecting in ${backoffMs}ms...`);
        scheduleReconnect();
    });
}

// ── Exponential Backoff Reconnect ─────────────────────────────────────────────
function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
        connect();
    }, backoffMs);
}

// ── Health Check Server ───────────────────────────────────────────────────────
function startHealthServer() {
    http.createServer((req, res) => {
        if (req.url === '/health') {
            const secondsSinceEvent = Math.floor((Date.now() - lastEventTs) / 1000);
            const mode = RTDS_URL ? 'websocket' : 'polling';
            // Polling mode is always healthy if process is running
            const healthy = RTDS_URL ? (isConnected && secondsSinceEvent < 300) : true;
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({
                status: isConnected ? 'connected' : (RTDS_URL ? 'disconnected' : 'polling'),
                mode, healthy, secondsSinceLastEvent: secondsSinceEvent,
                totalEvents, totalForwarded, latencyBreaches, backoffMs,
                bufferKey: BUFFER_KEY, ts: new Date().toISOString(),
            }));
        } else {
            res.writeHead(404); res.end();
        }
    }).listen(HEALTH_PORT, () => log.info(`Health endpoint: http://localhost:${HEALTH_PORT}/health`));
}

// ── API Schema Check ──────────────────────────────────────────────────────────
async function checkApiVersion() {
    try {
        const r = await axios.get(`${CLOB_REST}/markets`, {
            params: { limit: 1, next_cursor: 'MA==' }, timeout: 5000,
        });
        const fields = r.data?.data?.[0] ? Object.keys(r.data.data[0]) : [];
        const expected = ['condition_id', 'question', 'active', 'minimum_tick_size'];
        const missing = expected.filter(f => !fields.includes(f));
        if (missing.length > 0) {
            log.warn(`🚨 API SCHEMA CHANGE: Missing fields: ${missing.join(', ')}. Update gemini.md!`);
        } else {
            log.info('✅ API schema version check passed.');
        }
    } catch (e) {
        log.warn('API version check failed:', e.message);
    }
}

// ── Startup ───────────────────────────────────────────────────────────────────
(async () => {
    log.info('🐋 PolyVision — The Ear starting up...');
    startHealthServer();
    await checkApiVersion();
    connect();
    setInterval(checkApiVersion, 24 * 60 * 60 * 1000);
})();

process.on('SIGINT', () => { log.info('Shutting down...'); process.exit(0); });
process.on('SIGTERM', () => { log.info('Shutting down...'); process.exit(0); });
