#!/usr/bin/env python3
"""
cluster_detector.py — PolyVision Layer 3 Tool
Detects "Whale Cluster" events: 3+ unique wallets buying the same market
outcome within a 15-minute rolling window.

When a cluster is detected:
  - Returns a CLUSTER alert payload that REPLACES the standard alert
  - Throttle key in Redis prevents duplicate cluster alerts for the
    same market:outcome for 10 minutes after firing.

Usage (test):
    python tools/cluster_detector.py --test
"""

import os
import json
import time
import logging
import argparse
import uuid
from datetime import datetime, timezone

import redis as redis_lib
from dotenv import load_dotenv

load_dotenv()

REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379/0")
WINDOW_SECONDS    = int(os.getenv("CLUSTER_WINDOW_SEC",  "900"))   # 15 minutes
CLUSTER_THRESHOLD = int(os.getenv("CLUSTER_THRESHOLD",   "3"))      # unique wallets
THROTTLE_SECONDS  = int(os.getenv("CLUSTER_THROTTLE_SEC","600"))    # 10 min cooldown
MIN_USD_VALUE     = float(os.getenv("CLUSTER_MIN_USD",   "10000"))  # each leg ≥ $10k

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cluster] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


def _redis():
    return redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)


def check_cluster(trade_event: dict, base_alert: dict) -> dict | None:
    """
    Records the incoming trade in the rolling window and checks for cluster.

    Args:
        trade_event: raw trade dict from the Ear (must have market_id, outcome,
                     maker_address, usd_value, market_title, timestamp)
        base_alert:  the standard WhaleAlertPayload already built by signal_engine

    Returns:
        A CLUSTER alert payload dict if a cluster is detected, else None.
    """
    try:
        usd_value = float(trade_event.get("usd_value", 0))
        if usd_value < MIN_USD_VALUE:
            return None

        market_id = trade_event.get("market_id", "")
        outcome   = trade_event.get("outcome", "").upper()
        wallet    = trade_event.get("maker_address", "")

        if not market_id or not outcome or not wallet:
            return None

        r         = _redis()
        now       = time.time()
        window_start = now - WINDOW_SECONDS

        # ── Sorted set key: one per market+outcome combination ──────────────
        zset_key     = f"cluster:window:{market_id}:{outcome}"
        throttle_key = f"cluster:fired:{market_id}:{outcome}"

        # Already fired a cluster alert for this market:outcome recently?
        if r.exists(throttle_key):
            log.debug(f"Cluster throttle active for {market_id}:{outcome}")
            return None

        # Serialize the trade entry (wallet + trade info) as a JSON member
        entry = json.dumps({
            "wallet":        wallet,
            "handle":        base_alert.get("trader_handle", wallet[:10]),
            "usd_value":     usd_value,
            "price":         trade_event.get("price", 0),
            "timestamp":     now,
        })

        # Add to rolling window (score = unix timestamp)
        pipe = r.pipeline()
        pipe.zadd(zset_key, {entry: now})
        # Trim out entries older than the window
        pipe.zremrangebyscore(zset_key, "-inf", window_start)
        # Auto-expire the key (window_seconds * 2 headroom)
        pipe.expire(zset_key, WINDOW_SECONDS * 2)
        pipe.execute()

        # Fetch all entries in the current window
        raw_entries = r.zrangebyscore(zset_key, window_start, "+inf")
        participants = []
        seen_wallets: set[str] = set()

        for raw in raw_entries:
            try:
                e = json.loads(raw)
                w = e.get("wallet", "")
                if w not in seen_wallets:
                    seen_wallets.add(w)
                    participants.append(e)
            except json.JSONDecodeError:
                continue

        unique_count = len(seen_wallets)
        log.info(f"Cluster check {market_id}:{outcome} → {unique_count} unique wallets in window")

        if unique_count < CLUSTER_THRESHOLD:
            return None

        # ── Cluster detected! ───────────────────────────────────────────────
        log.info(f"🚨 CLUSTER DETECTED: {unique_count} whales on {market_id}:{outcome}")

        # Set throttle so we don't re-fire for the same market:outcome
        r.setex(throttle_key, THROTTLE_SECONDS, "1")

        total_volume = sum(p.get("usd_value", 0) for p in participants)
        avg_price    = (
            sum(p.get("price", 0) * p.get("usd_value", 0) for p in participants) / total_volume
            if total_volume > 0 else 0
        )

        cluster_alert = {
            "alert_id":           str(uuid.uuid4()),
            "alert_tier":         "CLUSTER",
            "trader_handle":      f"🚨 {unique_count}-Whale Cluster",
            "wallet_address":     wallet,               # triggering wallet
            "market_title":       trade_event.get("market_title", ""),
            "market_id":          market_id,
            "outcome":            outcome,
            "price":              round(avg_price, 4),
            "usd_value":          round(total_volume, 2),
            "cluster_count":      unique_count,
            "cluster_participants": participants,
            "wallet_win_rate":    base_alert.get("wallet_win_rate"),
            "wallet_roi_30d":     base_alert.get("wallet_roi_30d"),
            "ai_summary":         None,                 # filled by ai_summarizer
            "copy_trade_recommended": True,             # clusters always recommend
            "disclaimer":         "Whale clusters may indicate coordinated hedging. Not financial advice.",
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "source_trade_id":    trade_event.get("id", ""),
        }

        return cluster_alert

    except Exception as e:
        log.error(f"Cluster detector error: {e}", exc_info=True)
        return None


# ── CLI Smoke Test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        print("🧪 Simulating 3 whale trades on the same market:outcome...\n")

        test_market = {
            "id":           "test-001",
            "market_id":    "0xtest_cluster_market",
            "market_title": "Will AI reach AGI before 2030?",
            "outcome":      "YES",
            "price":        0.62,
            "size":         80000,
            "usd_value":    50000.00,
            "maker_address": "",
            "side":         "BUY",
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }

        base = {"trader_handle": "Test Whale", "wallet_win_rate": 0.72, "wallet_roi_30d": 0.18}

        for i, wallet in enumerate(["0xWHALE_A", "0xWHALE_B", "0xWHALE_C"], 1):
            test_market["maker_address"] = wallet
            test_market["id"] = f"test-00{i}"
            result = check_cluster(test_market, base)
            print(f"Trade {i} ({wallet}): cluster={'YES 🚨' if result else 'no'}")
            if result:
                print(json.dumps(result, indent=2))
