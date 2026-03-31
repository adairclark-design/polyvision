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
from whale_profiler  import profile_trade, init_db, generate_handle
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
            log.info('PostgreSQL tables initialized.')
        except Exception as e:
            log.warning(f'DB init skipped (no connection?): {e}')

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
    # ── X (Twitter) Daily Recap Thread (19:00 EST) ─────────────────────────────
    scheduler.add_job(
        _run_daily_recap,
        trigger=CronTrigger(hour=19, minute=0, timezone='America/New_York'),
        id='twitter_daily_recap',
        name='X Daily Recap Thread (19:00 EST)',
        replace_existing=True,
    )
    
    scheduler.start()
    log.info(f'Briefing scheduler started — fires daily at {BRIEFING_HOUR:02d}:00 EST.')
    log.info('Market resolution cron scheduled — fires daily at 06:00 EST.')
    log.info('Price impact tracker cron scheduled — fires daily at 07:30 EST.')
    log.info('Trojan Horse CRM cron scheduled — fires Tue/Thu at 10:00 EST.')
    log.info('X (Twitter) Daily Thread scheduled — fires daily at 19:00 EST.')

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
    """Proxy Polymarket top markets — browser CORS blocks direct calls."""
    url = (
        f'https://gamma-api.polymarket.com/markets'
        f'?limit={limit}&order={order}&ascending={str(ascending).lower()}&active=true'
    )
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'application/json',
        }
        resp = httpx.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        return JSONResponse(content=resp.json())
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

# ── Top of Funnel Marketing API ──────────────────────────────────────────────
@app.get('/api/analyze-wallet')
async def analyze_wallet(query: str):
    """
    Public endpoint for the Free Top-of-Funnel Whale Analyzer Tool.
    Requires no auth. Used to generate viral Share Cards.

    Lookup strategy (ordered by priority):
    1. Redis cache scan — search last 500 alerts for matching trader_handle (real username)
       → resolves username → wallet_address for DB lookup
    2. PostgreSQL wallets table — by exact wallet_address OR handle ILIKE
    3. If wallet found in DB, aggregate live stats from the alerts Redis cache
       (since trades table may be sparse; Redis has all recent trade data)
    """
    if not DATABASE_URL:
        raise HTTPException(503, 'Database not configured.')

    q = query.strip()
    if not q:
        raise HTTPException(400, 'Query cannot be empty.')

    try:
        # ── Step 1: Redis scan for real username match ────────────────────────
        resolved_wallet: str | None = None
        resolved_handle: str | None = None

        raw_events = await redis_client.zrevrange(CACHE_KEY, 0, 499)
        events = []
        for raw in raw_events:
            try:
                events.append(json.loads(raw))
            except Exception:
                pass

        # Search for matching trader_handle (case-insensitive) or wallet address
        for ev in events:
            ev_handle = ev.get('trader_handle', '')
            ev_wallet = ev.get('wallet_address', '')
            if (q.lower() == ev_handle.lower() or
                    q.lower() in ev_handle.lower() or
                    q.lower() == ev_wallet.lower()):
                resolved_wallet = ev_wallet
                resolved_handle = ev_handle
                break

        # ── Step 2: PostgreSQL lookup ─────────────────────────────────────────
        with psycopg2.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                # If Redis resolved a wallet address, query by exact address
                # Otherwise fall back to ILIKE search on handle or address
                if resolved_wallet:
                    cur.execute("""
                        SELECT wallet_address, handle, win_rate, roi_all_time,
                               total_trades, total_volume_usd
                        FROM wallets
                        WHERE wallet_address = %s
                        LIMIT 1;
                    """, (resolved_wallet,))
                else:
                    cur.execute("""
                        SELECT wallet_address, handle, win_rate, roi_all_time,
                               total_trades, total_volume_usd
                        FROM wallets
                        WHERE wallet_address ILIKE %s OR handle ILIKE %s
                        ORDER BY last_seen DESC
                        LIMIT 1;
                    """, (q, f"%{q}%"))

                user = cur.fetchone()

                # If we found a wallet via Redis but it's somehow not in DB yet
                # (e.g. whale_profiler hasn't run for it), build a synthetic row
                # from the Redis alert data directly
                if not user and resolved_wallet:
                    # Aggregate stats directly from Redis events
                    matching = [e for e in events
                                if e.get('wallet_address', '').lower() == resolved_wallet.lower()]
                    total_volume = sum(float(e.get('usd_value', 0)) for e in matching)

                    # Use the most recent alert's profiler data for win_rate / roi
                    latest = matching[0] if matching else {}
                    return {
                        "wallet_address": resolved_wallet,
                        "handle": resolved_handle or q,
                        "source": latest.get('source', 'POLYMARKET'),
                        "win_rate": float(latest.get('wallet_win_rate') or 0.0),
                        "roi_all_time": float(latest.get('wallet_roi_30d') or 0.0),
                        "total_trades": int(latest.get('wallet_total_trades') or len(matching)),
                        "total_volume": float(latest.get('wallet_total_volume') or total_volume),
                        "best_trades": [],
                    }

                db_trades_count = int(user['total_trades'] or 0) if user else 0
                
                if not user or db_trades_count < 15:
                    # ── DYNAMIC FALLBACK: If query is a Polymarket address, fetch via API ──
                    if q.startswith("0x") and len(q) == 42:
                        try:
                            import sys
                            import os
                            # Ensure tools module can be found if not already in path
                            if "tools" not in sys.modules:
                                sys.path.append(os.path.join(os.path.dirname(__file__), "tools"))
                            from wallet_xray import get_xray
                            
                            xray_data = get_xray(q, force_refresh=False)
                            if xray_data.get("all_time_vol", 0) > 0 or len(xray_data.get("history", [])) > 0:
                                best_trades = []
                                for p in xray_data.get("positions", []):
                                    if p["status"] == "up" and p["net_pnl"] > 0:
                                        best_trades.append({
                                            "market_title": p["title"],
                                            "outcome": p["outcome"],
                                            "profit": p["net_pnl"]
                                        })
                                    if len(best_trades) >= 3:
                                        break
                                
                                roi_all_time = 0.0
                                if xray_data.get("all_time_vol", 0) > 0:
                                    roi_all_time = xray_data["all_time_pnl"] / xray_data["all_time_vol"]

                                return {
                                    "wallet_address": q,
                                    "handle": xray_data.get("handle") or q,
                                    "source": "POLYMARKET (API DYNAMIC)",
                                    "win_rate": float(xray_data.get("win_rate") or 0.0),
                                    "roi_all_time": roi_all_time,
                                    "total_trades": len(xray_data.get("history", [])),
                                    "total_volume": float(xray_data.get("all_time_vol") or 0.0),
                                    "best_trades": best_trades,
                                }
                        except Exception as e:
                            log.error(f"[X-Ray Fallback] Failed for {q}: {e}")
                            
                    # If fallback fails or doesn't apply
                    if not user:
                        raise HTTPException(404, "Wallet or user not found.")
                wallet_address = user['wallet_address']
                # Use the real username from Redis if we have it
                display_handle = resolved_handle or user['handle']

                # ── Step 3: Enrich with live Redis trade stats ────────────────
                # The trades table is sparse (only resolved positions tracked)
                # so we cross-reference Redis for richer volume/trade counts
                redis_matching = [e for e in events
                                  if e.get('wallet_address', '').lower() == wallet_address.lower()]

                db_win_rate = float(user['win_rate'] or 0.0)
                db_roi      = float(user['roi_all_time'] or 0.0)
                db_trades   = int(user['total_trades'] or 0)
                db_volume   = float(user['total_volume_usd'] or 0.0)

                # If Redis has more recent data prefer it for trade count / volume
                if redis_matching:
                    latest_alert = redis_matching[0]
                    redis_trades = int(latest_alert.get('wallet_total_trades') or 0)
                    redis_volume = float(latest_alert.get('wallet_total_volume') or 0)
                    redis_wr     = float(latest_alert.get('wallet_win_rate') or 0)
                    redis_roi    = float(latest_alert.get('wallet_roi_30d') or 0)

                    # Take the higher of DB vs Redis since Redis has fresher data
                    db_trades = max(db_trades, redis_trades)
                    db_volume = max(db_volume, redis_volume)
                    if redis_wr > 0:
                        db_win_rate = redis_wr
                    if redis_roi != 0:
                        db_roi = redis_roi

                # Fetch top 3 profitable resolved trades from DB
                cur.execute("""
                    SELECT market_title, outcome, size as profit
                    FROM trades
                    WHERE wallet_address = %s AND resolved = TRUE AND won = TRUE
                    ORDER BY size DESC
                    LIMIT 3;
                """, (wallet_address,))
                best_trades = cur.fetchall()

                return {
                    "wallet_address": wallet_address,
                    "handle": display_handle,
                    "source": "POLYMARKET",
                    "win_rate": db_win_rate,
                    "roi_all_time": db_roi,
                    "total_trades": db_trades,
                    "total_volume": db_volume,
                    "best_trades": [dict(t) for t in best_trades],
                }

    except HTTPException:
        raise
    except Exception as e:
        log.error(f'/api/analyze-wallet failed: {e}', exc_info=True)
        raise HTTPException(500, "Internal Server Error")
