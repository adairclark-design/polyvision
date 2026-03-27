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
    Build a tweet string (≤280 chars) from a WhaleAlertPayload.

    Format:
        🐋 $127,000 YES on "Will the Fed cut rates in June?"
        📊 72% probability · Polymarket

        Live whale tracking → polyvision.app

        #PredictionMarkets #Polymarket
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

    # Truncate market title to keep tweet under 280 chars
    # Base tweet without market title = ~130 chars, leaving ~130 for title
    max_title = 100
    if len(market) > max_title:
        market = market[:max_title - 1] + "…"

    tweet = (
        f"{emoji} ${usd_value:,.0f} {outcome} on \"{market}\"\n"
        f"📊 {pct} probability · {platform}\n"
        f"\n"
        f"Live whale tracking → polyvision.app\n"
        f"\n"
        f"{hashtags}"
    )

    # Final safety truncation (Twitter hard limit is 280)
    if len(tweet) > 280:
        tweet = tweet[:277] + "…"

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
        log.debug(
            f"[Twitter] Skipped ({source}): ${usd_value:,.0f} < ${threshold:,.0f} threshold"
        )
        return {"status": "skipped_below_threshold", "threshold": threshold}

    tweet_text = format_tweet(payload)

    # Dry-run: always show the formatted tweet, no credentials needed
    if dry_run:
        print(f"\n── 🐦 Twitter (would post) ──────────────────────")
        print(tweet_text)
        print(f"  [{len(tweet_text)}/280 chars]")
        return {"status": "dry_run", "tweet": tweet_text}

    # Credentials check (production only)
    if not _credentials_set():
        log.debug("[Twitter] Credentials not set — skipping.")
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
