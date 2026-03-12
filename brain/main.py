"""
PolyVision — The Brain (main.py)
Layer 3 Tool: FastAPI service — receives trade events from The Ear,
runs the Signal Engine + Whale Profiler + AI Summarizer + Notifier pipeline.
Also serves a WebSocket endpoint for the live dashboard feed.

Architecture SOP: architecture/02_signal_engine.md, 03_whale_profiler.md,
                  04_ai_summarizer.md, 05_notification_delivery.md
"""

import os
import json
import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import redis.asyncio as aioredis
import psycopg2
import psycopg2.extras
import httpx
import stripe

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Import our Layer 3 tools
import sys
sys.path.insert(0, '/app/tools')
from signal_engine   import build_alert
from whale_profiler  import profile_trade, init_db, generate_handle
from ai_summarizer   import summarize
from notifier        import deliver
from paper_trader    import follow as paper_follow, unfollow as paper_unfollow, get_portfolio as paper_portfolio
from leaderboard      import get_leaderboard
from wallet_xray      import get_xray as get_wallet_xray
from cluster_detector import check_cluster
from morning_briefing import run_briefing as _run_briefing

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_URL      = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
DATABASE_URL   = os.getenv('DATABASE_URL', '')
CACHE_KEY      = 'cache:last100trades'   # Redis sorted set
ALERT_STREAM   = 'stream:alerts:live'    # Redis stream for dashboard
LOG_FILE       = '.tmp/brain.log'
BRIEFING_HOUR  = int(os.getenv('BRIEFING_HOUR_EST', '8'))  # 8 = 08:00 AM EST

STRIPE_API_KEY        = os.getenv('STRIPE_API_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
CLERK_SECRET_KEY      = os.getenv('CLERK_SECRET_KEY', '')

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

os.makedirs('.tmp', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BRAIN] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger(__name__)

# ── Global connections ────────────────────────────────────────────────────────
redis_client: Optional[aioredis.Redis] = None
ws_clients: list[WebSocket] = []   # connected dashboard WebSocket clients

# ── Startup / Shutdown ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
    log.info('Redis connected.')
    if DATABASE_URL:
        try:
            init_db()
            log.info('PostgreSQL tables initialized.')
        except Exception as e:
            log.warning(f'DB init skipped (no connection?): {e}')

    # ── Morning Alpha Briefing Scheduler ─────────────────────────────────────
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: asyncio.get_event_loop().run_in_executor(None, _run_briefing),
        trigger=CronTrigger(hour=BRIEFING_HOUR, minute=0, timezone='America/New_York'),
        id='morning_briefing',
        name=f'Morning Alpha Briefing ({BRIEFING_HOUR:02d}:00 EST)',
        replace_existing=True,
    )
    scheduler.start()
    log.info(f'Briefing scheduler started — fires daily at {BRIEFING_HOUR:02d}:00 EST.')

    yield
    scheduler.shutdown(wait=False)
    await redis_client.aclose()
    log.info('Redis disconnected.')

app = FastAPI(title='PolyVision Brain', version='1.0.0', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],   # tighten in production
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── Data Models ───────────────────────────────────────────────────────────────
class TradeEvent(BaseModel):
    id:            str
    market_id:     str
    market_title:  str
    outcome:       str
    price:         float
    size:          float
    usd_value:     float
    maker_address: str
    taker_address: str = ''
    side:          str = 'BUY'
    timestamp:     str = ''

# ── Pipeline Helpers ──────────────────────────────────────────────────────────
ADJECTIVES = ['Strategist','Oracle','Tactician','Visionary','Analyst',
              'Architect','Sentinel','Navigator','Pioneer','Scholar']
REGIONS    = ['Oregon','the Midwest','Texas','New York','California',
              'Chicago','Seattle','Miami','Boston','Denver']

def _quick_handle(wallet: str) -> str:
    h = int(hashlib.sha256(wallet.encode()).hexdigest(), 16)
    return f"The {ADJECTIVES[h % len(ADJECTIVES)]} of {REGIONS[(h >> 8) % len(REGIONS)]}"

async def run_pipeline(event_dict: dict):
    """Full pipeline: Signal → Profile → AI → Notify → Cache → Stream to dashboard."""
    try:
        # 1. Signal Engine (deterministic filter)
        whale_profile = None
        if DATABASE_URL:
            whale_profile = profile_trade(event_dict)

        # Build a minimal profile if DB is unavailable
        if not whale_profile:
            whale_profile = {
                'wallet_address': event_dict['maker_address'],
                'handle':         _quick_handle(event_dict['maker_address']),
                'win_rate':       None,
                'roi_30d':        None,
            }

        alert = build_alert(event_dict, whale_profile)
        if not alert:
            return   # filtered out by threshold

        # 1b. Cluster Detection — check if 3+ whales on same side within 15 min
        #     If a cluster is found, promote the alert to CLUSTER tier
        cluster = await asyncio.get_event_loop().run_in_executor(
            None, check_cluster, event_dict, alert
        )
        if cluster:
            alert = cluster
            log.info(f"🚨 CLUSTER OVERRIDE: {alert['cluster_count']} whales on "
                     f"'{alert['market_title'][:40]}' | Total: ${alert['usd_value']:,.0f}")

        # 2. AI Summary (async-compatible via thread pool)
        alert = await asyncio.get_event_loop().run_in_executor(None, summarize, alert)

        log.info(f"[{alert['alert_tier']}] {alert['trader_handle']} "
                 f"${alert['usd_value']:,.0f} on '{alert['market_title'][:40]}'")

        # 3. Push to Redis "Last 100 Trades" sorted set (timestamp score)
        score = datetime.now(timezone.utc).timestamp()
        payload = json.dumps(alert)
        await redis_client.zadd(CACHE_KEY, {payload: score})
        await redis_client.zremrangebyrank(CACHE_KEY, 0, -101)   # keep top 100

        # 4. Push to Redis stream for dashboard WebSocket clients
        await redis_client.xadd(ALERT_STREAM, {'payload': payload}, maxlen=500)

        # 5. Broadcast to all connected dashboard WebSocket clients
        dead = []
        for ws in ws_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            ws_clients.remove(ws)

        # 6. Notify (push / Discord / Telegram) — run in executor to not block
        await asyncio.get_event_loop().run_in_executor(
            None, deliver, alert, False
        )

    except Exception as e:
        log.error(f'Pipeline error: {e}', exc_info=True)

# ── Endpoints ─────────────────────────────────────────────────────────────────

# ── Paper Trading Endpoints ───────────────────────────────────────────────────

class PaperFollowRequest(BaseModel):
    alert_id:      str = ''
    market_id:     str
    market_title:  str
    outcome:       str
    price:         float
    usd_value:     float
    trader_handle: str
    conviction:    int = 5


@app.post('/paper/follow', status_code=201)
async def paper_follow_trade(req: PaperFollowRequest):
    """Record a paper trade entry when a user mock-follows a whale alert."""
    record = await asyncio.get_event_loop().run_in_executor(
        None, paper_follow, req.model_dump()
    )
    return {'status': 'followed', 'trade': record}


@app.delete('/paper/follow/{trade_id}', status_code=200)
async def paper_unfollow_trade(trade_id: str):
    """Remove a paper trade from the portfolio."""
    deleted = await asyncio.get_event_loop().run_in_executor(
        None, paper_unfollow, trade_id
    )
    if not deleted:
        raise HTTPException(404, 'Trade not found in paper portfolio.')
    return {'status': 'unfollowed', 'trade_id': trade_id}


@app.get('/paper/portfolio')
async def paper_get_portfolio():
    """
    Returns the full paper portfolio with real-time P&L.
    Fetches current prices from Polymarket CLOB — may take a few seconds
    if many positions are open. Results are cached in Redis between calls.
    """
    result = await asyncio.get_event_loop().run_in_executor(
        None, paper_portfolio
    )
    return result


@app.get('/leaderboard')
async def leaderboard_endpoint(limit: int = 100, refresh: bool = False):
    """
    Returns the top-N Polymarket traders by all-time P&L.
    Cached in Redis for 5 minutes. Pass ?refresh=true to force a fresh fetch.
    """
    rows = await asyncio.get_event_loop().run_in_executor(
        None, lambda: get_leaderboard(limit=min(limit, 100), force_refresh=refresh)
    )
    return {'count': len(rows), 'traders': rows}


@app.get('/wallet/{address}/xray')
async def wallet_xray_endpoint(address: str, refresh: bool = False):
    """
    Returns full X-Ray profile for a Polymarket wallet:
    - All-time stats (P&L, volume)
    - Per-market position breakdown (profit / underwater)
    - Equity curve data points for charting
    - Last 50 trade history rows
    Cached in Redis for 60 seconds per wallet.
    Pass ?refresh=true to force a fresh fetch.
    """
    if not address.startswith('0x') or len(address) < 10:
        raise HTTPException(400, 'Invalid wallet address format.')
    profile = await asyncio.get_event_loop().run_in_executor(
        None, lambda: get_wallet_xray(address, force_refresh=refresh)
    )
    return profile


# ── Health ────────────────────────────────────────────────────────────────────
@app.get('/health')
async def health():
    """Dead Man's Switch health endpoint."""
    checks = {}
    # Redis
    try:
        await redis_client.ping()
        checks['redis'] = 'ok'
    except Exception:
        checks['redis'] = 'error'
    # DB
    checks['db'] = 'ok' if DATABASE_URL else 'not_configured'
    return {
        'status': 'ok' if all(v == 'ok' for v in checks.values()) else 'degraded',
        'checks': checks,
        'ws_clients': len(ws_clients),
        'ts': datetime.now(timezone.utc).isoformat(),
    }


@app.post('/webhooks/stripe')
async def stripe_webhook(request: Request):
    """Listens for Stripe 'checkout.session.completed' and upgrades the Clerk user to PRO."""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature', '')
    
    if not STRIPE_WEBHOOK_SECRET:
        log.warning("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured.")
        return {"status": "ignored"}

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        log.error(f"Stripe Webhook Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
        
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        clerk_id = session.get('client_reference_id')
        if clerk_id and CLERK_SECRET_KEY:
            # Update Clerk user metadata (tier: PRO)
            async with httpx.AsyncClient() as client:
                res = await client.patch(
                    f"https://api.clerk.com/v1/users/{clerk_id}/metadata",
                    headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
                    json={"public_metadata": {"tier": "PRO"}}
                )
                if res.status_code != 200:
                    log.error(f"Failed to update Clerk user {clerk_id}: {res.text}")
                else:
                    log.info(f"Successfully upgraded Clerk user {clerk_id} to PRO")
    return {"status": "success"}

@app.post('/ingest/trade', status_code=202)
async def ingest_trade(event: TradeEvent, background_tasks: BackgroundTasks):
    """Receives a qualifying TradeEvent from The Ear and runs the full pipeline."""
    background_tasks.add_task(run_pipeline, event.model_dump())
    return {'status': 'queued', 'id': event.id}

@app.get('/trades/recent')
async def recent_trades(limit: int = 20):
    """Returns the last N whale alerts from the Redis cache (sub-50ms)."""
    raw = await redis_client.zrevrange(CACHE_KEY, 0, limit - 1)
    return [json.loads(r) for r in raw]

# ── Morning Alpha Briefing Endpoints ─────────────────────────────────────────
@app.get('/briefing/latest')
async def briefing_latest():
    """Returns the most recently generated Morning Alpha Report from Redis cache."""
    raw = await redis_client.get('briefing:latest')
    if not raw:
        return {'status': 'no_briefing', 'message': 'No briefing generated yet. POST /briefing/trigger to generate one now.'}
    return json.loads(raw)

@app.post('/briefing/trigger', status_code=202)
async def briefing_trigger(background_tasks: BackgroundTasks):
    """Manually fires the Morning Alpha Briefing pipeline right now (for testing)."""
    background_tasks.add_task(
        asyncio.get_event_loop().run_in_executor, None, _run_briefing
    )
    return {'status': 'queued', 'message': 'Morning Alpha Briefing generating — check /briefing/latest in ~20 seconds.'}


@app.websocket('/ws/pulse')
async def ws_pulse(websocket: WebSocket):
    """
    Live WebSocket endpoint for the dashboard.
    Sends the last 20 cached events on connect, then streams new alerts as they arrive.
    """
    await websocket.accept()
    ws_clients.append(websocket)
    log.info(f'Dashboard client connected. Total: {len(ws_clients)}')

    try:
        # Send cache on connect
        recent = await redis_client.zrevrange(CACHE_KEY, 0, 19)
        for r in reversed(recent):
            await websocket.send_text(r)

        # Stay open — new alerts are pushed by run_pipeline()
        while True:
            data = await websocket.receive_text()
            # Client can send 'ping' to keep alive
            if data == 'ping':
                await websocket.send_text('pong')
    except WebSocketDisconnect:
        ws_clients.remove(websocket)
        log.info(f'Dashboard client disconnected. Total: {len(ws_clients)}')

@app.post('/cron/recalculate-profiles')
async def recalculate_profiles():
    """
    Called by the 03:00 UTC cron job (see deploy/cron_jobs.yml).
    Re-computes win rates and ROI for all wallets with trades in the last 24h.
    """
    if not DATABASE_URL:
        raise HTTPException(503, 'Database not configured.')
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE wallets w
                SET
                    win_rate = COALESCE(
                        (SELECT CAST(COUNT(*) FILTER (WHERE won = TRUE) AS FLOAT) /
                         NULLIF(COUNT(*) FILTER (WHERE resolved = TRUE), 0)
                         FROM trades t WHERE t.wallet_address = w.wallet_address), 0),
                    roi_all_time = COALESCE(
                        (SELECT SUM(CASE WHEN won THEN size ELSE -usd_value END) /
                         NULLIF(SUM(usd_value), 0)
                         FROM trades t WHERE t.wallet_address = w.wallet_address
                         AND resolved = TRUE), 0)
                WHERE last_seen > NOW() - INTERVAL '24 hours';
            """)
            updated = cur.rowcount
        conn.commit()
        conn.close()
        log.info(f'Profile recalculation complete. {updated} wallets updated.')
        return {'status': 'ok', 'wallets_updated': updated}
    except Exception as e:
        log.error(f'Recalculation failed: {e}')
        raise HTTPException(500, str(e))
