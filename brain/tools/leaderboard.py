#!/usr/bin/env python3
"""
leaderboard.py — PolyVision Layer 3 Tool
Fetches the top-N Polymarket traders by all-time P&L from the public
Data API and caches the result in Redis (TTL: 5 minutes).
Also enriches each wallet with a generated handle via whale_profiler.

Usage:
    python tools/leaderboard.py --top 100     # print top 100 to stdout
    python tools/leaderboard.py --test        # smoke test with 5 results
"""

import os
import sys
import json
import logging
import argparse
import requests

import redis as redis_lib
from dotenv import load_dotenv

load_dotenv()

REDIS_URL   = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATA_API    = "https://data-api.polymarket.com"
CACHE_KEY   = "leaderboard:top100"
CACHE_TTL   = 300     # 5 minutes
DEFAULT_TOP = 100
TIMEOUT     = 12

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [leaderboard] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


def _redis():
    return redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)


def _fetch_from_api(limit: int = DEFAULT_TOP) -> list[dict]:
    """Fetches the raw leaderboard from the Polymarket Data API."""
    try:
        resp = requests.get(
            f"{DATA_API}/v1/leaderboard",
            params={
                "category":   "OVERALL",
                "timePeriod": "ALL",
                "orderBy":    "PNL",
                "limit":      min(limit, 50),   # API max is 50 per page
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # API returns either a list directly or wrapped in a key
        if isinstance(data, list):
            return data
        return data.get("data", data.get("results", data.get("leaderboard", [])))
    except Exception as e:
        log.error(f"Failed to fetch leaderboard from Data API: {e}")
        return []


def _normalize(rank: int, raw: dict) -> dict:
    """Normalises a raw API row into a consistent schema."""
    wallet   = raw.get("proxyWallet") or raw.get("address") or raw.get("walletAddress", "")
    pnl      = float(raw.get("pnl") or 0)
    volume   = float(raw.get("vol") or raw.get("volume") or raw.get("tradingVolume") or 0)
    trades   = int(raw.get("tradesCount") or raw.get("numTrades") or raw.get("positionsCount") or 0)
    win_rate = float(raw.get("winRate") or 0)

    # Use the displayed username, fall back to wallet-derived handle
    short  = wallet[-6:].upper() if len(wallet) >= 6 else wallet
    handle = raw.get("userName") or raw.get("name") or raw.get("pseudonym") or f"Trader 0x…{short}"

    return {
        "rank":        int(raw.get("rank", rank)),
        "wallet":      wallet,
        "handle":      handle,
        "pnl":         round(pnl, 2),
        "volume":      round(volume, 2),
        "trades":      trades,
        "win_rate":    round(win_rate, 4),
    }


def get_leaderboard(limit: int = DEFAULT_TOP, force_refresh: bool = False) -> list[dict]:
    """
    Returns the top-N leaderboard rows.
    Uses Redis cache (TTL 5 min) to avoid hammering the Data API.
    Pass force_refresh=True to bypass cache.
    """
    r = _redis()

    if not force_refresh:
        cached = r.get(CACHE_KEY)
        if cached:
            try:
                return json.loads(cached)[:limit]
            except json.JSONDecodeError:
                pass

    raw_rows = _fetch_from_api(limit)
    if not raw_rows:
        # Return stale cache rather than empty list if API fails
        stale = r.get(CACHE_KEY)
        if stale:
            log.warning("Returning stale leaderboard cache — API fetch failed.")
            return json.loads(stale)[:limit]
        return []

    normalized = [_normalize(i + 1, row) for i, row in enumerate(raw_rows)]
    r.setex(CACHE_KEY, CACHE_TTL, json.dumps(normalized))
    log.info(f"Leaderboard cached: {len(normalized)} traders.")
    return normalized[:limit]


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Leaderboard")
    parser.add_argument("--top",  type=int, default=DEFAULT_TOP, help="How many traders to fetch")
    parser.add_argument("--test", action="store_true", help="Smoke test (top 5)")
    parser.add_argument("--refresh", action="store_true", help="Force cache refresh")
    args = parser.parse_args()

    if args.test:
        print("🧪 Leaderboard smoke test (top 5)...\n")
        rows = get_leaderboard(limit=5, force_refresh=True)
        if not rows:
            print("❌ No data returned. Check REDIS_URL and network.")
            sys.exit(1)
        for row in rows:
            print(f"  #{row['rank']:>3}  {row['handle']:<28}  P&L: ${row['pnl']:>12,.2f}  "
                  f"Win: {row['win_rate']:.0%}  Trades: {row['trades']}")
        print(f"\n✅ {len(rows)} rows returned.")
        sys.exit(0)

    rows = get_leaderboard(limit=args.top, force_refresh=args.refresh)
    print(json.dumps(rows, indent=2))
