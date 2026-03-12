#!/usr/bin/env python3
"""
wallet_xray.py — PolyVision Layer 3 Tool
Fetches a comprehensive X-Ray profile for a Polymarket wallet address:
  - Biography / username from leaderboard
  - Full trade history (last 50 activity entries)
  - Derived equity curve (cumulative P&L over time)
  - Per-market profit/loss classification (winning vs underwater)

Usage:
    python tools/wallet_xray.py --wallet 0x56687... --test
"""

import os
import sys
import json
import logging
import argparse
import requests
from datetime import datetime, timezone

import redis as redis_lib
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATA_API  = "https://data-api.polymarket.com"
CACHE_TTL = 60   # seconds per wallet — short because positions change frequently
TIMEOUT   = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [xray] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


def _redis():
    return redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)


# ── Data Fetchers ─────────────────────────────────────────────────────────────

def _fetch_activity(wallet: str, limit: int = 50) -> list[dict]:
    """Fetches the wallet's recent trade activity from the Data API."""
    try:
        resp = requests.get(
            f"{DATA_API}/activity",
            params={"user": wallet, "limit": limit},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        log.warning(f"Activity fetch failed for {wallet}: {e}")
        return []


def _fetch_wallet_stats(wallet: str) -> dict:
    """
    Fetches all-time stats for this specific wallet from the leaderboard endpoint.
    Returns empty dict if the wallet isn't in the top 50.
    """
    try:
        resp = requests.get(
            f"{DATA_API}/v1/leaderboard",
            params={"category": "OVERALL", "timePeriod": "ALL",
                    "orderBy": "PNL", "limit": 50, "user": wallet},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        rows = data if isinstance(data, list) else data.get("data", [])
        if rows:
            return rows[0]
    except Exception as e:
        log.warning(f"Wallet stats fetch failed for {wallet}: {e}")
    return {}


# ── Processors ────────────────────────────────────────────────────────────────

def _build_equity_curve(activity: list[dict]) -> list[dict]:
    """
    Derives a cumulative P&L curve from activity entries sorted oldest-first.
    Includes BUY (negative cash flow) and REDEEM/WIN (positive cash flow).
    Returns list of {ts, cumulative_pnl} for Chart.js consumption.
    """
    sorted_activity = sorted(activity, key=lambda x: x.get("timestamp", 0))
    cumulative = 0.0
    curve = []
    for entry in sorted_activity:
        ts   = entry.get("timestamp", 0)
        typ  = entry.get("type", "")
        size = float(entry.get("usdcSize") or 0)

        if typ in ("BUY",):
            cumulative -= size            # cash out
        elif typ in ("REDEEM", "SELL"):
            cumulative += size            # cash in
        # MERGE, SPLIT, CONVERT — skip (non-cash events)
        if typ in ("BUY", "REDEEM", "SELL"):
            curve.append({
                "ts":  ts,
                "pnl": round(cumulative, 2),
                "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d"),
            })
    return curve


def _build_positions(activity: list[dict]) -> list[dict]:
    """
    Aggregates open-ish positions by conditionId from activity.
    Treats any market where buys > redeems as still "open".
    Marks each as 'up', 'down', or 'neutral' based on net USDC flow.
    Returns sorted by net_pnl descending (best → worst).
    """
    markets: dict[str, dict] = {}

    for entry in activity:
        cid   = entry.get("conditionId", "")
        typ   = entry.get("type", "")
        size  = float(entry.get("usdcSize") or 0)
        title = entry.get("title") or entry.get("slug") or cid[:12] + "…"
        outcome = entry.get("outcome") or entry.get("side") or "?"

        if cid not in markets:
            markets[cid] = {
                "condition_id": cid,
                "title":        title,
                "outcome":      outcome,
                "spent":        0.0,   # USD paid in (BUYs)
                "received":     0.0,   # USD received (REDEEMs/SELLs)
            }

        if typ == "BUY":
            markets[cid]["spent"] += size
        elif typ in ("REDEEM", "SELL"):
            markets[cid]["received"] += size

    positions = []
    for m in markets.values():
        net = round(m["received"] - m["spent"], 2)
        still_open = m["received"] < m["spent"] * 0.9   # < 90% redeemed = likely open
        status = "up" if net > 0 else ("down" if net < 0 else "neutral")
        positions.append({
            "title":      m["title"],
            "outcome":    m["outcome"],
            "spent":      round(m["spent"], 2),
            "received":   round(m["received"], 2),
            "net_pnl":    net,
            "status":     status,
            "is_open":    still_open,
        })

    return sorted(positions, key=lambda x: x["net_pnl"], reverse=True)


# ── Main X-Ray Builder ────────────────────────────────────────────────────────

def get_xray(wallet: str, force_refresh: bool = False) -> dict:
    """
    Returns the full X-Ray profile for a wallet.
    Uses Redis cache (TTL 60s). Pass force_refresh=True to bypass.
    """
    r = _redis()
    cache_key = f"xray:{wallet}"

    if not force_refresh:
        cached = r.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

    log.info(f"Fetching X-Ray for {wallet[:10]}…")
    activity     = _fetch_activity(wallet, limit=100)
    stats        = _fetch_wallet_stats(wallet)
    equity_curve = _build_equity_curve(activity)
    positions    = _build_positions(activity)

    handle = (
        stats.get("userName") or stats.get("name") or
        activity[0].get("name") if activity else None or
        f"Trader 0x…{wallet[-6:].upper()}"
    )

    profile = {
        "wallet":       wallet,
        "handle":       handle,
        "all_time_pnl": round(float(stats.get("pnl") or 0), 2),
        "all_time_vol": round(float(stats.get("vol") or 0), 2),
        "win_rate":     round(float(stats.get("winRate") or 0), 4),
        "positions":    positions[:30],       # top 30 markets
        "equity_curve": equity_curve[-100:],  # last 100 data points
        "history":      activity[:50],        # last 50 raw trades
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
    }

    r.setex(cache_key, CACHE_TTL, json.dumps(profile))
    log.info(f"X-Ray fetched: {len(positions)} markets, {len(equity_curve)} curve points.")
    return profile


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Wallet X-Ray")
    parser.add_argument("--wallet", default="0x56687bf447db6ffa42ffe2204a05edaa20f55839",
                        help="Wallet address to X-ray")
    parser.add_argument("--test", action="store_true", help="Smoke test with Theo4")
    args = parser.parse_args()

    profile = get_xray(args.wallet, force_refresh=True)
    print(f"\n🔬 X-RAY: {profile.get('handle')} ({args.wallet[:10]}…)")
    print(f"   All-time P&L : ${profile['all_time_pnl']:>14,.2f}")
    print(f"   Volume       : ${profile['all_time_vol']:>14,.2f}")
    print(f"   Markets seen : {len(profile['positions'])}")
    print(f"   History rows : {len(profile['history'])}")
    print(f"   Curve points : {len(profile['equity_curve'])}")
    print(f"\n📊 Top 5 positions by P&L:")
    for p in profile["positions"][:5]:
        icon = "🟢" if p["status"] == "up" else ("🔴" if p["status"] == "down" else "⚪")
        open_tag = " [OPEN]" if p["is_open"] else ""
        print(f"  {icon} {p['title'][:45]:<45} net: ${p['net_pnl']:>10,.2f}{open_tag}")
