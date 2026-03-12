#!/usr/bin/env python3
"""
notifier.py — PolyVision Layer 3 Tool
Routes a finalized WhaleAlertPayload to push notifications (OneSignal).

Architecture SOP: architecture/05_notification_delivery.md
Usage:
    python tools/notifier.py --dry-run          # print output, no HTTP calls
    python tools/notifier.py --test             # dry-run with fixture payload
    python tools/notifier.py < alert.json       # pipe a finalized payload
"""

import os
import sys
import json
import time
import logging
import argparse
import requests
import redis as redis_lib
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
ONESIGNAL_APP_ID  = os.getenv("ONESIGNAL_APP_ID", "")
ONESIGNAL_API_KEY = os.getenv("ONESIGNAL_API_KEY", "")
REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379/0")

RATE_LIMIT_WHALE_TTL    = 300   # 5 minutes: one WHALE alert per market
RATE_LIMIT_STANDARD_MAX = 10    # max STANDARD alerts per hour
RATE_LIMIT_STANDARD_TTL = 3600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [notifier] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(".tmp/notifier.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)


# ── Rate Limiting ─────────────────────────────────────────────────────────────
def check_rate_limit(payload: dict, dry_run: bool = False) -> bool:
    """Returns True if alert may be sent, False if rate-limited."""
    if dry_run:
        return True
    try:
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        tier      = payload.get("alert_tier", "STANDARD")
        market_id = payload.get("market_id", "unknown")

        if tier == "WHALE":
            key = f"alert:sent:{market_id}"
            if r.get(key):
                log.info(f"Rate limited (WHALE, 5min): market {market_id}")
                return False
            r.setex(key, RATE_LIMIT_WHALE_TTL, "1")
        else:
            count_key = "alerts:standard:count"
            count = int(r.get(count_key) or 0)
            if count >= RATE_LIMIT_STANDARD_MAX:
                log.info("Rate limited (STANDARD, hourly cap reached)")
                return False
            pipe = r.pipeline()
            pipe.incr(count_key)
            pipe.expire(count_key, RATE_LIMIT_STANDARD_TTL)
            pipe.execute()
        return True
    except Exception as e:
        log.warning(f"Rate limit check failed (allowing through): {e}")
        return True


# ── Formatters ────────────────────────────────────────────────────────────────
def format_push(payload: dict) -> dict:
    tier    = payload.get("alert_tier", "STANDARD")
    handle  = payload.get("trader_handle", "Unknown Trader")
    market  = payload.get("market_title", "an undisclosed market")
    usd     = payload.get("usd_value", 0)
    summary = payload.get("ai_summary", "")
    emoji   = "🐋" if tier == "WHALE" else "🔵"

    return {
        "title": f"{emoji} {tier} ALERT",
        "body":  f'"{handle}" just took a ${usd:,.0f} position on "{market}". {summary}'[:500],
    }


# ── Senders ───────────────────────────────────────────────────────────────────
def send_with_retry(label: str, fn) -> bool:
    for attempt in range(2):
        try:
            fn()
            log.info(f"✅ {label} delivery succeeded.")
            return True
        except Exception as e:
            log.warning(f"{label} attempt {attempt+1} failed: {e}")
            if attempt == 0:
                time.sleep(3)
    log.error(f"❌ {label} delivery failed after 2 attempts.")
    return False


def send_onesignal(push: dict):
    if not ONESIGNAL_APP_ID or not ONESIGNAL_API_KEY:
        raise ValueError("OneSignal credentials not set.")
    r = requests.post(
        "https://onesignal.com/api/v1/notifications",
        headers={
            "Authorization": f"Basic {ONESIGNAL_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "app_id":            ONESIGNAL_APP_ID,
            "included_segments": ["All"],
            "headings":          {"en": push["title"]},
            "contents":          {"en": push["body"]},
        },
        timeout=10,
    )
    r.raise_for_status()


# ── Main Delivery ─────────────────────────────────────────────────────────────
def deliver(payload: dict, dry_run: bool = False) -> dict:
    """Route payload to all channels. Returns delivery receipt."""
    os.makedirs(".tmp", exist_ok=True)

    if not check_rate_limit(payload, dry_run):
        return {"status": "rate_limited", "channels": {}}

    push = format_push(payload)

    if dry_run:
        print("\n── 📱 Push Notification ──────────────────────────")
        print(f"  Title: {push['title']}")
        print(f"  Body:  {push['body']}")
        return {"status": "dry_run", "channels": {"push": "skipped"}}

    results = {
        "push": send_with_retry("OneSignal Push", lambda: send_onesignal(push)),
    }
    return {"status": "delivered", "channels": results}


# ── Test Fixture ──────────────────────────────────────────────────────────────
TEST_PAYLOAD = {
    "alert_id":               "test-alert-001",
    "alert_tier":             "WHALE",
    "trader_handle":          "The Oracle of Oregon",
    "wallet_address":         "0xDeAdBeEf1234567890abcdef",
    "market_title":           "Will the Fed cut rates in March 2026?",
    "market_id":              "0xabc123",
    "outcome":                "Yes",
    "price":                  0.72,
    "usd_value":              50000.00,
    "wallet_win_rate":        0.73,
    "wallet_roi_30d":         0.18,
    "copy_trade_recommended": True,
    "disclaimer":             "Whales can hedge. Following a trade is at your own risk.",
    "ai_summary":             (
        "The Oracle of Oregon has deployed $50,000 into the 'YES' side of the Fed rate "
        "cut market at $0.72, implying a 72% probability. Trade at your own risk."
    ),
}


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Notifier")
    parser.add_argument("--dry-run", action="store_true", help="Print output, no HTTP calls")
    parser.add_argument("--test",    action="store_true", help="Run with fixture + dry-run")
    args = parser.parse_args()

    if args.test:
        print("🧪 Running notifier with test fixture (dry-run)...\n")
        receipt = deliver(TEST_PAYLOAD, dry_run=True)
        print(f"\n✅ Receipt: {json.dumps(receipt, indent=2)}")
        sys.exit(0)

    if args.dry_run:
        raw = sys.stdin.read().strip()
        payload = json.loads(raw)
        receipt = deliver(payload, dry_run=True)
        print(json.dumps(receipt, indent=2))
        sys.exit(0)

    raw = sys.stdin.read().strip()
    payload = json.loads(raw)
    receipt = deliver(payload, dry_run=False)
    print(json.dumps(receipt, indent=2))
    if not all(receipt.get("channels", {}).values()):
        sys.exit(1)
