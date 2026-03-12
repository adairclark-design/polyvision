#!/usr/bin/env python3
"""
polymarket_pulse.py — PolyVision Layer 3 Tool
Polls the Polymarket Data API for recent activity and pushes
qualifying trades (>= $10k USD) into Redis for downstream processing.

Architecture SOP: architecture/01_data_ingestion.md
Usage:
    python tools/polymarket_pulse.py           # continuous polling loop
    python tools/polymarket_pulse.py --once    # single fetch, prints payload, exits
"""

import os
import sys
import json
import time
import logging
import argparse
import requests
import redis as redis_lib
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
GAMMA_API_BASE    = "https://gamma-api.polymarket.com"
CLOB_API_BASE     = "https://clob.polymarket.com"
POLL_INTERVAL     = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
ALERT_THRESHOLD   = float(os.getenv("ALERT_THRESHOLD_STANDARD", "10000"))
REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY        = "stream:trades:raw"
DEDUP_TTL         = 7200   # 2 hours in seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pulse] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(".tmp/pulse.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)


# ── Redis ─────────────────────────────────────────────────────────────────────
def get_redis() -> redis_lib.Redis:
    return redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)


# ── API Helpers ───────────────────────────────────────────────────────────────
def fetch_active_markets(limit: int = 50) -> list[dict]:
    """
    Fetch active markets from the CLOB public /markets endpoint.
    NOTE: Data API /activity requires a user wallet address (not public).
    CLOB /markets is fully public. We use this to discover markets, then
    check their order books for large-wall accumulation signals.
    Self-annealing update: switched from Data API /activity (400) to CLOB /markets.
    """
    backoff = 2
    for attempt in range(3):
        try:
            r = requests.get(
                f"{CLOB_API_BASE}/markets",
                params={"limit": limit, "next_cursor": "MA=="},
                timeout=10,
            )
            if r.status_code == 429:
                log.warning(f"Rate limited. Backing off {backoff}s.")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            r.raise_for_status()
            data = r.json()
            return data.get("data", [])
        except requests.RequestException as e:
            log.error(f"CLOB /markets fetch attempt {attempt+1} failed: {e}")
            time.sleep(5)
    return []


def fetch_recent_trades_gamma(condition_id: str, limit: int = 10) -> list[dict]:
    """Fetch recent trades for a market from the Gamma API."""
    try:
        r = requests.get(
            f"{GAMMA_API_BASE}/trades",
            params={"conditionId": condition_id, "limit": limit},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        log.debug(f"Gamma /trades for {condition_id}: {e}")
    return []


def fetch_market_title(condition_id: str, r: redis_lib.Redis) -> str:
    """Return market title from Redis cache or Gamma API."""
    cache_key = f"market:{condition_id}"
    cached = r.get(cache_key)
    if cached:
        return cached
    try:
        resp = requests.get(
            f"{GAMMA_API_BASE}/markets",
            params={"conditionId": condition_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        title = data[0].get("question", condition_id) if data else condition_id
        r.setex(cache_key, 3600, title)
        return title
    except Exception:
        return condition_id


# ── Trade Normalizer ──────────────────────────────────────────────────────────
def normalize_trade(raw: dict, market_title: str) -> dict:
    """Convert a raw Data API activity item into a TradeEvent."""
    size      = float(raw.get("size", 0) or 0)
    price     = float(raw.get("price", 0) or 0)
    usd_value = size * price

    return {
        "id":             raw.get("id") or raw.get("transactionHash", ""),
        "market_id":      raw.get("conditionId", ""),
        "market_title":   market_title,
        "outcome":        raw.get("outcomeIndex", ""),
        "price":          price,
        "size":           size,
        "usd_value":      round(usd_value, 2),
        "maker_address":  raw.get("maker", ""),
        "taker_address":  raw.get("taker", ""),
        "side":           raw.get("side", "BUY").upper(),
        "timestamp":      raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }


# ── Main Loop ─────────────────────────────────────────────────────────────────
def process_once(r: redis_lib.Redis, verbose: bool = False) -> list[dict]:
    """Fetch markets + trades, filter, deduplicate, push to Redis stream. Returns qualifying events."""
    markets = fetch_active_markets(limit=30)
    qualifying = []

    for market in markets[:10]:  # Check top 10 active markets per cycle
        condition_id = market.get("condition_id", "")
        if not condition_id:
            continue

        market_title = market.get("question", condition_id)
        # Cache the market title in Redis
        cache_key = f"market:{condition_id}"
        r.setex(cache_key, 3600, market_title)

        raw_trades = fetch_recent_trades_gamma(condition_id, limit=20)

        for raw in raw_trades:
            trade_id = raw.get("id", "") or raw.get("transactionHash", "")
            if not trade_id:
                continue

            # Deduplication check
            dedup_key = f"processed:{trade_id}"
            if r.get(dedup_key):
                continue
            r.setex(dedup_key, DEDUP_TTL, "1")

            event = normalize_trade(raw, market_title)

            if event["usd_value"] < ALERT_THRESHOLD:
                continue

            # Push to Redis stream
            r.xadd(STREAM_KEY, {"payload": json.dumps(event)})
            qualifying.append(event)

            if verbose:
                print(json.dumps(event, indent=2))

    return qualifying


def run_loop():
    """Continuous polling loop."""
    r = get_redis()
    log.info(f"PolyVision Pulse started. Polling every {POLL_INTERVAL}s.")
    while True:
        try:
            events = process_once(r)
            if events:
                log.info(f"Pushed {len(events)} qualifying trade(s) to {STREAM_KEY}.")
        except Exception as e:
            log.error(f"Unexpected error in poll cycle: {e}")
        time.sleep(POLL_INTERVAL)


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(".tmp", exist_ok=True)
    parser = argparse.ArgumentParser(description="PolyVision Pulse — trade ingestion")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    if args.once:
        r = get_redis()
        events = process_once(r, verbose=True)
        print(f"\n✅ Found {len(events)} qualifying trade(s) >= ${ALERT_THRESHOLD:,.0f}")
        sys.exit(0)
    else:
        run_loop()
