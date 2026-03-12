#!/usr/bin/env python3
"""
paper_trader.py — PolyVision Layer 3 Tool
Real paper-trading portfolio engine backed by Redis.
Records mock-follow entries with real entry prices and periodically
fetches current market prices from the Polymarket CLOB API to compute
actual P&L — no wallet or real money required.

Usage:
    python tools/paper_trader.py --test     # smoke test with fixtures
    python tools/paper_trader.py --status   # print current portfolio
"""

import os
import sys
import json
import uuid
import logging
import argparse
import requests
from datetime import datetime, timezone

import redis as redis_lib
from dotenv import load_dotenv

load_dotenv()

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CLOB_REST  = "https://clob.polymarket.com"
PORTFOLIO_KEY = "paper:portfolio"    # Redis hash  trade_id → JSON
TIMEOUT    = 8                        # seconds for CLOB HTTP calls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [paper] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


# ── Redis helpers ─────────────────────────────────────────────────────────────
def _redis():
    return redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)


# ── Price lookup via Polymarket CLOB ─────────────────────────────────────────
def _get_token_id(condition_id: str, outcome: str) -> str | None:
    """
    Fetches the token_id for a given condition_id + YES/NO outcome.
    Returns None on failure (network error, market not found, etc.).
    """
    try:
        resp = requests.get(
            f"{CLOB_REST}/markets/{condition_id}",
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        tokens = data.get("tokens", [])
        for token in tokens:
            if token.get("outcome", "").upper() == outcome.upper():
                return token.get("token_id")
    except Exception as e:
        log.warning(f"Token ID lookup failed for {condition_id}: {e}")
    return None


def _get_current_price(token_id: str) -> float | None:
    """
    Fetches the current midpoint price (0–1) for a token from the CLOB.
    Returns None on failure.
    """
    try:
        resp = requests.get(
            f"{CLOB_REST}/midpoint",
            params={"token_id": token_id},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return float(data.get("mid", 0))
    except Exception as e:
        log.warning(f"Price lookup failed for token {token_id}: {e}")
    return None


# ── Portfolio operations ───────────────────────────────────────────────────────
def follow(alert: dict) -> dict:
    """
    Records a paper trade entry. Call this when a user mock-follows a trade.
    Stores the full alert + entry metadata in Redis.
    Returns the saved trade record.
    """
    r = _redis()
    trade_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    record = {
        "trade_id":      trade_id,
        "alert_id":      alert.get("alert_id", ""),
        "market_id":     alert.get("market_id", ""),
        "market_title":  alert.get("market_title", ""),
        "outcome":       alert.get("outcome", "YES"),
        "entry_price":   float(alert.get("price", 0.5)),
        "paper_size":    100.0,   # $100 paper position (configurable later)
        "trader_handle": alert.get("trader_handle", "Unknown"),
        "whale_usd":     float(alert.get("usd_value", 0)),
        "conviction":    alert.get("conviction", 5),
        "followed_at":   now,
        "current_price": None,
        "pnl":           None,
        "pnl_pct":       None,
        "status":        "open",
    }

    r.hset(PORTFOLIO_KEY, trade_id, json.dumps(record))
    log.info(f"Paper follow: {record['trader_handle']} | {record['market_title'][:40]}")
    return record


def unfollow(trade_id: str) -> bool:
    """Removes a trade from the paper portfolio."""
    r = _redis()
    deleted = r.hdel(PORTFOLIO_KEY, trade_id)
    return bool(deleted)


def get_portfolio() -> dict:
    """
    Fetches all open paper trades from Redis, updates each with the
    current CLOB market price, and returns the full portfolio + aggregate stats.
    """
    r = _redis()
    raw = r.hgetall(PORTFOLIO_KEY)

    trades = []
    total_pnl        = 0.0
    total_invested   = 0.0
    winning_trades   = 0
    resolved_trades  = 0

    for trade_id, trade_json in raw.items():
        try:
            trade = json.loads(trade_json)
        except json.JSONDecodeError:
            continue

        # Fetch live price from Polymarket CLOB
        market_id = trade.get("market_id", "")
        outcome   = trade.get("outcome", "YES")
        current_price = None

        if market_id:
            token_id = _get_token_id(market_id, outcome)
            if token_id:
                current_price = _get_current_price(token_id)

        if current_price is not None:
            entry_price  = float(trade.get("entry_price", 0.5))
            paper_size   = float(trade.get("paper_size", 100.0))

            # Shares bought = paper_size / entry_price
            shares = paper_size / entry_price if entry_price > 0 else 0
            # Current value = shares × current_price  (in $, since max payout = 1 per share)
            current_value = shares * current_price
            pnl     = current_value - paper_size
            pnl_pct = (pnl / paper_size) * 100 if paper_size > 0 else 0

            trade["current_price"] = round(current_price, 4)
            trade["current_value"] = round(current_value, 2)
            trade["pnl"]           = round(pnl, 2)
            trade["pnl_pct"]       = round(pnl_pct, 2)

            total_pnl      += pnl
            total_invested += paper_size

            if pnl > 0:
                winning_trades += 1
            resolved_trades += 1

            # Persist updated price back to Redis
            r.hset(PORTFOLIO_KEY, trade_id, json.dumps(trade))

        trades.append(trade)

    roi_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0
    win_rate = (winning_trades / resolved_trades) if resolved_trades > 0 else 0.0

    return {
        "trades":          sorted(trades, key=lambda x: x.get("followed_at", ""), reverse=True),
        "total_trades":    len(trades),
        "total_invested":  round(total_invested, 2),
        "total_pnl":       round(total_pnl, 2),
        "roi_pct":         round(roi_pct, 2),
        "win_rate":        round(win_rate, 4),
        "priced_trades":   resolved_trades,
        "last_updated":    datetime.now(timezone.utc).isoformat(),
    }


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Paper Trader")
    parser.add_argument("--test",   action="store_true", help="Run smoke test with fixture data")
    parser.add_argument("--status", action="store_true", help="Print current portfolio state")
    args = parser.parse_args()

    if args.status:
        portfolio = get_portfolio()
        print(json.dumps(portfolio, indent=2))
        sys.exit(0)

    if args.test:
        print("🧪 Running paper_trader smoke test...\n")
        TEST_ALERT = {
            "alert_id":      "test-001",
            "market_id":     "0x1234abcd",
            "market_title":  "Will the Fed cut rates in March 2026?",
            "outcome":       "YES",
            "price":         0.65,
            "usd_value":     50000,
            "trader_handle": "The Oracle of Oregon",
            "conviction":    8,
        }
        record = follow(TEST_ALERT)
        print(f"✅ Followed trade: {record['trade_id']}")
        print(f"   Entry price: ${record['entry_price']}")
        print(f"   Market: {record['market_title']}")

        portfolio = get_portfolio()
        print(f"\n📊 Portfolio snapshot ({portfolio['total_trades']} trades):")
        for t in portfolio["trades"]:
            pnl_str = f"${t.get('pnl', 'N/A')}" if t.get('pnl') is not None else "N/A (price unavailable)"
            print(f"  [{t['outcome']}] {t['market_title'][:45]} | P&L: {pnl_str}")

        # Clean up test trade
        unfollow(record["trade_id"])
        print("\n✅ Smoke test complete. Test trade cleaned up.")
        sys.exit(0)
