#!/usr/bin/env python3
"""
signal_engine.py — PolyVision Layer 3 Tool
Applies deterministic threshold filtering, alert tier assignment, and
copy-trade eligibility to produce a final WhaleAlertPayload.

Architecture SOP: architecture/02_signal_engine.md
Usage:
    python tools/signal_engine.py --test         # run with fixture data
    python tools/signal_engine.py < trade.json   # pipe a TradeEvent JSON
"""

import os
import sys
import json
import uuid
import argparse
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
THRESHOLD_STANDARD  = float(os.getenv("ALERT_THRESHOLD_STANDARD", "10000"))
THRESHOLD_WHALE     = float(os.getenv("ALERT_THRESHOLD_WHALE", "50000"))
COPY_TRADE_MIN_WR   = float(os.getenv("COPY_TRADE_MIN_WIN_RATE", "0.60"))
DISCLAIMER          = "Whales can hedge. Following a trade is at your own risk."

logging.basicConfig(level=logging.INFO, format="%(asctime)s [signal] %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ── Core Logic ────────────────────────────────────────────────────────────────
def build_alert(trade_event: dict, whale_profile: dict | None) -> dict | None:
    """
    Applies signal engine logic to a TradeEvent + WhaleProfile.
    Returns a WhaleAlertPayload or None if trade doesn't qualify.
    """
    usd_value = float(trade_event.get("usd_value", 0))

    # ── Rule 1: Hard threshold gate ──────────────────────────────────────────
    if usd_value < THRESHOLD_STANDARD:
        log.debug(f"Trade {trade_event.get('id')} below threshold (${usd_value:,.0f}). Discarding.")
        return None

    # ── Rule 2: Alert tier assignment ────────────────────────────────────────
    alert_tier = "WHALE" if usd_value >= THRESHOLD_WHALE else "STANDARD"

    # ── Rule 3: Copy-trade eligibility ───────────────────────────────────────
    win_rate = None
    roi_30d  = None

    if whale_profile:
        win_rate = whale_profile.get("win_rate")
        roi_30d  = whale_profile.get("roi_30d")

    # Never recommend if win_rate is unknown or below threshold
    copy_trade_recommended = (
        win_rate is not None and win_rate >= COPY_TRADE_MIN_WR
    )

    # ── Rule 4: Build payload ─────────────────────────────────────────────────
    payload = {
        "alert_id":               str(uuid.uuid4()),
        "alert_tier":             alert_tier,
        "trader_handle":          (whale_profile or {}).get("handle", f"Whale {trade_event.get('maker_address','?')[:6]}"),
        "wallet_address":         trade_event.get("maker_address", ""),
        "market_title":           trade_event.get("market_title", ""),
        "market_id":              trade_event.get("market_id", ""),
        "outcome":                trade_event.get("outcome", ""),
        "price":                  trade_event.get("price", 0),
        "usd_value":              usd_value,
        "wallet_win_rate":        win_rate,
        "wallet_roi_30d":         roi_30d,
        "ai_summary":             None,   # filled by ai_summarizer.py
        "copy_trade_recommended": copy_trade_recommended,
        "disclaimer":             DISCLAIMER,
        "timestamp":              datetime.now(timezone.utc).isoformat(),
        "source_trade_id":        trade_event.get("id", ""),
    }

    log.info(
        f"[{alert_tier}] {payload['trader_handle']} | "
        f"${usd_value:,.0f} | {trade_event.get('market_title','?')} | "
        f"Copy: {'✅' if copy_trade_recommended else '⛔'}"
    )
    return payload


# ── Test Fixture ──────────────────────────────────────────────────────────────
TEST_TRADE = {
    "id":             "test-trade-001",
    "market_id":      "0xabc123",
    "market_title":   "Will the Fed cut rates in March 2026?",
    "outcome":        "Yes",
    "price":          0.72,
    "size":           69444.44,
    "usd_value":      50000.00,
    "maker_address":  "0xDeAdBeEf1234567890abcdef",
    "taker_address":  "0x0000000000000000000000000",
    "side":           "BUY",
    "timestamp":      datetime.now(timezone.utc).isoformat(),
}

TEST_PROFILE = {
    "wallet_address":   "0xDeAdBeEf1234567890abcdef",
    "handle":           "The Oracle of Oregon",
    "total_trades":     142,
    "total_volume_usd": 2_400_000.00,
    "win_rate":         0.73,
    "roi_30d":          0.18,
    "roi_all_time":     0.54,
    "dominant_category": "US Politics",
}


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Signal Engine")
    parser.add_argument("--test", action="store_true", help="Run with fixture data")
    args = parser.parse_args()

    if args.test:
        print("🧪 Running signal engine with test fixture...\n")
        result = build_alert(TEST_TRADE, TEST_PROFILE)
        if result:
            print(json.dumps(result, indent=2))
            # Validate non-negotiable rules
            assert result["disclaimer"] == DISCLAIMER, "Disclaimer missing!"
            assert result["copy_trade_recommended"] == (TEST_PROFILE["win_rate"] >= COPY_TRADE_MIN_WR)
            print("\n✅ All assertions passed.")
        else:
            print("❌ No alert produced.")
    else:
        # Read TradeEvent from stdin, profile from second line (optional)
        raw = sys.stdin.read().strip()
        lines = raw.split("\n", 1)
        trade = json.loads(lines[0])
        profile = json.loads(lines[1]) if len(lines) > 1 else None
        result = build_alert(trade, profile)
        if result:
            print(json.dumps(result))
