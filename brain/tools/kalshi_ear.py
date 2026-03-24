#!/usr/bin/env python3
"""
PolyVision — Kalshi Ear (kalshi_ear.py)

Layer 3 Tool: Polls the Kalshi REST API every 60 seconds for large trades
and forwards them to the Brain's /ingest/trade endpoint.

Authentication: Kalshi uses RSA-PSS signing.
Required env vars:
    KALSHI_ACCESS_KEY  → your Kalshi API key (from Trading Settings → API)
    KALSHI_PRIVATE_KEY → RSA private key PEM (store with \\n escaped as literal \\n in Railway)
    KALSHI_THRESHOLD   → minimum USD value to forward (default: $5,000)
    BRAIN_URL          → Brain service URL (default: http://localhost:8000)

Self-annealing log:
  2026-03-23: Initial implementation. Kalshi API requires RSA-PSS auth; all
              endpoints return 401 without credentials. The /trade-api/v2/markets/trades
              endpoint uses cursor-based pagination (newest trades first).
              Post-migration (March 12, 2026): uses count_fp and yes_price_fp fields.
              Falls back to count/yes_price for backward compat.
  2026-03-23: Fixed 401 — Kalshi signature must use BASE PATH ONLY (no query string).
              i.e. sign(ts + "GET" + "/trade-api/v2/markets/trades"), NOT the ?limit=200 part.
              Query params are passed separately in the request, not in the signed message.
"""

import os
import time
import hashlib
import base64
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
KALSHI_BASE       = 'https://trading-api.kalshi.com'
KALSHI_ACCESS_KEY = os.getenv('KALSHI_ACCESS_KEY', '')
KALSHI_PRIVATE_KEY = os.getenv('KALSHI_PRIVATE_KEY', '')   # PEM with \\n escaped in Railway
KALSHI_THRESHOLD  = float(os.getenv('KALSHI_THRESHOLD', '5000'))   # $5,000 minimum
BRAIN_URL_ENV     = os.getenv('BRAIN_URL', 'http://localhost:8000')

# ── In-memory state ───────────────────────────────────────────────────────────
_market_cache: dict[str, str] = {}     # ticker → market title
_seen_trades: set[str] = set()         # dedup set (clears when > 50,000 entries)
_last_cursor: Optional[str] = None     # Kalshi pagination cursor


# ── RSA-PSS Auth ──────────────────────────────────────────────────────────────
def _sign_headers(method: str, path: str) -> dict:
    """
    Generate Kalshi authentication headers using RSA-PSS signing.
    Kalshi protocol: sign(timestamp_ms + METHOD_UPPERCASE + base_path)
    Salt length: PSS.MAX_LENGTH (Kalshi reference SDK uses max, not digest length)
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding as apadding

        ts = str(int(time.time() * 1000))   # milliseconds
        msg = (ts + method.upper() + path).encode('utf-8')

        # Handle both escaped \\n and real newlines (Railway pastes may vary)
        pem = KALSHI_PRIVATE_KEY.replace('\\n', '\n').strip()
        if not pem.startswith('-----BEGIN'):
            raise ValueError('KALSHI_PRIVATE_KEY does not look like a valid PEM key.')

        private_key = serialization.load_pem_private_key(pem.encode(), password=None)
        sig = private_key.sign(
            msg,
            apadding.PSS(
                mgf=apadding.MGF1(hashes.SHA256()),
                salt_length=apadding.PSS.MAX_LENGTH,   # Kalshi reference SDK: MAX_LENGTH
            ),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(sig).decode()
        log.debug(f'[Kalshi] Signed {method} {path} ts={ts} sig_prefix={sig_b64[:16]}...')
        return {
            'KALSHI-ACCESS-KEY':       KALSHI_ACCESS_KEY,
            'KALSHI-ACCESS-TIMESTAMP': ts,
            'KALSHI-ACCESS-SIGNATURE': sig_b64,
            'Content-Type':            'application/json',
        }
    except ImportError:
        log.error('[Kalshi] cryptography package not installed. Run: pip install cryptography')
        return {}
    except Exception as e:
        log.error(f'[Kalshi] Auth signing failed: {e}')
        return {}


# ── Market Title Cache ────────────────────────────────────────────────────────
def _get_market_title(ticker: str) -> str:
    """Fetch and cache the market title for a Kalshi ticker."""
    if ticker in _market_cache:
        return _market_cache[ticker]

    path = f'/trade-api/v2/markets/{ticker}'
    try:
        resp = requests.get(
            KALSHI_BASE + path,
            headers=_sign_headers('GET', path),
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Try both v1 and v2 response shapes
            title = (
                data.get('market', {}).get('title')
                or data.get('market', {}).get('question')
                or data.get('title')
                or ticker
            )
            _market_cache[ticker] = title
            log.debug(f'[Kalshi] Cached market title: {ticker} → "{title}"')
            return title
        else:
            log.warning(f'[Kalshi] Market lookup failed for {ticker}: {resp.status_code}')
    except Exception as e:
        log.warning(f'[Kalshi] Could not fetch market title for {ticker}: {e}')

    return ticker  # fallback to the ticker


# ── Main Poller ───────────────────────────────────────────────────────────────
def poll_kalshi(brain_url: str = None) -> int:
    """
    Poll Kalshi for large trades and forward qualifying ones to Brain.
    Returns count of trades forwarded.

    Called by APScheduler in main.py every 60 seconds.
    """
    global _last_cursor, _seen_trades

    brain = brain_url or BRAIN_URL_ENV

    # Guard: skip silently if credentials not configured
    if not KALSHI_ACCESS_KEY:
        log.debug('[Kalshi] KALSHI_ACCESS_KEY not set — skipping poll.')
        return 0
    if not KALSHI_PRIVATE_KEY:
        log.debug('[Kalshi] KALSHI_PRIVATE_KEY not set — skipping poll.')
        return 0

    path = '/trade-api/v2/markets/trades'
    params: dict = {'limit': 200}
    if _last_cursor:
        params['cursor'] = _last_cursor

    try:
        resp = requests.get(
            KALSHI_BASE + path,
            headers=_sign_headers('GET', path),   # sign base path only — NO query string
            params=params,
            timeout=12,
        )
    except Exception as e:
        log.error(f'[Kalshi] Request failed: {e}')
        return 0

    if resp.status_code == 401:
        log.error(
            f'[Kalshi] Authentication failed (401).\n'
            f'  KALSHI-ACCESS-KEY: {KALSHI_ACCESS_KEY[:8]}...\n'
            f'  Response body: {resp.text[:400]}'
        )
        return 0
    if resp.status_code != 200:
        log.error(f'[Kalshi] API error {resp.status_code}: {resp.text[:200]}')
        return 0

    data = resp.json()
    trades = data.get('trades', [])
    new_cursor = data.get('cursor')

    # Only advance cursor if we got a full page (avoid re-processing on retry)
    if len(trades) >= 200 and new_cursor:
        _last_cursor = new_cursor

    forwarded = 0
    for trade in trades:
        trade_id = trade.get('trade_id', '')
        if not trade_id:
            continue
        if trade_id in _seen_trades:
            continue
        _seen_trades.add(trade_id)

        # Prevent unbounded memory growth
        if len(_seen_trades) > 50_000:
            _seen_trades.clear()

        # ── Parse trade values (handle post-/pre-migration field names) ──────
        ticker     = trade.get('ticker', '')
        taker_side = (trade.get('taker_side', 'yes') or 'yes').lower()

        # count_fp = contracts as fixed-point string (post March 2026 migration)
        # count = integer (legacy, removed March 12, 2026)
        count_raw = trade.get('count_fp') or trade.get('count') or 0
        count = float(count_raw) if count_raw else 0.0

        # yes_price_fp = dollar string ("0.72") post-migration
        # yes_price = integer cents (legacy)
        price_raw = (
            trade.get('yes_price_fp')
            or trade.get('yes_price_dollars')
            or (float(trade.get('yes_price', 50)) / 100 if trade.get('yes_price') else None)
            or 0.5
        )
        yes_price = float(price_raw) if price_raw else 0.5

        # USD value: contracts × price per contract ($1 at expiry)
        # If taker buys YES, they pay yes_price per contract
        # If taker buys NO (sells YES), they pay (1 - yes_price) per contract
        if taker_side == 'yes':
            usd_value = count * yes_price
            price = yes_price
            outcome = 'YES'
        else:
            usd_value = count * (1.0 - yes_price)
            price = 1.0 - yes_price
            outcome = 'NO'

        if usd_value < KALSHI_THRESHOLD:
            continue

        # ── Fetch market title ───────────────────────────────────────────────
        market_title = _get_market_title(ticker) if ticker else 'Unknown Kalshi Market'

        # ── Synthetic wallet address (Kalshi is centralized — no on-chain addr) ─
        synthetic_wallet = 'kalshi:' + hashlib.md5(ticker.encode()).hexdigest()[:12]

        created_time = trade.get('created_time', datetime.now(timezone.utc).isoformat())
        if isinstance(created_time, (int, float)):
            created_time = datetime.fromtimestamp(created_time, tz=timezone.utc).isoformat()

        event = {
            'id':            f'kalshi-{trade_id}',
            'market_id':     ticker,
            'market_title':  market_title,
            'outcome':       outcome,
            'price':         round(price, 4),
            'size':          count,
            'usd_value':     round(usd_value, 2),
            'maker_address': synthetic_wallet,
            'taker_address': '',
            'side':          'BUY',
            'timestamp':     created_time,
            'source':        'KALSHI',   # ← platform badge in dashboard UI
        }

        try:
            r = requests.post(f'{brain}/ingest/trade', json=event, timeout=5)
            if r.status_code in (200, 201):
                forwarded += 1
                log.info(
                    f'[Kalshi] ⚡ ${usd_value:,.0f} {outcome} on '
                    f'"{market_title[:55]}" ({ticker})'
                )
            else:
                log.warning(
                    f'[Kalshi] Brain rejected event for {ticker}: '
                    f'{r.status_code} — {r.text[:100]}'
                )
        except Exception as e:
            log.warning(f'[Kalshi] Could not forward to Brain: {e}')

    log.info(
        f'[Kalshi] Poll done. {len(trades)} trades scanned, '
        f'{forwarded} forwarded (≥${KALSHI_THRESHOLD:,.0f}).'
    )
    return forwarded


# ── Standalone Test ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    if not KALSHI_ACCESS_KEY or not KALSHI_PRIVATE_KEY:
        print('[Kalshi] ❌ KALSHI_ACCESS_KEY and KALSHI_PRIVATE_KEY must be set in .env')
        sys.exit(1)
    n = poll_kalshi()
    print(f'[Kalshi] ✅ Test poll complete. {n} trades forwarded.')
