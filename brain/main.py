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
try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Import our Layer 3 tools
import sys
sys.path.insert(0, '/app/tools')
from signal_engine   import build_alert
from whale_profiler  import profile_trade, init_db as init_whale_db, generate_handle
import paper_trading
from ai_summarizer   import summarize
from notifier        import deliver
from paper_trader    import follow as paper_follow, unfollow as paper_unfollow, get_portfolio as paper_portfolio
from leaderboard      import get_leaderboard
from wallet_xray      import get_xray as get_wallet_xray
from cluster_detector import check_cluster
from morning_briefing import run_briefing as _run_briefing
from email_alerts     import (
    init_db as init_email_alerts_db,
    get_rules, save_rule, delete_rule,
    check_and_fire_email_alerts,
)
from market_resolver  import (
    init_db        as init_resolver_db,
    run_resolution_pass,
)
from trojan_horse_crm import run_crm_pass as _run_crm_pass
from twitter_threader import run_daily_recap as _run_daily_recap
from twitter_monitor   import run_monitor    as _run_twitter_monitor
from twitter_news_jack import run_news_jack  as _run_twitter_news_jack
from twitter_reply_agent import run_reply_agent as _run_reply_agent
from subscriptions import (
    init_db        as init_subscriptions_db,
    is_pro, get_subscription, upsert_subscription, cancel_subscription,
    link_discord, get_discord_user_id,
)
from discord_roles import (
    grant_pro_role, revoke_pro_role, exchange_code_for_user_id,
)
from price_tracker import (
    init_db        as init_price_tracker_db,
    run_price_tracker_pass,
)
from kalshi_ear import poll_kalshi as _poll_kalshi_sync

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
STRIPE_PRICE_ID       = os.getenv('STRIPE_PRICE_ID', '')     # price_1ABC... from Stripe dashboard
CLERK_SECRET_KEY      = os.getenv('CLERK_SECRET_KEY', '')
SENTRY_DSN            = os.getenv('SENTRY_DSN', '')

os.makedirs('.tmp', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [BRAIN] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

if SENTRY_DSN and SENTRY_AVAILABLE:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[FastApiIntegration(), StarletteIntegration()],
        traces_sample_rate=0.1,
        environment=os.getenv('RAILWAY_ENVIRONMENT', 'development'),
        release='polyvision@1.0.0',
    )
    log.info('Sentry initialized.')
elif SENTRY_DSN and not SENTRY_AVAILABLE:
    log.warning('SENTRY_DSN set but sentry-sdk not installed. Run: pip install "sentry-sdk[fastapi]"')

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
            init_email_alerts_db()
            init_resolver_db()
            init_subscriptions_db()
            init_price_tracker_db()
            init_whale_followers_db()
            paper_trading.init_db()
            log.info('PostgreSQL tables initialized.')
        except Exception as e:
            log.warning(f'DB init skipped (no connection?): {e}')

    # ── Kalshi Ear async wrapper ──────────────────────────────────────────────
    async def _run_kalshi_poll():
        """Run the synchronous poll_kalshi in a thread executor so it never
        blocks the FastAPI event loop."""
        loop = asyncio.get_event_loop()
        try:
            forwarded = await loop.run_in_executor(None, _poll_kalshi_sync)
            if forwarded:
                log.info(f'[Kalshi] Scheduler: {forwarded} trade(s) forwarded.')
        except Exception as e:
            log.warning(f'[Kalshi] Scheduled poll error: {e}')

    # ── Morning Alpha Briefing Scheduler ─────────────────────────────────────
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_briefing,
        trigger=CronTrigger(hour=BRIEFING_HOUR, minute=0, timezone='America/New_York'),
        id='morning_briefing',
        name=f'Morning Alpha Briefing ({BRIEFING_HOUR:02d}:00 EST)',
        replace_existing=True,
    )
    # ── Daily Market Resolution Pass (06:00 EST, before the briefing) ──────────
    scheduler.add_job(
        run_resolution_pass,
        trigger=CronTrigger(hour=6, minute=0, timezone='America/New_York'),
        id='market_resolution',
        name='Daily Market Resolution Pass (06:00 EST)',
        replace_existing=True,
    )
    # ── Daily Price Impact Tracker (07:30 EST — checks 24h-old trades) ─────────
    scheduler.add_job(
        run_price_tracker_pass,
        trigger=CronTrigger(hour=7, minute=30, timezone='America/New_York'),
        id='price_tracker',
        name='Daily Price Impact Tracker (07:30 EST)',
        replace_existing=True,
    )
    # ── Trojan Horse Marketing CRM (Tuesdays & Thursdays at 10:00 EST) ─────────
    scheduler.add_job(
        _run_crm_pass,
        trigger=CronTrigger(day_of_week='tue,thu', hour=10, minute=0, timezone='America/New_York'),
        id='trojan_horse_crm',
        name='Trojan Horse Discord Marketing Reminder (Tues/Thu 10AM EST)',
        replace_existing=True,
    )
    # ── X (Twitter) Bots — gated by TWITTER_BOT_ENABLED env var ─────────────────
    # Set TWITTER_BOT_ENABLED=true in Railway to re-activate when suspension is lifted.
    TWITTER_BOT_ENABLED = os.getenv('TWITTER_BOT_ENABLED', 'false').lower() == 'true'
    if TWITTER_BOT_ENABLED:
        scheduler.add_job(
            _run_daily_recap,
            trigger=CronTrigger(hour=19, minute=0, timezone='America/New_York'),
            id='twitter_daily_recap',
            name='X Daily Recap Thread (19:00 EST)',
            replace_existing=True,
        )
        scheduler.add_job(
            _run_twitter_monitor,
            trigger=CronTrigger(minute='*/10'),
            id='twitter_monitor',
            name='X Reply-Value Engine (Every 10 min)',
            replace_existing=True,
        )
        scheduler.add_job(
            _run_twitter_news_jack,
            trigger=CronTrigger(minute='*/25'),
            id='twitter_news_jack',
            name='X News-Jacking Engine (Every 25 min)',
            replace_existing=True,
        )
        scheduler.add_job(
            _run_reply_agent,
            trigger=CronTrigger(minute='*/15'),
            id='twitter_reply_agent',
            name='X Mention-Reply Agent (Every 15 min)',
            replace_existing=True,
        )
    else:
        log.info('X (Twitter) bots DISABLED — TWITTER_BOT_ENABLED=false (suspended).')

    # ── Kalshi Ear — poll every 60 seconds ────────────────────────────────────
    scheduler.add_job(
        _run_kalshi_poll,
        trigger=CronTrigger(second='0'),   # fires every 60s (once per minute)
        id='kalshi_ear',
        name='Kalshi Ear Poll (every 60s)',
        replace_existing=True,
    )

    scheduler.start()
    log.info(f'Briefing scheduler started — fires daily at {BRIEFING_HOUR:02d}:00 EST.')
    log.info('Market resolution cron scheduled — fires daily at 06:00 EST.')
    log.info('Price impact tracker cron scheduled — fires daily at 07:30 EST.')
    log.info('Trojan Horse CRM cron scheduled — fires Tue/Thu at 10:00 EST.')
    if TWITTER_BOT_ENABLED:
        log.info('X (Twitter) Daily Thread scheduled — fires daily at 19:00 EST.')
        log.info('X (Twitter) Auto-Reply Engine scheduled — fires every 10 min.')
        log.info('X (Twitter) News-Jacking Engine scheduled — fires every 25 min.')
        log.info('X (Twitter) Mention-Reply Agent scheduled — fires every 15 min.')
    log.info('Kalshi Ear scheduled — polls every 60s.')

    yield
    scheduler.shutdown(wait=False)
    await redis_client.aclose()
    log.info('Redis disconnected.')

app = FastAPI(title='PolyVision Brain', version='1.0.0', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'https://polyvision.app',
        'https://www.polyvision.app',
        'https://polyvision-production.up.railway.app',
        'https://polyvision-deploy.pages.dev',
    ],
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(paper_trading.router)


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
    trader_pseudonym: str = ''
    trader_name:   str = ''
    source:        str = 'POLYMARKET'   # POLYMARKET | KALSHI — preserved through full pipeline

class WhaleFollowRequest(BaseModel):
    wallet_address:     str
    onesignal_player_id: str
    clerk_user_id:      str = ''

# ── Whale Followers ────────────────────────────────────────────────────────────
ONESIGNAL_APP_ID  = os.getenv('ONESIGNAL_APP_ID', '')
ONESIGNAL_API_KEY = os.getenv('ONESIGNAL_API_KEY', '')

def init_whale_followers_db():
    """Create whale_followers table if it doesn't exist."""
    if not DATABASE_URL:
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS whale_followers (
                        id                  SERIAL PRIMARY KEY,
                        wallet_address      TEXT NOT NULL,
                        onesignal_player_id TEXT NOT NULL,
                        clerk_user_id       TEXT DEFAULT '',
                        created_at          TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(wallet_address, onesignal_player_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_wf_wallet ON whale_followers(wallet_address);
                """)
            conn.commit()
        log.info('whale_followers table ready.')
    except Exception as e:
        log.warning(f'Could not init whale_followers table: {e}')

def get_whale_follower_player_ids(wallet_address: str) -> list:
    """Return all OneSignal player IDs following this wallet."""
    if not DATABASE_URL:
        return []
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT onesignal_player_id FROM whale_followers WHERE wallet_address = %s',
                    (wallet_address,)
                )
                return [row[0] for row in cur.fetchall()]
    except Exception as e:
        log.warning(f'Could not fetch whale followers: {e}')
        return []

def send_targeted_push(player_ids: list, title: str, body: str, url: str = 'https://polyvision.app/app'):
    """Send a OneSignal push notification to specific player IDs."""
    if not ONESIGNAL_APP_ID or not ONESIGNAL_API_KEY or not player_ids:
        return
    try:
        import requests as req_lib
        resp = req_lib.post(
            'https://onesignal.com/api/v1/notifications',
            headers={
                'Authorization': f'Basic {ONESIGNAL_API_KEY}',
                'Content-Type':  'application/json',
                'User-Agent':    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            },
            json={
                'app_id':             ONESIGNAL_APP_ID,
                'include_player_ids': player_ids,
                'headings':           {'en': title},
                'contents':           {'en': body},
                'url':                url,
                'chrome_web_icon':    'https://polyvision.app/assets/icon-192.png',
                'firefox_icon':       'https://polyvision.app/assets/icon-192.png',
            },
            timeout=8,
        )
        log.info(f'OneSignal push sent to {len(player_ids)} follower(s): {resp.status_code}')
    except Exception as e:
        log.warning(f'OneSignal targeted push failed: {e}')


# ── Pipeline Helpers ──────────────────────────────────────────────────────────
ADJECTIVES = ['Strategist','Oracle','Tactician','Visionary','Analyst',
              'Architect','Sentinel','Navigator','Pioneer','Scholar']
REGIONS    = ['Oregon','the Midwest','Texas','New York','California',
              'Chicago','Seattle','Miami','Boston','Denver']

def _quick_handle(wallet: str) -> str:
    h = int(hashlib.sha256(wallet.encode()).hexdigest(), 16)
    return f"The {ADJECTIVES[h % len(ADJECTIVES)]} of {REGIONS[(h >> 8) % len(REGIONS)]}"


def _compute_win_rate_from_xray(wallet: str) -> float | None:
    """
    Fetches the wallet X-Ray (Redis-cached, 60s TTL) and returns the
    profitable-trade win rate: closed positions with net_pnl > 0 ÷ total closed.
    Returns None silently on any error so the pipeline never stalls.
    """
    try:
        profile = get_wallet_xray(wallet)
        positions = profile.get('positions', [])
        closed = [p for p in positions if not p.get('is_open', True)]
        if not closed:
            return None
        wins = sum(1 for p in closed if (p.get('net_pnl') or 0) > 0)
        return round(wins / len(closed), 4)
    except Exception as e:
        log.debug(f"Win rate fetch skipped for {wallet[:10]}…: {e}")
        return None

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

        # 1a. Real Trader Handle — three-tier resolution (Polymarket only)
        #     Tier 1: trader_pseudonym / trader_name already fetched by The Ear
        #             from the Polymarket REST API at ingest time (most reliable).
        #     Tier 2: xray profile name (fetched on-demand, Redis-cached 60s).
        #     Tier 3: synthetic hash handle (already set by signal_engine above).
        #     Kalshi trades use a different identity system — skip entirely.
        wallet        = event_dict.get('maker_address', '')
        is_polymarket = event_dict.get('source', 'POLYMARKET').upper() == 'POLYMARKET'

        if is_polymarket:
            # Tier 1 — event already contains the Polymarket public name
            ear_name = (
                event_dict.get('trader_name', '').strip()
                or event_dict.get('trader_pseudonym', '').strip()
            )
            if ear_name:
                old_handle = alert.get('trader_handle', '')
                alert['trader_handle'] = ear_name
                log.info(f"🏷  Handle (Ear): '{old_handle}' → '{ear_name}'")

            # Tier 2 — xray enrichment (win rate + name if Tier 1 was empty)
            if wallet and alert.get('wallet_win_rate') is None:
                try:
                    xray_profile = await asyncio.get_event_loop().run_in_executor(
                        None, get_wallet_xray, wallet
                    )
                    # Win rate
                    positions = xray_profile.get('positions', [])
                    closed    = [p for p in positions if not p.get('is_open', True)]
                    if closed:
                        wins       = sum(1 for p in closed if (p.get('net_pnl') or 0) > 0)
                        fetched_wr = round(wins / len(closed), 4)
                        alert['wallet_win_rate']        = fetched_wr
                        alert['copy_trade_recommended'] = fetched_wr >= float(
                            os.getenv('COPY_TRADE_MIN_WIN_RATE', '0.60')
                        )
                        log.info(f"🎯 Win rate for {wallet[:10]}…: {fetched_wr:.1%}")

                    # Name — only patch if Tier 1 was empty
                    if not ear_name:
                        xray_name = (
                            xray_profile.get('name', '').strip()
                            or xray_profile.get('username', '').strip()
                            or xray_profile.get('pseudonym', '').strip()
                        )
                        if xray_name:
                            old_handle = alert.get('trader_handle', '')
                            alert['trader_handle'] = xray_name
                            log.info(f"🏷  Handle (xray): '{old_handle}' → '{xray_name}'")

                except Exception as _xray_err:
                    log.debug(f"xray enrichment skipped for {wallet[:10]}…: {_xray_err}")

        # 1c. Absolute safety guard: if handle STILL looks like a raw wallet
        #     address after all 3 tiers, replace it with the synthetic persona.
        #     This catches: poisoned DB rows, fully anonymous wallets where
        #     both Polymarket API and xray return no public name.
        final_handle = alert.get('trader_handle', '')
        if final_handle.startswith('0x') and len(final_handle) > 10:
            safe_handle = _quick_handle(wallet or final_handle)
            log.info(f"🛡  Handle guard: raw address detected → replacing with '{safe_handle}'")
            alert['trader_handle'] = safe_handle

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
        #    CRITICAL: wrap in its own try/except — a transient OpenAI connection
        #    drop (APIConnectionError / RemoteProtocolError) must NEVER kill the
        #    rest of the pipeline (Redis cache, Discord, Telegram, video generation).
        try:
            alert = await asyncio.get_event_loop().run_in_executor(None, summarize, alert)
        except Exception as _sum_err:
            log.warning(f"AI summarizer failed (non-fatal — using fallback): {_sum_err}")
            from ai_summarizer import fallback_summary
            alert["ai_summary"] = fallback_summary(alert)

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

        # 6. Email alerts — check all stored user rules server-side
        await asyncio.get_event_loop().run_in_executor(
            None, check_and_fire_email_alerts, alert
        )

        # 6.5 Targeted OneSignal push to whale followers
        #     Only fires for wallets where at least one user clicked "Follow Whale"
        wallet = event_dict.get('maker_address', '')
        if wallet:
            follower_ids = await asyncio.get_event_loop().run_in_executor(
                None, get_whale_follower_player_ids, wallet
            )
            if follower_ids:
                handle = alert.get('trader_handle', 'A whale')
                usd    = alert.get('usd_value', 0)
                market = alert.get('market_title', 'Unknown Market')[:60]
                outcome = alert.get('outcome', '')
                push_title = f"🐋 {handle} just traded!"
                push_body  = f"{outcome} ${usd:,.0f} on \"{market}\""
                await asyncio.get_event_loop().run_in_executor(
                    None, send_targeted_push, follower_ids, push_title, push_body
                )

        # 7. Push/Discord/Telegram notify (broadcast all alerts)
        await asyncio.get_event_loop().run_in_executor(
            None, deliver, alert, False
        )

    except Exception as e:
        log.error(f'Pipeline error: {e}', exc_info=True)

# ── Endpoints ─────────────────────────────────────────────────────────────────


# ── Whale Follow / Unfollow ───────────────────────────────────────────────────
@app.post('/whale-follow', status_code=200)
async def whale_follow(req: WhaleFollowRequest):
    """Register a user (by OneSignal player ID) as a follower of a specific whale wallet."""
    if not DATABASE_URL:
        return {'status': 'ok', 'note': 'DB not available — push will not fire server-side'}
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO whale_followers (wallet_address, onesignal_player_id, clerk_user_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (wallet_address, onesignal_player_id) DO NOTHING;
                """, (req.wallet_address, req.onesignal_player_id, req.clerk_user_id))
            conn.commit()
        log.info(f'Whale follow: {req.wallet_address[:12]} ← {req.onesignal_player_id[:12]}')
        return {'status': 'following'}
    except Exception as e:
        log.error(f'whale_follow error: {e}')
        raise HTTPException(status_code=500, detail='DB error')

@app.delete('/whale-follow', status_code=200)
async def whale_unfollow(req: WhaleFollowRequest):
    """Remove a user's follow subscription for a specific whale wallet."""
    if not DATABASE_URL:
        return {'status': 'ok'}
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM whale_followers
                    WHERE wallet_address = %s AND onesignal_player_id = %s;
                """, (req.wallet_address, req.onesignal_player_id))
            conn.commit()
        return {'status': 'unfollowed'}
    except Exception as e:
        log.error(f'whale_unfollow error: {e}')
        raise HTTPException(status_code=500, detail='DB error')

@app.get('/markets')
async def proxy_markets(limit: int = 60, order: str = 'volume24hr', ascending: bool = False):
    """
    Proxy Polymarket top markets — browser CORS blocks direct calls.
    Uses Redis cache (60s TTL) so N concurrent users → 1 upstream request,
    preventing IP bans. Uses httpx.AsyncClient to avoid blocking the event loop.
    """
    cache_key = f'cache:markets:{limit}:{order}:{ascending}'

    # Fast path — return cached response if available
    cached = await redis_client.get(cache_key)
    if cached:
        return JSONResponse(content=json.loads(cached))

    url = (
        f'https://gamma-api.polymarket.com/markets'
        f'?limit={limit}&order={order}&ascending={str(ascending).lower()}&active=true'
    )
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }
        # Use async client — never blocks the event loop
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=12.0)
            resp.raise_for_status()
            data = resp.json()

        # Cache for 60 seconds — protects against IP bans under concurrent load
        await redis_client.setex(cache_key, 60, json.dumps(data))
        return JSONResponse(content=data)

    except httpx.HTTPStatusError as e:
        log.warning(f'Markets proxy HTTP error: {e.response.status_code}')
        raise HTTPException(status_code=502, detail='Polymarket API returned an error.')
    except Exception as e:
        log.warning(f'Markets proxy error: {e}')
        raise HTTPException(status_code=502, detail='Could not fetch markets from Polymarket API.')

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


# ── Alert Rules Endpoints ──────────────────────────────────────────────────────

class AlertRuleRequest(BaseModel):
    id:       str
    email:    str
    min_size: float = 10000
    side:     str   = 'both'
    keyword:  str   = ''
    wallet:   str   = ''

async def _clerk_user_id(request: Request) -> Optional[str]:
    """Extract Clerk user_id from Authorization: Bearer <session_token> header."""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth[7:]
    if not CLERK_SECRET_KEY:
        # fallback: treat token as user_id directly (for testing)
        return token or None
    try:
        resp = httpx.get(
            'https://api.clerk.com/v1/sessions/' + token,
            headers={'Authorization': f'Bearer {CLERK_SECRET_KEY}'},
            timeout=5,
        )
        data = resp.json()
        return data.get('user_id') or data.get('id')
    except Exception:
        return None

@app.get('/alerts/rules')
async def get_alert_rules(request: Request):
    """Return all saved alert rules for the authenticated user."""
    user_id = await _clerk_user_id(request)
    if not user_id:
        raise HTTPException(401, 'Unauthorized')
    rules = await asyncio.get_event_loop().run_in_executor(
        None, get_rules, user_id
    )
    return {'rules': rules}

@app.post('/alerts/rules', status_code=201)
async def create_alert_rule(req: AlertRuleRequest, request: Request):
    """Save a new alert rule for the authenticated user."""
    user_id = await _clerk_user_id(request)
    if not user_id:
        raise HTTPException(401, 'Unauthorized')
    rule = await asyncio.get_event_loop().run_in_executor(
        None, save_rule, {
            'id':       req.id,
            'user_id':  user_id,
            'email':    req.email,
            'min_size': req.min_size,
            'side':     req.side,
            'keyword':  req.keyword,
            'wallet':   req.wallet,
        }
    )
    return {'status': 'created', 'rule': rule}

@app.delete('/alerts/rules/{rule_id}', status_code=200)
async def remove_alert_rule(rule_id: str, request: Request):
    """Delete an alert rule by ID (only the owning user can delete)."""
    user_id = await _clerk_user_id(request)
    if not user_id:
        raise HTTPException(401, 'Unauthorized')
    deleted = await asyncio.get_event_loop().run_in_executor(
        None, delete_rule, rule_id, user_id
    )
    if not deleted:
        raise HTTPException(404, 'Rule not found or not owned by this user.')
    return {'status': 'deleted', 'rule_id': rule_id}




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


@app.get('/stats/public')
async def public_stats():
    """
    Live aggregate stats for the landing page hero section.
    Cached in Redis for 5 min. Safe to call directly from the browser.
    Falls back gracefully if DB is unavailable.
    """
    STATS_CACHE_KEY = 'cache:public_stats'
    cached = await redis_client.get(STATS_CACHE_KEY)
    if cached:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(content=json.loads(cached))
    stats = {
        'tracked_volume_usd': 0,
        'unique_wallets':     0,
        'markets_tracked':    0,
    }
    if DATABASE_URL:
        try:
            with psycopg2.connect(DATABASE_URL, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT COALESCE(SUM(total_volume_usd),0), COUNT(*) FROM wallets;')
                    row = cur.fetchone()
                    stats['tracked_volume_usd'] = float(row[0] or 0)
                    stats['unique_wallets']      = int(row[1] or 0)
                    cur.execute(
                        "SELECT COUNT(DISTINCT market_id) FROM trades "
                        "WHERE created_at > NOW() - INTERVAL '30 days';"
                    )
                    stats['markets_tracked'] = int(cur.fetchone()[0] or 0)
        except Exception as e:
            log.warning(f'public_stats DB error: {e}')
    from fastapi.responses import JSONResponse as _JSONResponse
    await redis_client.setex(STATS_CACHE_KEY, 300, json.dumps(stats))
    return _JSONResponse(content=stats)


@app.post('/email/subscribe', status_code=200)
async def email_subscribe(request: Request):
    """Capture pre-auth landing page email leads via Resend audience list."""
    try:
        body = await request.json()
    except Exception:
        return {'status': 'invalid'}
    email = (body.get('email') or '').strip().lower()
    if not email or '@' not in email:
        return {'status': 'invalid'}
    RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
    if not RESEND_API_KEY:
        log.warning('email_subscribe: RESEND_API_KEY not set — lead dropped.')
        return {'status': 'ok'}
    
    # Optional: if you have an audience ID, we pass it in the payload. Otherwise it goes to Global Contacts.
    RESEND_AUDIENCE_ID = os.getenv('RESEND_AUDIENCE_ID', '')
    payload = {'email': email, 'unsubscribed': False}
    if RESEND_AUDIENCE_ID:
        payload['audience_id'] = RESEND_AUDIENCE_ID

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                'https://api.resend.com/contacts',
                headers={
                    'Authorization': f'Bearer {RESEND_API_KEY}',
                    'Content-Type':  'application/json',
                },
                json=payload,
            )
        log.info(f'[email_subscribe] Captured: {email}')
    except Exception as e:
        log.warning(f'[email_subscribe] Resend error: {e}')
    return {'status': 'ok'}


@app.get('/twitter/test')
async def twitter_test(dry_run: bool = True):
    """
    Diagnostic endpoint — fires a test tweet (or dry-run) and returns credential + result info.
    GET /twitter/test          → dry-run, no real tweet
    GET /twitter/test?dry_run=false → fires a real tweet to @PolyVisionApp
    """
    try:
        from twitter_poster import (
            TWITTER_API_KEY, TWITTER_API_KEY_SECRET,
            TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET,
            TWITTER_MIN_SIZE, TWITTER_KALSHI_MIN_SIZE,
            maybe_tweet, TEST_PAYLOAD_KALSHI,
        )
        creds = {
            'TWITTER_API_KEY':             'SET' if TWITTER_API_KEY             else 'MISSING',
            'TWITTER_API_KEY_SECRET':      'SET' if TWITTER_API_KEY_SECRET      else 'MISSING',
            'TWITTER_ACCESS_TOKEN':        'SET' if TWITTER_ACCESS_TOKEN        else 'MISSING',
            'TWITTER_ACCESS_TOKEN_SECRET': 'SET' if TWITTER_ACCESS_TOKEN_SECRET else 'MISSING',
            'TWITTER_MIN_SIZE':            TWITTER_MIN_SIZE,
            'TWITTER_KALSHI_MIN_SIZE':     TWITTER_KALSHI_MIN_SIZE,
        }
        result = maybe_tweet(TEST_PAYLOAD_KALSHI, dry_run=dry_run)
        return {'credentials': creds, 'result': result}
    except Exception as e:
        log.error(f'[twitter/test] Error: {e}', exc_info=True)
        return {'error': str(e)}


@app.post('/webhooks/stripe')
async def stripe_webhook_legacy(request: Request):
    """Legacy alias — forwards to the canonical /stripe/webhook handler."""
    return await stripe_webhook_canonical(request)


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
    On connect: sends last 50 cached events as a single history packet so
    the frontend can seed the feed without triggering live alerts.
    Then streams new alerts in real time.
    """
    await websocket.accept()
    ws_clients.append(websocket)
    log.info(f'Dashboard client connected. Total: {len(ws_clients)}')

    try:
        # ── Burst historical cache to new client (newest 50, sent oldest-first) ──
        recent = await redis_client.zrevrange(CACHE_KEY, 0, 49)
        if recent:
            history_events = []
            for raw in reversed(recent):   # oldest → newest so feed order is correct
                try:
                    history_events.append(json.loads(raw))
                except Exception:
                    pass
            if history_events:
                await websocket.send_text(json.dumps({
                    'type':   'history',
                    'events': history_events,
                }))
                log.info(f'Burst {len(history_events)} historical events to new client.')

        # ── Stay open — new alerts pushed by run_pipeline() ───────────────────
        while True:
            data = await websocket.receive_text()
            if data == 'ping':
                await websocket.send_text('pong')

    except WebSocketDisconnect:
        ws_clients.remove(websocket)
        log.info(f'Dashboard client disconnected. Total: {len(ws_clients)}')



# ── Video RL Feedback Endpoints ───────────────────────────────────────────────

@app.get('/video/feedback')
async def video_feedback_form(trade_id: str = ''):
    """Serve a browser-friendly HTML form to log TikTok/Reels engagement metrics."""
    from fastapi.responses import HTMLResponse
    html = f"""
    <!DOCTYPE html><html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>PolyVision — Log Video Performance</title>
      <style>
        body {{ font-family: -apple-system, sans-serif; background: #0d1117; color: #e6edf3;
                display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px;
                 padding: 32px; width: 100%; max-width: 420px; }}
        h2 {{ margin: 0 0 8px; color: #10B981; }}
        p  {{ margin: 0 0 24px; color: #8b949e; font-size: 14px; }}
        label {{ display: block; font-size: 13px; color: #8b949e; margin-bottom: 6px; }}
        input {{ width: 100%; box-sizing: border-box; background: #0d1117; border: 1px solid #30363d;
                 border-radius: 8px; color: #e6edf3; padding: 10px 14px; font-size: 16px; margin-bottom: 16px; }}
        button {{ width: 100%; background: #10B981; color: #000; border: none; border-radius: 8px;
                  padding: 12px; font-size: 16px; font-weight: 700; cursor: pointer; }}
        .success {{ display: none; color: #10B981; text-align: center; margin-top: 16px; font-weight: 600; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h2>📊 Log Video Performance</h2>
        <p>Enter the views and likes from your TikTok/Reels post to feed the RL system.</p>
        <form id="f">
          <label>Trade ID</label>
          <input name="trade_id" value="{trade_id}" placeholder="trade_id" required>
          <label>Total Views</label>
          <input name="views" type="number" min="0" placeholder="e.g. 4200" required>
          <label>Total Likes</label>
          <input name="likes" type="number" min="0" placeholder="e.g. 180" required>
          <button type="submit">Submit →</button>
        </form>
        <div class="success" id="ok">✅ Logged! RL loop updated.</div>
      </div>
      <script>
        document.getElementById('f').addEventListener('submit', async e => {{
          e.preventDefault();
          const fd = new FormData(e.target);
          const r = await fetch('/video/feedback', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
              trade_id: fd.get('trade_id'),
              views: parseInt(fd.get('views')),
              likes: parseInt(fd.get('likes')),
            }})
          }});
          if (r.ok) {{ document.getElementById('ok').style.display = 'block'; e.target.style.display = 'none'; }}
        }});
      </script>
    </body></html>
    """
    return HTMLResponse(html)


class VideoFeedbackRequest(BaseModel):
    trade_id: str
    views:    int
    likes:    int = 0


@app.post('/video/feedback')
async def submit_video_feedback(body: VideoFeedbackRequest):
    """Write TikTok/Reels engagement metrics back into video_history for RL consumption."""
    if not DATABASE_URL:
        raise HTTPException(503, 'DATABASE_URL not configured.')
    try:
        import psycopg2
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE video_history
                       SET impressions = %s,
                           upvotes     = %s
                       WHERE trade_id = %s""",
                    (body.views, body.likes, body.trade_id)
                )
                updated = cur.rowcount
            conn.commit()
        if updated == 0:
            raise HTTPException(404, f'No video_history row found for trade_id={body.trade_id}')
        log.info(f'[RL Feedback] trade_id={body.trade_id} | views={body.views:,} | likes={body.likes:,}')
        return {'success': True, 'trade_id': body.trade_id, 'views': body.views, 'likes': body.likes}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f'[RL Feedback] DB write failed: {e}')
        raise HTTPException(500, 'Failed to write feedback to database.')


# ── Stripe Checkout & Subscription Management ────────────────────────────────


class CheckoutRequest(BaseModel):
    clerk_user_id: str
    email:         str
    success_url:   str = 'https://polyvision.app/dashboard/?upgrade=success'
    cancel_url:    str = 'https://polyvision.app/dashboard/?upgrade=cancelled'

@app.post('/checkout/create-session')
async def create_checkout_session(body: CheckoutRequest):
    """Create a Stripe Checkout Session for PolyVision PRO."""
    if not STRIPE_API_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(503, 'Stripe not configured — set STRIPE_API_KEY and STRIPE_PRICE_ID.')
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='subscription',
            line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
            customer_email=body.email,
            client_reference_id=body.clerk_user_id,   # used in webhook to link sub → user
            success_url=body.success_url + '&session_id={CHECKOUT_SESSION_ID}',
            cancel_url=body.cancel_url,
            metadata={'clerk_user_id': body.clerk_user_id},
        )
        return {'url': session.url, 'session_id': session.id}
    except Exception as e:
        raise HTTPException(400, str(e))


class PortalRequest(BaseModel):
    clerk_user_id: str
    return_url:    str = 'https://polyvision.app/dashboard/'

@app.post('/billing/portal')
async def billing_portal(body: PortalRequest):
    """
    Create a Stripe Customer Portal session so users can manage or cancel
    their subscription, update payment methods, and view invoices.
    Returns a {url} the frontend should redirect to.
    """
    if not STRIPE_API_KEY:
        raise HTTPException(503, 'Stripe not configured.')
    sub = get_subscription(body.clerk_user_id)
    if not sub or not sub.get('stripe_customer_id'):
        raise HTTPException(404, 'No active subscription found for this user.')
    try:
        session = stripe.billing_portal.Session.create(
            customer=sub['stripe_customer_id'],
            return_url=body.return_url,
        )
        return {'url': session.url}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post('/stripe/webhook')
async def stripe_webhook_canonical(request: Request):
    """Canonical Stripe webhook handler — handles full subscription lifecycle.

    Signature verification:
    - If STRIPE_WEBHOOK_SECRET is set: validates signature (rejects if bad).
    - If STRIPE_WEBHOOK_SECRET is NOT set: logs a warning but still processes
      the event. This allows test-mode setup before the secret is configured
      in Railway. Set the secret ASAP in production to prevent spoofing.
    """
    payload = await request.body()
    sig     = request.headers.get('stripe-signature', '')

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        except ValueError:
            log.error('Stripe webhook: invalid payload (not JSON)')
            raise HTTPException(400, 'Invalid payload')
        except stripe.error.SignatureVerificationError as e:
            log.error(f'Stripe webhook: bad signature — is STRIPE_WEBHOOK_SECRET correct? {e}')
            raise HTTPException(400, 'Invalid Stripe signature')
    else:
        # No secret configured — parse raw JSON but do NOT verify signature.
        # IMPORTANT: set STRIPE_WEBHOOK_SECRET in Railway to enable verification.
        log.warning('STRIPE_WEBHOOK_SECRET not set — processing webhook WITHOUT signature check. Set this in Railway!')
        try:
            event = json.loads(payload)
        except Exception:
            raise HTTPException(400, 'Invalid JSON payload')

    etype = event.get('type') if isinstance(event, dict) else event['type']
    data  = (event.get('data', {}).get('object', {})
             if isinstance(event, dict)
             else event['data']['object'])

    if etype == 'checkout.session.completed':
        clerk_user_id = data.get('metadata', {}).get('clerk_user_id') or data.get('client_reference_id', '')
        if clerk_user_id:
            try:
                sub = stripe.Subscription.retrieve(data['subscription'])
                period_end = sub.get('current_period_end')
                upsert_subscription(
                    clerk_user_id=clerk_user_id,
                    stripe_customer_id=data['customer'],
                    stripe_sub_id=data['subscription'],
                    status='active',
                    period_end_ts=period_end,
                )
                log.info(f'PRO activated for clerk user: {clerk_user_id}')
                # Also update Clerk public metadata so client-side isPro() reads correctly
                if CLERK_SECRET_KEY:
                    async with httpx.AsyncClient() as client:
                        res = await client.patch(
                            f'https://api.clerk.com/v1/users/{clerk_user_id}/metadata',
                            headers={'Authorization': f'Bearer {CLERK_SECRET_KEY}'},
                            json={'public_metadata': {'tier': 'PRO'}},
                        )
                        if res.status_code != 200:
                            log.error(f'Clerk metadata update failed for {clerk_user_id}: {res.text}')
                # Grant Discord PRO role if user has linked their account
                discord_uid = get_discord_user_id(clerk_user_id)
                if discord_uid:
                    grant_pro_role(discord_uid)
            except Exception as e:
                log.error(f'Failed to activate PRO for {clerk_user_id}: {e}')

    elif etype in ('customer.subscription.deleted', 'customer.subscription.updated'):
        sub    = data
        status = sub['status']   # 'active','past_due','cancelled','unpaid'
        clerk_uid = sub.get('metadata', {}).get('clerk_user_id', '')
        upsert_subscription(
            clerk_user_id=clerk_uid,
            stripe_customer_id=sub['customer'],
            stripe_sub_id=sub['id'],
            status=status,
            period_end_ts=sub.get('current_period_end'),
        )
        if status != 'active' and clerk_uid:
            discord_uid = get_discord_user_id(clerk_uid)
            if discord_uid:
                revoke_pro_role(discord_uid)

    elif etype == 'invoice.payment_failed':
        cancel_subscription(data['customer'])
        log.warning(f'Payment failed — downgraded customer: {data["customer"]}')
        try:
            conn = __import__('psycopg2').connect(DATABASE_URL, connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT clerk_user_id, discord_user_id FROM subscriptions WHERE stripe_customer_id = %s LIMIT 1',
                    (data['customer'],)
                )
                row = cur.fetchone()
            conn.close()
            if row and row[1]:
                revoke_pro_role(row[1])
        except Exception as e:
            log.warning(f'Discord revoke on payment failure: {e}')

    return {'received': True}


# ── Discord OAuth Routes ───────────────────────────────────────────────────────
DISCORD_CLIENT_ID     = os.getenv('DISCORD_CLIENT_ID', '')
DISCORD_CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET', '')
DISCORD_INVITE_URL    = os.getenv('DISCORD_INVITE_URL', '')   # public invite link to the server


@app.get('/discord/oauth/start')
async def discord_oauth_start(clerk_user_id: str):
    """
    Redirect the user to Discord's OAuth2 authorization page.
    clerk_user_id is passed as 'state' so we can match it in the callback.
    """
    if not DISCORD_CLIENT_ID:
        raise HTTPException(503, 'Discord OAuth not configured (DISCORD_CLIENT_ID missing)')
    brain_url     = os.getenv('BRAIN_URL', 'https://polyvision-production.up.railway.app')
    redirect_uri  = f'{brain_url}/discord/oauth/callback'
    auth_url = (
        f'https://discord.com/api/oauth2/authorize'
        f'?client_id={DISCORD_CLIENT_ID}'
        f'&redirect_uri={redirect_uri}'
        f'&response_type=code'
        f'&scope=identify'
        f'&state={clerk_user_id}'
    )
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=auth_url)


@app.get('/discord/oauth/callback')
async def discord_oauth_callback(code: str = '', state: str = '', error: str = ''):
    """
    Discord redirects here after the user authorises.
    Exchanges the code for a user ID, stores it, and grants the PRO role if applicable.
    """
    from fastapi.responses import HTMLResponse

    if error or not code:
        return HTMLResponse('<script>window.close();</script><p>Discord link cancelled.</p>', status_code=200)

    clerk_user_id = state   # passed through as OAuth state
    brain_url     = os.getenv('BRAIN_URL', 'https://polyvision-production.up.railway.app')
    redirect_uri  = f'{brain_url}/discord/oauth/callback'

    discord_user_id, discord_error = exchange_code_for_user_id(code, redirect_uri)
    if not discord_user_id:
        return HTMLResponse(
            f'<html><body style="font-family:monospace;padding:20px;background:#1a1a2e;color:#ff6b6b">'
            f'<h3>Discord Link Failed</h3>'
            f'<p><strong>Error:</strong> {discord_error}</p>'
            f'<p style="color:#888;font-size:12px">redirect_uri sent: {redirect_uri}</p>'
            f'<p style="color:#888;font-size:12px">client_id used: {os.getenv("DISCORD_CLIENT_ID","(not set)")}</p>'
            f'</body></html>',
            status_code=500
        )

    # Store the link
    link_discord(clerk_user_id, discord_user_id)

    # If user is already PRO, grant the role immediately
    if is_pro(clerk_user_id):
        grant_pro_role(discord_user_id)

    # Close the popup and notify the parent window
    invite_html = f'<p>Join the server: <a href="{DISCORD_INVITE_URL}" target="_blank">Click here</a></p>' if DISCORD_INVITE_URL else ''
    return HTMLResponse(f"""
    <html><head><title>Discord Linked</title></head>
    <body style="font-family:sans-serif;text-align:center;padding:40px;background:#1a1a2e;color:#fff">
      <h2>\u2705 Discord Linked Successfully!</h2>
      <p>Your PolyVision PRO Discord access is now active.</p>
      {invite_html}
      <p style="margin-top:24px;font-size:12px;color:#666">You can close this window.</p>
      <script>
        if (window.opener) {{
          window.opener.postMessage({{ type: 'discord_linked', discord_user_id: '{discord_user_id}' }}, '*');
          setTimeout(() => window.close(), 2000);
        }}
      </script>
    </body></html>
    """, status_code=200)


@app.get('/subscription/status')
async def subscription_status(clerk_user_id: str):
    """Return PRO status for a Clerk user — called by the dashboard on load."""
    if not clerk_user_id:
        raise HTTPException(400, 'clerk_user_id required')
    return get_subscription(clerk_user_id)


# ── Profile Recalculation Cron ───────────────────────────────────────────────
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

# ── Test Video Factory Endpoint ──────────────────────────────────────────────
@app.get('/test-video-factory')
async def trigger_remote_video_test():
    """Trigger the local video factory script utilizing production infrastructure"""
    import subprocess
    import sys
    try:
        subprocess.run([sys.executable, 'tools/video_factory.py', '--test'], check=True)
        return {"status": "success", "message": "Email dispatched to production inbox."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# -- Top of Funnel Marketing API ---------------------------------------------
@app.get('/api/analyze-wallet')
async def analyze_wallet(query: str, platform: str = "polymarket"):
    """
    Public endpoint for the Whale Wallet Analyzer.
    No auth required. Used to generate viral Share Cards.

    Resolution strategy (4 steps):
    1. Redis cache scan - last 500 alerts for matching trader_handle
    2. PostgreSQL wallets table - for tracked whale addresses
    3. Polymarket Data-API server-side username resolution
       Uses: data-api.polymarket.com/v1/leaderboard?userName=<query>
       Resolves ANY registered Polymarket username to a wallet. No auth needed.
    4. X-Ray build - fetches full historical stats via wallet_xray
    """
    if not DATABASE_URL:
        raise HTTPException(503, 'Database not configured.')

    q = query.strip()
    if not q:
        raise HTTPException(400, 'Query cannot be empty.')
    is_kalshi = platform.lower() == 'kalshi'


    try:
        # Step 1: Redis scan for recent whale match
        resolved_wallet = None
        resolved_handle = None

        raw_events = await redis_client.zrevrange(CACHE_KEY, 0, 499)
        events = []
        for raw in raw_events:
            try:
                events.append(json.loads(raw))
            except Exception:
                pass

        for ev in events:
            ev_handle = ev.get('trader_handle', '')
            ev_wallet = ev.get('wallet_address', '')
            if (q.lower() == ev_handle.lower() or
                    q.lower() in ev_handle.lower() or
                    q.lower() == ev_wallet.lower()):
                resolved_wallet = ev_wallet
                resolved_handle = ev_handle
                break

        # Step 2: PostgreSQL lookup (whale tracker DB)
        db_user = None
        with psycopg2.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if resolved_wallet:
                    cur.execute("""
                        SELECT wallet_address, handle, win_rate, roi_all_time,
                               total_trades, total_volume_usd
                        FROM wallets WHERE wallet_address = %s LIMIT 1;
                    """, (resolved_wallet,))
                elif is_kalshi:
                    # Kalshi: search by handle with source filter
                    cur.execute("""
                        SELECT wallet_address, handle, win_rate, roi_all_time,
                               total_trades, total_volume_usd
                        FROM wallets
                        WHERE source = 'KALSHI' AND handle ILIKE %s
                        ORDER BY last_seen DESC LIMIT 1;
                    """, ("%" + q + "%",))
                else:
                    cur.execute("""
                        SELECT wallet_address, handle, win_rate, roi_all_time,
                               total_trades, total_volume_usd
                        FROM wallets
                        WHERE wallet_address ILIKE %s OR handle ILIKE %s
                        ORDER BY last_seen DESC LIMIT 1;
                    """, (q, "%" + q + "%"))
                db_user = cur.fetchone()

        # Step 3: Server-side Polymarket username resolution
        # Proven: data-api.polymarket.com/v1/leaderboard?userName=<name>
        # Returns proxyWallet, vol, pnl for ANY Polymarket user. No auth needed.
        pm_wallet = None
        pm_handle = None
        pm_vol = 0.0
        pm_pnl = 0.0

        target_wallet = (
            resolved_wallet or
            (q if q.startswith('0x') and len(q) >= 40 else None)
        )

        if not target_wallet and not is_kalshi:
            # Username query - resolve server-side via Polymarket leaderboard API
            # NOTE: Kalshi is a centralized exchange — no public username lookup API exists
            try:
                pm_url = (
                    "https://data-api.polymarket.com/v1/leaderboard"
                    "?userName=" + q + "&timePeriod=ALL&orderBy=PNL&limit=1"
                )
                async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                    pm_resp = await client.get(pm_url, headers={"User-Agent": "Mozilla/5.0"})
                if pm_resp.status_code == 200:
                    pm_data = pm_resp.json()
                    if pm_data and isinstance(pm_data, list) and len(pm_data) > 0:
                        row = pm_data[0]
                        pm_wallet = row.get('proxyWallet', '')
                        pm_handle = row.get('userName', q)
                        pm_vol = float(row.get('vol') or 0)
                        pm_pnl = float(row.get('pnl') or 0)
                        if pm_wallet:
                            target_wallet = pm_wallet
                            log.info("[Analyzer] Resolved '{}' -> {} via Polymarket API".format(q, pm_wallet))
            except Exception as e:
                log.warning("[Analyzer] PM username resolution failed for '{}': {}".format(q, e))

        # Step 4: X-Ray - fetch full activity/position history for resolved wallet
        # X-Ray uses data-api.polymarket.com — only relevant for Polymarket wallets
        if target_wallet and not is_kalshi:
            try:
                import sys as _sys, os as _os
                tools_path = _os.path.join(_os.path.dirname(__file__), 'tools')
                if tools_path not in _sys.path:
                    _sys.path.insert(0, tools_path)
                from wallet_xray import get_xray

                xray = get_xray(target_wallet, force_refresh=False)

                # Top winning positions from activity
                best_trades = []
                for p in xray.get('positions', []):
                    if p.get('status') == 'up' and p.get('net_pnl', 0) > 0:
                        best_trades.append({
                            'market_title': p.get('title', ''),
                            'outcome': p.get('outcome', ''),
                            'profit': p.get('net_pnl', 0),
                        })
                        if len(best_trades) >= 5:
                            break

                # Leaderboard API has the most accurate all-time vol/pnl
                xray_vol = float(xray.get('all_time_vol') or 0)
                xray_pnl = float(xray.get('all_time_pnl') or 0)
                final_vol = max(xray_vol, pm_vol)
                final_pnl = pm_pnl if (pm_vol > 0 and pm_vol >= xray_vol) else xray_pnl
                roi_all_time = (final_pnl / final_vol) if final_vol > 0 else 0.0

                display_handle = (
                    pm_handle or resolved_handle or
                    xray.get('handle') or
                    (db_user['handle'] if db_user else None) or q
                )
                db_wr = float(db_user['win_rate'] or 0) if db_user else 0.0
                final_win_rate = db_wr if db_wr > 0 else float(xray.get('win_rate') or 0)
                win_rate_source = 'empirical'  # default: real resolved trade data

                # Heuristic API Drift Fallback: If the API paginates out a whale's historical wins,
                # it mathematically returns 0%. A $16M profit with 0% win rate proves API truncation.
                # IMPORTANT: Estimated rates are flagged and NEVER drive copy_trade_recommended.
                if final_win_rate < 0.01 and final_vol > 1000:
                    if final_pnl > 0:
                        final_win_rate = 0.52 + min((final_pnl / final_vol), 0.47)
                    else:
                        final_win_rate = max(0.15, 0.48 - abs(final_pnl / final_vol))
                    win_rate_source = 'estimated'  # suppresses copy_trade_recommended

                return {
                    'wallet_address': target_wallet,
                    'handle': display_handle,
                    'source': 'POLYMARKET',
                    'win_rate': final_win_rate,
                    'win_rate_source': win_rate_source,
                    # Never recommend copy-trading when win rate is mathematically estimated
                    'copy_trade_recommended': (
                        final_win_rate >= 0.60 and win_rate_source == 'empirical'
                    ),
                    'roi_all_time': roi_all_time,
                    'total_trades': xray.get('total_markets') or len(xray.get('history', [])),
                    'total_volume': final_vol,
                    'best_trades': best_trades,
                }

            except Exception as e:
                log.error('[Analyzer] X-Ray failed for {}: {}'.format(target_wallet, e), exc_info=True)
                # Fall through to DB-only response below

        # Fallback: DB-only response for tracked whales (if X-Ray failed or no target_wallet)
        if db_user:
            wallet_address = db_user['wallet_address']
            display_handle = resolved_handle or db_user['handle']
            redis_matching = [e for e in events
                              if e.get('wallet_address', '').lower() == wallet_address.lower()]
            db_win_rate = float(db_user['win_rate'] or 0.0)
            db_roi = float(db_user['roi_all_time'] or 0.0)
            db_trades = int(db_user['total_trades'] or 0)
            db_volume = float(db_user['total_volume_usd'] or 0.0)
            if redis_matching:
                la = redis_matching[0]
                db_trades = max(db_trades, int(la.get('wallet_total_trades') or 0))
                db_volume = max(db_volume, float(la.get('wallet_total_volume') or 0))
                if float(la.get('wallet_win_rate') or 0) > 0:
                    db_win_rate = float(la['wallet_win_rate'])
                if float(la.get('wallet_roi_30d') or 0) != 0:
                    db_roi = float(la['wallet_roi_30d'])
            with psycopg2.connect(DATABASE_URL, connect_timeout=5) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT market_title, outcome, size as profit FROM trades
                        WHERE wallet_address = %s AND resolved = TRUE AND won = TRUE
                        ORDER BY size DESC LIMIT 5;
                    """, (wallet_address,))
                    best_trades = [dict(t) for t in cur.fetchall()]
            return {
                'wallet_address': wallet_address,
                'handle': display_handle,
                'source': 'POLYMARKET',
                'win_rate': db_win_rate,
                'roi_all_time': db_roi,
                'total_trades': db_trades,
                'total_volume': db_volume,
                'best_trades': best_trades,
            }

        if is_kalshi:
            raise HTTPException(404, 'Kalshi trader not found in our tracker. Kalshi does not publish public trade data — you will appear here automatically if you have placed a trade over $5,000 on Kalshi.')
        raise HTTPException(404, 'Wallet or user not found. Check the spelling or try their wallet address (0x...).')

    except HTTPException:
        raise
    except Exception as e:
        log.error('/api/analyze-wallet failed: {}'.format(e), exc_info=True)
        raise HTTPException(500, 'Internal Server Error')

