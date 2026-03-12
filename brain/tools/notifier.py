#!/usr/bin/env python3
"""
notifier.py — PolyVision Layer 3 Tool
Routes a finalized WhaleAlertPayload to push notifications (OneSignal),
Discord webhook embed, and Telegram bot message.

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
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID= os.getenv("DISCORD_CHANNEL_ID", "")
TELEGRAM_BOT_TOKEN= os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
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


def format_discord_embed(payload: dict) -> dict:
    tier    = payload.get("alert_tier", "STANDARD")
    handle  = payload.get("trader_handle", "Unknown")
    market  = payload.get("market_title", "")
    outcome = payload.get("outcome", "")
    price   = payload.get("price", 0)
    usd     = payload.get("usd_value", 0)
    wr      = payload.get("wallet_win_rate")
    copy    = payload.get("copy_trade_recommended", False)
    summary = payload.get("ai_summary", "")

    # Green for YES, Red for NO
    color = 0x00C851 if str(outcome).lower() == "yes" else 0xFF4444
    wr_str = f"{wr:.0%}" if wr is not None else "N/A"

    return {
        "embeds": [{
            "title":       f"{'🐋' if tier == 'WHALE' else '🔵'} {handle} — {market}",
            "color":       color,
            "description": summary,
            "fields": [
                {"name": "Position",    "value": f"{outcome} @ ${price:.2f}",      "inline": True},
                {"name": "Size",        "value": f"${usd:,.0f} USD",               "inline": True},
                {"name": "Win Rate",    "value": wr_str,                            "inline": True},
                {"name": "Copy Trade",  "value": "✅ Recommended" if copy else "⛔ Not Recommended", "inline": True},
            ],
            "footer": {"text": "⚠️ Whales can hedge. Trade at your own risk."},
        }]
    }


def format_telegram(payload: dict) -> str:
    tier    = payload.get("alert_tier", "STANDARD")
    handle  = payload.get("trader_handle", "Unknown")
    market  = payload.get("market_title", "")
    outcome = payload.get("outcome", "")
    price   = payload.get("price", 0)
    usd     = payload.get("usd_value", 0)
    wr      = payload.get("wallet_win_rate")
    copy    = payload.get("copy_trade_recommended", False)
    summary = payload.get("ai_summary", "")

    emoji  = "🐋" if tier == "WHALE" else "🔵"
    wr_str = f"{wr:.0%}" if wr is not None else "N/A"
    copy_str = "✅ Recommended" if copy else "⛔ Not Recommended"

    return (
        f"{emoji} *{handle}* — {tier} ALERT\n"
        f"📊 Market: {market}\n"
        f"🎯 Position: {outcome} @ ${price:.2f}\n"
        f"💰 Size: ${usd:,.0f}\n"
        f"📈 Win Rate: {wr_str}\n"
        f"📋 Copy Trade: {copy_str}\n"
        f"🤖 {summary}\n"
        f"⚠️ _Trade at your own risk._"
    )


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


def send_discord(embed: dict):
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        raise ValueError("Discord credentials not set.")
    r = requests.post(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
        json=embed,
        timeout=10,
    )
    r.raise_for_status()


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("Telegram credentials not set.")
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    r.raise_for_status()


# ── Main Delivery ─────────────────────────────────────────────────────────────
def deliver(payload: dict, dry_run: bool = False) -> dict:
    """Route payload to all channels. Returns delivery receipt."""
    os.makedirs(".tmp", exist_ok=True)

    if not check_rate_limit(payload, dry_run):
        return {"status": "rate_limited", "channels": {}}

    push    = format_push(payload)
    embed   = format_discord_embed(payload)
    tg_text = format_telegram(payload)

    if dry_run:
        print("\n── 📱 Push Notification ──────────────────────────")
        print(f"  Title: {push['title']}")
        print(f"  Body:  {push['body']}")
        print("\n── 🎮 Discord Embed ───────────────────────────────")
        print(json.dumps(embed, indent=2))
        print("\n── 📨 Telegram Message ────────────────────────────")
        print(tg_text)
        return {"status": "dry_run", "channels": {"push": "skipped", "discord": "skipped", "telegram": "skipped"}}

    results = {
        "push":     send_with_retry("OneSignal Push", lambda: send_onesignal(push)),
        "discord":  send_with_retry("Discord",        lambda: send_discord(embed)),
        "telegram": send_with_retry("Telegram",       lambda: send_telegram(tg_text)),
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
