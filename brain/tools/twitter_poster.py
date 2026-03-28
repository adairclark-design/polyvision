#!/usr/bin/env python3
"""
twitter_poster.py — PolyVision Layer 3 Tool

Auto-posts whale trade alerts to @PolyVisionApp on X (Twitter) using
the Twitter API v2 Free tier (1,500 tweets/month, $0 cost).

Authentication: OAuth 1.0a (tweepy)
Required env vars:
    TWITTER_API_KEY             → App API Key (Consumer Key)
    TWITTER_API_KEY_SECRET      → App API Key Secret (Consumer Secret)
    TWITTER_ACCESS_TOKEN        → Account Access Token
    TWITTER_ACCESS_TOKEN_SECRET → Account Access Token Secret
    TWITTER_MIN_SIZE            → Min USD for Polymarket trades (default: 50000)
    TWITTER_KALSHI_MIN_SIZE     → Min USD for Kalshi trades (default: 5000)

Usage:
    python tools/twitter_poster.py --test          # dry-run with fixture payload
    python tools/twitter_poster.py --dry-run       # print tweet, no post
    python tools/twitter_poster.py < alert.json    # post from stdin payload

Self-annealing log:
    2026-03-26: Initial implementation. Uses tweepy v4 Client (API v2).
                OAuth 1.0a required for posting (Bearer token is read-only).
                Redis deduplication prevents repeat tweets for same market
                within TWEET_DEDUP_TTL seconds (default 15 min).
"""

import os
import sys
import json
import logging
import argparse
import hashlib
import random

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TWITTER_API_KEY             = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_KEY_SECRET      = os.getenv("TWITTER_API_KEY_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
TWITTER_MIN_SIZE            = float(os.getenv("TWITTER_MIN_SIZE", "50000"))
TWITTER_KALSHI_MIN_SIZE     = float(os.getenv("TWITTER_KALSHI_MIN_SIZE", "5000"))
REDIS_URL                   = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TWEET_DEDUP_TTL             = 900   # 15 minutes: skip repeat tweets for same market

# ── Startup credential audit (INFO level — visible in Railway logs) ────────────
log.info("[Twitter] ENV audit → "
         f"API_KEY={'SET' if TWITTER_API_KEY else 'MISSING'} | "
         f"API_KEY_SECRET={'SET' if TWITTER_API_KEY_SECRET else 'MISSING'} | "
         f"ACCESS_TOKEN={'SET' if TWITTER_ACCESS_TOKEN else 'MISSING'} | "
         f"ACCESS_TOKEN_SECRET={'SET' if TWITTER_ACCESS_TOKEN_SECRET else 'MISSING'} | "
         f"MIN_SIZE_POLY=${TWITTER_MIN_SIZE:,.0f} | "
         f"MIN_SIZE_KALSHI=${TWITTER_KALSHI_MIN_SIZE:,.0f}")


# ── Credentials check ─────────────────────────────────────────────────────────
def _credentials_set() -> bool:
    return all([
        TWITTER_API_KEY,
        TWITTER_API_KEY_SECRET,
        TWITTER_ACCESS_TOKEN,
        TWITTER_ACCESS_TOKEN_SECRET,
    ])


# ── Tweet Formatter ───────────────────────────────────────────────────────────
def format_tweet(payload: dict) -> str:
    """
    Build a dynamic, human-sounding tweet string (≤280 chars) from a WhaleAlertPayload.
    """
    usd_value   = float(payload.get("usd_value", 0))
    outcome     = payload.get("outcome", "YES")
    market      = payload.get("market_title", "an undisclosed market")
    price       = float(payload.get("price", 0.5))
    source      = payload.get("source", "POLYMARKET").upper()
    tier        = payload.get("alert_tier", "STANDARD")

    # Platform label + hashtag
    if source == "KALSHI":
        platform   = "Kalshi"
        hashtags   = "#PredictionMarkets #Kalshi"
    else:
        platform   = "Polymarket"
        hashtags   = "#PredictionMarkets #Polymarket"

    # Whale emoji for big trades
    emoji = "🐋" if tier == "WHALE" or usd_value >= 50_000 else "🔵"

    # Price as percentage
    pct = f"{price:.0%}"
    
    # Format dollars nicely
    usd_str = f"${usd_value:,.0f}"

    templates = [
        "A trader just made a {usd} bet on {outcome} for '{market}' at {pct} to win on {platform}! What do they know that we don't? Want to get notified whenever a whale enters the market? Try out PolyVision, and follow the money! {emoji}👇\n\npolyvision.app\n\n{hashtags}",
        "🚨 Whale Alert: Someone just dropped {usd} on {outcome} for '{market}' ({pct} probability) via {platform}. Are they hedging or do they have inside info? Catch moves like this in real-time with PolyVision before the market reacts ⚡️\n\npolyvision.app\n\n{hashtags}",
        "Millions are moving on {platform}... A {usd} position was just taken on '{market}' ({outcome} @ {pct}). Don't trade blind—see exactly what the smart money is doing. Track every whale live on PolyVision 🎯\n\npolyvision.app\n\n{hashtags}",
        "Just in: massive {usd} play on '{market}' betting {outcome} ({pct}). Is smart money leading the charge? Don't miss the next big shift on {platform}. Follow the whales and trade smarter with PolyVision. 🌊\n\npolyvision.app\n\n{hashtags}",
        "🔥 Huge move on {platform}! A whale just bet {usd} that {outcome} happens for '{market}', buying in at {pct}. Want to know the second these trades happen? PolyVision gets you real-time alerts so you're never late.\n\npolyvision.app\n\n{hashtags}"
    ]

    template = random.choice(templates)
    
    # Calculate how much space the template uses WITHOUT the market title
    template_blank = template.format(
        usd=usd_str, outcome=outcome, market="", pct=pct, 
        platform=platform, emoji=emoji, hashtags=hashtags
    )
    
    # Twitter hard limit is 280
    chars_left = 280 - len(template_blank)
    
    # Truncate market if necessary
    if len(market) > chars_left:
        safe_market = market[:max(0, chars_left - 1)] + "…"
    else:
        safe_market = market

    tweet = template.format(
        usd=usd_str, outcome=outcome, market=safe_market, pct=pct, 
        platform=platform, emoji=emoji, hashtags=hashtags
    )

    return tweet


# ── Redis Deduplication ───────────────────────────────────────────────────────
def _is_duplicate(market_id: str, source: str) -> bool:
    """Returns True if we already tweeted this market recently."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        key = f"tweet:sent:{source}:{market_id}"
        if r.get(key):
            log.info(f"[Twitter] Dedup skip: already tweeted {source}:{market_id}")
            return True
        r.setex(key, TWEET_DEDUP_TTL, "1")
        return False
    except Exception as e:
        log.warning(f"[Twitter] Redis dedup check failed (allowing through): {e}")
        return False


# ── Poster ────────────────────────────────────────────────────────────────────
def post_tweet(tweet_text: str) -> str:
    """Post a tweet via tweepy v4 API v2. Returns the tweet ID."""
    try:
        import tweepy
    except ImportError:
        raise RuntimeError(
            "tweepy is not installed. Run: pip install tweepy>=4.14.0,<5.0.0"
        )

    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_KEY_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )
    response = client.create_tweet(text=tweet_text)
    tweet_id = response.data["id"]
    log.info(f"[Twitter] ✅ Tweeted: https://twitter.com/i/web/status/{tweet_id}")
    return tweet_id


# ── Main Entry ────────────────────────────────────────────────────────────────
def maybe_tweet(payload: dict, dry_run: bool = False) -> dict:
    """
    Check thresholds and post a tweet if the trade qualifies.
    Returns a receipt dict with status and any tweet_id.

    Called from notifier.py deliver() for every qualifying trade.
    """
    usd_value = float(payload.get("usd_value", 0))
    source    = payload.get("source", "POLYMARKET").upper()
    market_id = payload.get("market_id", "unknown")

    # Threshold gate
    threshold = TWITTER_KALSHI_MIN_SIZE if source == "KALSHI" else TWITTER_MIN_SIZE
    if usd_value < threshold:
        log.info(
            f"[Twitter] Skipped ({source}): ${usd_value:,.0f} < ${threshold:,.0f} threshold"
        )
        return {"status": "skipped_below_threshold", "threshold": threshold}

    log.info(f"[Twitter] Trade qualifies ({source}): ${usd_value:,.0f} >= ${threshold:,.0f} — proceeding to post")

    tweet_text = format_tweet(payload)

    # Dry-run: always show the formatted tweet, no credentials needed
    if dry_run:
        print(f"\n── 🐦 Twitter (would post) ──────────────────────")
        print(tweet_text)
        print(f"  [{len(tweet_text)}/280 chars]")
        return {"status": "dry_run", "tweet": tweet_text}

    # Credentials check (production only)
    if not _credentials_set():
        log.warning("[Twitter] Credentials not set — skipping. Check TWITTER_API_KEY / TWITTER_ACCESS_TOKEN env vars on Railway.")
        return {"status": "skipped_no_credentials"}

    # Deduplication check
    if _is_duplicate(market_id, source):
        return {"status": "skipped_duplicate"}

    try:
        tweet_id = post_tweet(tweet_text)
        return {"status": "posted", "tweet_id": tweet_id, "tweet": tweet_text}
    except Exception as e:
        log.error(f"[Twitter] Post failed: {e}")
        return {"status": "failed", "error": str(e)}


# ── Test Fixture ──────────────────────────────────────────────────────────────
TEST_PAYLOAD = {
    "alert_tier":    "WHALE",
    "market_title":  "Will the Fed cut rates in June 2026?",
    "market_id":     "0xfed-rate-june-2026",
    "outcome":       "YES",
    "price":         0.72,
    "usd_value":     127_000.00,
    "source":        "POLYMARKET",
    "trader_handle": "The Oracle of Oregon",
}

TEST_PAYLOAD_KALSHI = {
    "alert_tier":    "WHALE",
    "market_title":  "Will Trump be indicted before January 2027?",
    "market_id":     "TRUMP-INDICT-2027",
    "outcome":       "NO",
    "price":         0.35,
    "usd_value":     8_500.00,
    "source":        "KALSHI",
    "trader_handle": "KalshiWhale",
}


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [twitter] %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="PolyVision Twitter Poster")
    parser.add_argument("--test",    action="store_true", help="Dry-run with fixture payload (Polymarket + Kalshi)")
    parser.add_argument("--dry-run", action="store_true", help="Format tweet from stdin, no post")
    args = parser.parse_args()

    if args.test:
        print("🧪 Twitter poster test (dry-run only — no actual tweet):\n")
        for label, fixture in [("Polymarket WHALE", TEST_PAYLOAD), ("Kalshi WHALE", TEST_PAYLOAD_KALSHI)]:
            print(f"── {label} ──────────────────")
            result = maybe_tweet(fixture, dry_run=True)
            print(f"Status: {result['status']}\n")
        sys.exit(0)

    raw = sys.stdin.read().strip()
    payload = json.loads(raw)
    dry = args.dry_run
    result = maybe_tweet(payload, dry_run=dry)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("posted", "dry_run", "skipped_duplicate",
                                        "skipped_below_threshold", "skipped_no_credentials") else 1)
