#!/usr/bin/env python3
"""
notifier.py — PolyVision Layer 3 Tool
Routes a finalized WhaleAlertPayload to push notifications (OneSignal),
Discord webhook embed, Telegram bot message, and Twitter/X post.

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
ONESIGNAL_APP_ID   = os.getenv("ONESIGNAL_APP_ID", "")
ONESIGNAL_API_KEY  = os.getenv("ONESIGNAL_API_KEY", "")
DISCORD_WEBHOOK_URL= os.getenv("DISCORD_WEBHOOK_URL", "")   # main whale-alerts channel
DISCORD_WHALE_WEBHOOK_URL = os.getenv("DISCORD_WHALE_WEBHOOK_URL", "")  # premium channel for WHALE-tier only
DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN", "")    # fallback: bot token
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")

# ── Multi-tier Discord channel routing ────────────────────────────────────────
# Each tier is a (webhook_url, min_usd_threshold) pair.
# Trades are routed by SOURCE: POLYMARKET → POLY tiers, KALSHI → KALSHI tiers.
# Configure all webhook URLs via Railway env vars — no code changes needed.
#
# POLYMARKET channels (rename in Discord: poly-standard / poly-whale / poly-mega)
DISCORD_POLY_TIERS = [
    url_thresh for url_thresh in [
        (os.getenv("DISCORD_WEBHOOK_URL",      ""),  float(os.getenv("DISCORD_MIN_SIZE",         "500"))),
        (os.getenv("DISCORD_WEBHOOK_50K_URL",  ""),  50_000.0),
        (os.getenv("DISCORD_WEBHOOK_100K_URL", ""), 100_000.0),
    ]
    if url_thresh[0]  # only include tiers where the webhook URL is set
]

# KALSHI channels (new) — lower thresholds: Kalshi positions are typically smaller
# Add webhooks via new Railway vars: DISCORD_KALSHI_WEBHOOK_URL / _1K_URL / _5K_URL
DISCORD_KALSHI_TIERS = [
    url_thresh for url_thresh in [
        (os.getenv("DISCORD_KALSHI_WEBHOOK_URL", ""),  float(os.getenv("DISCORD_KALSHI_MIN_SIZE", "200"))),
        (os.getenv("DISCORD_KALSHI_1K_URL",     ""),   1_000.0),
        (os.getenv("DISCORD_KALSHI_5K_URL",     ""),   5_000.0),
    ]
    if url_thresh[0]
]

# Backward-compat alias (used by dry-run path)
DISCORD_TIERS = DISCORD_POLY_TIERS
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

RESEND_API_KEY             = os.getenv("RESEND_API_KEY", "")
REDDIT_EMAIL_TO            = os.getenv("REDDIT_EMAIL_TO", "adair.clark@gmail.com")
REDDIT_MIN_SIZE            = float(os.getenv("REDDIT_MIN_SIZE", "100000"))
REDDIT_KALSHI_MIN_SIZE     = float(os.getenv("REDDIT_KALSHI_MIN_SIZE", "25000"))
REDIS_URL          = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Logging (must be initialized before any log.* calls below) ────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [notifier] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(".tmp/notifier.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ── Twitter/X auto-posting ────────────────────────────────────────────────────
# IMPORTANT: notifier.py lives in tools/ but is imported from brain/ by main.py.
# A bare 'from twitter_poster import ...' fails silently because brain/ is the
# cwd, not brain/tools/. We fix this by injecting tools/ into sys.path first.
try:
    import sys as _sys
    _tools_dir = os.path.dirname(os.path.abspath(__file__))
    if _tools_dir not in _sys.path:
        _sys.path.insert(0, _tools_dir)
    from twitter_poster import maybe_tweet as _maybe_tweet
    TWITTER_ENABLED = True
    log.info("[Twitter] twitter_poster loaded — TWITTER_ENABLED=True")
except ImportError as _e:
    TWITTER_ENABLED = False
    _maybe_tweet = None
    log.warning(f"[Twitter] twitter_poster import failed — tweets disabled: {_e}")


# ── Reddit/Discord Silver Platter auto-post ───────────────────────────────────
try:
    import sys as _sys
    _tools_dir = os.path.dirname(os.path.abspath(__file__))
    if _tools_dir not in _sys.path:
        _sys.path.insert(0, _tools_dir)
    from reddit_engine import generate_reddit_package as _generate_reddit_package
    from card_generator import generate_card as _generate_card
    REDDIT_ENABLED = True
    log.info("[Reddit] reddit_engine loaded — REDDIT_ENABLED=True")
except ImportError as _e:
    REDDIT_ENABLED = False
    _generate_reddit_package = None
    _generate_card = None
    log.warning(f"[Reddit] reddit_engine import failed: {_e}")

RATE_LIMIT_WHALE_TTL    = 300   # 5 minutes: one WHALE alert per market
RATE_LIMIT_STANDARD_MAX = 10    # max STANDARD alerts per hour
RATE_LIMIT_STANDARD_TTL = 3600



# ── Rate Limiting ─────────────────────────────────────────────────────────────
def check_rate_limit(payload: dict, dry_run: bool = False) -> bool:
    """Returns True if alert may be sent, False if rate-limited.

    Rate limit strategy:
      WHALE tier  — 1 alert per market per 5 minutes (deduplicates same-market clusters)
      STANDARD    — 1 alert per wallet per 10 minutes (prevents single-whale spam;
                    each wallet has its OWN counter so one busy whale can't silence others)
    """
    if dry_run:
        return True
    try:
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        tier      = payload.get("alert_tier", "STANDARD")
        market_id = payload.get("market_id", "unknown")
        wallet    = payload.get("wallet_address", payload.get("maker_address", "unknown"))

        if tier == "WHALE":
            key = f"alert:sent:{market_id}"
            if r.get(key):
                log.info(f"Rate limited (WHALE, 5min): market {market_id}")
                return False
            r.setex(key, RATE_LIMIT_WHALE_TTL, "1")
        else:
            # Per-wallet cooldown — each wallet gets its own 10-minute window
            wallet_key = f"alert:wallet:{wallet[:20]}"
            if r.get(wallet_key):
                log.info(f"Rate limited (STANDARD, 10min cooldown): wallet {wallet[:10]}")
                return False
            r.setex(wallet_key, 600, "1")   # 10-minute per-wallet cooldown
        return True
    except Exception as e:
        log.warning(f"Rate limit check failed (allowing through): {e}")
        return True



# ── Formatters ────────────────────────────────────────────────────────────────
def _fmt_price(price) -> str:
    """
    Convert a Polymarket price (0–1 float probability) to a display string.
    $1.00 = 100% certainty is confusing — show as % instead.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "N/A"
    if p <= 0:
        return "N/A"
    if p >= 0.99:
        return "~100%"
    if p <= 0.01:
        return "<1%"
    return f"{p:.0%}"


def _fmt_win_rate(wr, total_trades=None) -> str:
    """
    Format a win rate with proper TBD handling.
    Shows TBD when:
      - No data (None)
      - win_rate is 0.0 with fewer than 5 trades (indistinguishable from no data)
    Shows 0% only when there are ≥5 trades to make it meaningful.
    """
    if wr is None:
        return "TBD"
    if wr == 0.0:
        try:
            trades = int(total_trades or 0)
        except (TypeError, ValueError):
            trades = 0
        return "0%" if trades >= 5 else "TBD"
    return f"{wr:.0%}"

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
    trades  = payload.get("wallet_total_trades")
    copy    = payload.get("copy_trade_recommended", False)
    summary = payload.get("ai_summary", "")
    source  = payload.get("source", "POLYMARKET").upper()

    # Platform badge — shown in footer
    platform_label = "◎ Polymarket" if source == "POLYMARKET" else "⚡ Kalshi"

    # Green for YES, Red for NO
    color = 0x00C851 if str(outcome).lower() == "yes" else 0xFF4444
    wr_str = _fmt_win_rate(wr, trades)

    return {
        "embeds": [{
            "title":       f"{'🐋' if tier == 'WHALE' else '🔵'} {handle} — {market}",
            "color":       color,
            "description": summary,
            "fields": [
                {"name": "Platform",   "value": platform_label,                               "inline": True},
                {"name": "Position",   "value": f"{outcome} @ {_fmt_price(price)}",           "inline": True},
                {"name": "Size",       "value": f"${usd:,.0f} USD",                          "inline": True},
                {"name": "Win Rate",   "value": wr_str,                                       "inline": True},
                {"name": "Copy Trade", "value": "✅ Recommended" if copy else "⛔ Not Recommended", "inline": True},
            ],
            "footer": {"text": f"{platform_label} · ⚠️ Whales can hedge. Trade at your own risk."},
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
    trades  = payload.get("wallet_total_trades")
    copy    = payload.get("copy_trade_recommended", False)
    summary = payload.get("ai_summary", "")

    emoji  = "🐋" if tier == "WHALE" else "🔵"
    wr_str = _fmt_win_rate(wr, trades)
    copy_str = "✅ Recommended" if copy else "⛔ Not Recommended"

    return (
        f"{emoji} *{handle}* — {tier} ALERT\n"
        f"📊 Market: {market}\n"
        f"🎯 Position: {outcome} @ {_fmt_price(price)}\n"
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
            "included_segments": ["Subscribed Users"],  # "All" is not always a valid segment; use the default
            "headings":          {"en": push["title"]},
            "contents":          {"en": push["body"]},
        },
        timeout=10,
    )
    # Log the response body on any error so we can see what OneSignal actually says
    if not r.ok:
        body = r.text[:500]
        # OneSignal returns 400 when no subscribers have enabled push yet —
        # this is expected for a new app and shouldn't spam ERROR logs
        if r.status_code == 400 and ("All included players are not subscribed" in body
                                      or "No subscribers" in body
                                      or "errors" in body.lower()):
            log.info(f"OneSignal: no active subscribers yet (this is normal for a new app). Response: {body}")
            return   # don't raise — treat as a soft skip, not a failure
        log.error(f"OneSignal error {r.status_code}: {body}")
        r.raise_for_status()



def send_discord(embed: dict, webhook_override: str = ""):
    """Send to Discord via webhook URL (preferred) or bot token.
    webhook_override allows posting to a secondary channel (e.g. premium whale alerts).
    """
    url = webhook_override or DISCORD_WEBHOOK_URL
    if url:
        # Wrap embed in a payload that overrides the bot display name + avatar
        payload = {
            "username":   "PolyVision Brain",
            "avatar_url": "https://polyvision.app/assets/icon-192.png",
            "embeds":     embed.get("embeds", [embed]) if "embeds" not in embed else embed["embeds"],
        }
        # Pass through any top-level content field (e.g. @here mentions)
        if "content" in embed:
            payload["content"] = embed["content"]
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        raise ValueError("Neither DISCORD_WEBHOOK_URL nor DISCORD_BOT_TOKEN set.")
    r = requests.post(
        f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
        json=embed,
        timeout=10,
    )
    r.raise_for_status()



import base64

def send_reddit_email(pkg: dict, img_buf):
    if not RESEND_API_KEY or not REDDIT_EMAIL_TO:
        return
        
    sub = pkg.get('subreddit', 'r/Polymarket')
    title = pkg.get('title', '')
    comment = pkg.get('comment', '')
    
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #111827;">
        <h2 style="color: #0057ff; margin-bottom: 24px;">🥈 Reddit Silver Platter Package</h2>
        <p><strong>Target Subreddit:</strong> <a href="https://reddit.com/{sub}">{sub}</a></p>
        
        <p style="margin-top:20px;"><strong>Title:</strong></p>
        <div style="background: #f3f4f6; padding: 16px; border-radius: 8px; border: 1px solid #e5e7eb; margin-bottom: 24px; font-size: 16px; font-weight: 500;">
            {title}
        </div>
        
        <p><strong>Comment:</strong></p>
        <div style="background: #f3f4f6; padding: 16px; border-radius: 8px; border: 1px solid #e5e7eb; margin-bottom: 24px; font-size: 15px; line-height: 1.5;">
            {comment}
        </div>
        
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;" />
        <p style="font-size: 13px; color: #6b7280;">Attached below is the full-res Trade Card. Save it to your camera roll and upload directly to Reddit.</p>
    </div>
    """
    
    img_b64 = base64.b64encode(img_buf.getvalue()).decode('utf-8')
    
    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "from":    "PolyVision Alerts <alerts@polyvision.app>",
            "to":      [REDDIT_EMAIL_TO],
            "subject": f"Reddit Package: {title[:40]}...",
            "html":    html,
            "attachments": [{
                "filename": "card.png",
                "content": img_b64
            }]
        },
        timeout=15
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
    """Route payload to all channels. Returns delivery receipt.

    Channel routing:
      - Live feed WebSocket: ALL trades (handled upstream, not here)
      - Email alerts:        Per-user rules with custom min_size (handled upstream)
      - OneSignal push:      ALL trades that pass rate limit (targeted whale follows handled upstream)
      - Discord:             Only trades >= DISCORD_MIN_SIZE (default $5,000)
                             WHALE-tier also posts to DISCORD_WHALE_WEBHOOK_URL if set
      - Telegram:            Only trades >= DISCORD_MIN_SIZE
    """
    os.makedirs(".tmp", exist_ok=True)

    if not check_rate_limit(payload, dry_run):
        return {"status": "rate_limited", "channels": {}}

    push    = format_push(payload)
    embed   = format_discord_embed(payload)
    tg_text = format_telegram(payload)

    # ── Discord / Telegram size gate ───────────────────────────────────────────
    usd_value  = float(payload.get("usd_value", 0))

    # ── Source-aware Discord tier routing ──────────────────────────────────────
    source = payload.get("source", "POLYMARKET").upper()
    if source == "KALSHI":
        active_tiers = DISCORD_KALSHI_TIERS
    else:
        active_tiers = DISCORD_POLY_TIERS

    qualifying_tiers = [(url, thr) for url, thr in active_tiers if usd_value >= thr]

    if not qualifying_tiers:
        log.info(
            f"Discord/Telegram skipped ({source}): ${usd_value:,.0f} below all configured thresholds "
            f"({[f'${t:,.0f}' for _, t in active_tiers]})"
        )

    if dry_run:
        print("\n── 📱 Push Notification ──────────────────────────")
        print(f"  Title: {push['title']}")
        print(f"  Body:  {push['body']}")
        for url, thr in DISCORD_TIERS:
            label = f"${thr:,.0f}+ channel"
            if usd_value >= thr:
                print(f"\n── 🎮 Discord ({label}) would POST ─")
                print(json.dumps(embed, indent=2))
            else:
                print(f"\n── 🎮 Discord ({label}): SKIPPED (${usd_value:,.0f} < ${thr:,.0f})")
        print("\n── 📨 Telegram Message ────────────────────────────")
        print(tg_text if qualifying_tiers else "SKIPPED")
        if TWITTER_ENABLED:
            _maybe_tweet(payload, dry_run=True)
            
        if REDDIT_ENABLED:
            reddit_thresh = REDDIT_KALSHI_MIN_SIZE if source == "KALSHI" else REDDIT_MIN_SIZE
            if usd_value >= reddit_thresh or payload.get("alert_tier") == "CLUSTER":
                print(f"\n── 🥈 Reddit Silver Platter (would generate) ─────")
                pkg = _generate_reddit_package(payload)
                print(json.dumps(pkg, indent=2))
        
        return {"status": "dry_run", "channels": {}}

    results = {
        "push": send_with_retry("OneSignal Push", lambda: send_onesignal(push)),
    }

    # Post to each qualifying Discord channel
    for i, (wh_url, threshold) in enumerate(qualifying_tiers):
        label = f"discord_{i}_${threshold:,.0f}+"
        captured_url = wh_url          # avoid closure-capture bug in lambda loop
        results[label] = send_with_retry(
            f"Discord (${threshold:,.0f}+ channel)",
            lambda u=captured_url: send_discord(embed, webhook_override=u),
        )
        log.info(f"Discord posted to ${threshold:,.0f}+ channel: ${usd_value:,.0f} trade")

    # Telegram mirrors the most-inclusive Discord tier
    if qualifying_tiers:
        results["telegram"] = send_with_retry("Telegram", lambda: send_telegram(tg_text))
    else:
        results["telegram"] = "skipped_min_size"

    # ── Reddit Silver Platter ──────────────────────────────────────────────────
    if REDDIT_ENABLED and RESEND_API_KEY and REDDIT_EMAIL_TO:
        reddit_thresh = REDDIT_KALSHI_MIN_SIZE if source == "KALSHI" else REDDIT_MIN_SIZE
        if usd_value >= reddit_thresh or alert_tier == "CLUSTER":
            try:
                log.info(f"Generating Reddit package for ${usd_value:,.0f} trade...")
                r_pkg = _generate_reddit_package(payload)
                r_img = _generate_card(payload)
                if r_img:
                    send_with_retry("Reddit Email", lambda: send_reddit_email(r_pkg, r_img))
                    results["reddit"] = "delivered"
            except Exception as e:
                log.error(f"[Reddit] Engine error: {e}")
                results["reddit"] = "failed"


    # ── Twitter/X auto-post ────────────────────────────────────────────────────
    if TWITTER_ENABLED:
        try:
            tw_result = _maybe_tweet(payload, dry_run=False)
            results["twitter"] = tw_result.get("status", "unknown")
            if tw_result.get("tweet_id"):
                log.info(f"[Twitter] Posted tweet ID {tw_result['tweet_id']}")
        except Exception as e:
            log.error(f"[Twitter] Post error: {e}")
            results["twitter"] = "failed"
    else:
        results["twitter"] = "skipped_no_module"

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
