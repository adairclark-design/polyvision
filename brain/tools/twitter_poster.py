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
TWEET_DEDUP_TTL             = 3600  # 1 hour: prevent identical trade from being tweeted twice

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
    Build a dynamic, human-sounding tweet string (≤280 chars) from a WhaleAlertPayload
    using OpenAI to generate Visceral Copy, Pattern Interruption, and Tag & Bait strategy.
    Falls back to a static template if OpenAI fails or is not configured.
    """
    usd_value   = float(payload.get("usd_value", 0))
    outcome     = payload.get("outcome", "YES")
    market      = payload.get("market_title", "an undisclosed market")
    price       = float(payload.get("price", 0.5))
    source      = payload.get("source", "POLYMARKET").upper()
    tier        = payload.get("alert_tier", "STANDARD")

    if source == "KALSHI":
        platform   = "Kalshi"
        hashtags   = "#PredictionMarkets"
        tags       = "the Kalshi markets"
    else:
        platform   = "Polymarket"
        hashtags   = "#PredictionMarkets"
        tags       = "the Polymarket ecosystem"

    emoji = "🐋" if tier == "WHALE" or usd_value >= 50_000 else "🔵"

    if price >= 0.995:
        pct = "99%+"
    elif price <= 0.005:
        pct = "<1%"
    else:
        pct = f"{price:.0%}"
    
    usd_str = f"${usd_value:,.0f}"

    # ── 6 Adversarial Static Fallbacks ──────────────────────────────────────────
    # Used when OpenAI is unavailable. Each is pre-written in the adversarial
    # 'Smart Money vs Retail' voice so the fallback never looks like a boring alert.
    ADVERSARIAL_FALLBACKS = [
        (
            f"{emoji} While retail traders were distracted by headlines, smart money quietly dropped "
            f"{usd_str} on {outcome} for '{market}'. The market moves before you even hear about it.\n\n"
            f"Track the whales before they disappear: polyvision.app\n\n{hashtags}"
        ),
        (
            f"{emoji} {usd_str} on {outcome}. {platform} whale. {pct} conviction. "
            f"Retail is always last to know — that's why they're always last to profit.\n\n"
            f"Stop trading blind: polyvision.app\n\n{hashtags}"
        ),
        (
            f"{emoji} Someone just deployed {usd_str} on {outcome} for '{market}' at {pct}. "
            f"This isn't a rumor. This is a real position. The exit liquidity is whoever doesn't see it.\n\n"
            f"polyvision.app\n\n{hashtags}"
        ),
        (
            f"{emoji} A {platform} whale just put {usd_str} on '{market}' going {outcome}. "
            f"Meanwhile, the mainstream is debating yesterday's news. "
            f"Smart money doesn't wait for permission.\n\npolyvision.app\n\n{hashtags}"
        ),
        (
            f"{emoji} {usd_str} position just opened on {outcome} @ {pct} in '{market}'. "
            f"The whales aren't confused. Are you? "
            f"Follow the money, not the noise: polyvision.app\n\n{hashtags}"
        ),
        (
            f"{emoji} Insider or genius? A whale just bet {usd_str} on {outcome} for '{market}'. "
            f"The market will answer that question eventually. "
            f"Be ready before it does: polyvision.app\n\n{hashtags}"
        ),
    ]
    fallback_tweet = random.choice(ADVERSARIAL_FALLBACKS)
    if len(fallback_tweet) > 280:
        fallback_tweet = fallback_tweet[:276] + "..."

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    if not OPENAI_API_KEY:
        log.warning("[Twitter] OPENAI_API_KEY not set. Using adversarial fallback template.")
        return fallback_tweet

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        hook_types = [
            "Retail Exit Liquidity (Frame retail traders as the suckers being left holding the bag while this whale quietly rotates against them)",
            "Insider Knowledge (Imply aggressively that this whale knows an industry secret that the general public and mainstream media do not)",
            "Contrarian Mockery (Mock the current mainstream news narrative explicitly using this massive trade as proof that the media is wrong)",
            "Market Predation (Suggest this whale is actively hunting and feasting on smaller retail traders who are trading purely on emotion)"
        ]
        chosen_hook = random.choice(hook_types)

        prompt = f"""
We are tweeting a whale alert for a prediction market tracking application called PolyVision.
Market: "{market}"
Position: {outcome} @ {pct} probability
Size: {usd_str}
Platform: {platform}

INSTRUCTIONS:
1. Write ONE highly engaging tweet (max 180 characters) about this trade.
2. Follow the Marketing Growth Skill principles: Frame the data as a highly aggressive Us vs Them argument ('Smart Money' vs 'Retail'). Use purely data-backed inflammatory takes. Let them know retail is about to get wiped out.
3. PATTERN INTERRUPTION: Strictly use this adversarial hook style for this tweet: [{chosen_hook}].
4. "TAG & BAIT" STRATEGY: Mention {tags} naturally to bait a reply or retweet.
5. Do NOT include hyperlinks or hashtags. Do NOT use emojis at the start.
6. Make it arrogant, urgent, and professional (A ruthless Wall Street predator's tone).
"""
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.7,
            timeout=10,
        )
        ai_text = resp.choices[0].message.content.strip().strip('"')
        
        # Assemble final tweet
        final_tweet = f"{emoji} {ai_text}\n\npolyvision.app\n\n{hashtags}"
        
        if len(final_tweet) > 280:
            log.warning("[Twitter] LLM generated too long tweet. Using fallback.")
            return fallback_tweet
            
        return final_tweet

    except Exception as e:
        log.error(f"[Twitter] LLM generation failed: {e}. Using fallback.")
        return fallback_tweet


# ── Redis Deduplication ───────────────────────────────────────────────────────
def _is_duplicate(trade_id: str, source: str) -> bool:
    """Returns True if we already tweeted this exact trade event recently.
    
    Key is based on the unique trade/transaction ID — NOT the market_id.
    This allows multiple different whale trades on the same market to all be
    posted, while still preventing the identical event from firing twice if
    it flows through the pipeline more than once (e.g. via WS + REST poll).
    """
    try:
        import redis as redis_lib
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        key = f"tweet:sent:{source}:{trade_id}"
        if r.get(key):
            log.info(f"[Twitter] Dedup skip: already tweeted trade {source}:{trade_id[:20]}")
            return True
        r.setex(key, TWEET_DEDUP_TTL, "1")
        return False
    except Exception as e:
        log.warning(f"[Twitter] Redis dedup check failed (allowing through): {e}")
        return False


# ── Poster ────────────────────────────────────────────────────────────────────
def post_tweet(tweet_text: str, payload: dict | None = None) -> str:
    """
    Post a tweet via tweepy v4 API v2 with an optional generated card image.
    Media is uploaded via tweepy API v1.1 (the only endpoint that supports media_upload).
    Returns the tweet ID.
    """
    try:
        import tweepy
    except ImportError:
        raise RuntimeError(
            "tweepy is not installed. Run: pip install tweepy>=4.14.0,<5.0.0"
        )

    # ── v1 API (media upload) ──────────────────────────────────────────────────
    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_KEY_SECRET,
        TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET,
    )
    api_v1 = tweepy.API(auth)

    # ── v2 Client (create_tweet) ───────────────────────────────────────────────
    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_KEY_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
    )

    # ── Generate and upload card image ─────────────────────────────────────────
    media_ids = None
    if payload is not None:
        try:
            import sys, os
            # Allow import from sibling directory (tools/) regardless of cwd
            tools_dir = os.path.dirname(os.path.abspath(__file__))
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)
            from card_generator import generate_card
            card_buf = generate_card(payload)
            media = api_v1.media_upload(filename="card.png", file=card_buf)
            media_ids = [media.media_id]
            log.info(f"[Twitter] Card uploaded: media_id={media.media_id}")
        except Exception as e:
            log.warning(f"[Twitter] Card generation/upload failed (posting without image): {e}")

    # ── Post tweet ─────────────────────────────────────────────────────────────
    kwargs = {"text": tweet_text}
    if media_ids:
        kwargs["media_ids"] = media_ids

    response = client.create_tweet(**kwargs)
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

    # Deduplication check — keyed on the unique trade ID, not market_id
    trade_id = payload.get("source_trade_id") or payload.get("alert_id") or market_id
    if _is_duplicate(trade_id, source):
        return {"status": "skipped_duplicate"}

    try:
        tweet_id = post_tweet(tweet_text, payload=payload)
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
    "price":         1.00,
    "usd_value":     127_000.00,
    "source":        "POLYMARKET",
    "trader_handle": "GamblingIsAllYouNeed",
    "wallet_win_rate": 0.58,
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
