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

    # Price as percentage (capped to prevent 100% display which looks artificial)
    if price >= 0.995:
        pct = "99%+"
    elif price <= 0.005:
        pct = "<1%"
    else:
        pct = f"{price:.0%}"
    
    # Format dollars nicely
    usd_str = f"${usd_value:,.0f}"

    templates = [
        # 1 — Curiosity / "what do they know?" hook
        "A trader just made a {usd} bet on {outcome} for '{market}' at {pct} to win on {platform}! What do they know that we don't? Want to get notified whenever a whale enters the market? Try out PolyVision and follow the money! {emoji}👇\n\npolyvision.app\n\n{hashtags}",

        # 2 — Breaking alert style
        "🚨 Whale Alert: Someone just dropped {usd} on {outcome} for '{market}' ({pct} probability) via {platform}. Are they hedging or do they have inside info? Catch moves like this in real-time with PolyVision before the market reacts ⚡️\n\npolyvision.app\n\n{hashtags}",

        # 3 — Smart money / don't trade blind
        "Millions are moving on {platform}... A {usd} position was just taken on '{market}' ({outcome} @ {pct}). Don't trade blind — see exactly what the smart money is doing. Track every whale live on PolyVision 🎯\n\npolyvision.app\n\n{hashtags}",

        # 4 — Is smart money leading the charge?
        "Just in: massive {usd} play on '{market}' betting {outcome} ({pct}). Is smart money leading the charge? Don't miss the next big shift on {platform}. Follow the whales and trade smarter with PolyVision. 🌊\n\npolyvision.app\n\n{hashtags}",

        # 5 — Never be late
        "🔥 Huge move on {platform}! A whale just bet {usd} that {outcome} happens for '{market}', buying in at {pct}. Want to know the second these trades happen? PolyVision gets you real-time alerts so you're never late.\n\npolyvision.app\n\n{hashtags}",

        # 6 — What would you do?
        "If someone dropped {usd} on {outcome} for '{market}' at {pct} on {platform}, what would you do — copy the trade, fade it, or just watch? PolyVision shows you every whale move in real-time so you can decide. {emoji}\n\npolyvision.app\n\n{hashtags}",

        # 7 — Storytelling / most people never see it
        "Picture this: a trader quietly places {usd} on {outcome} for '{market}' at {pct} odds on {platform}. Most people never see it. PolyVision users do. Be one of them. 👀\n\npolyvision.app\n\n{hashtags}",

        # 8 — FOMO / odds shift before you know
        "The smart money just moved. {usd} on {outcome} for '{market}' ({pct}) via {platform}. By the time most people see this, the odds will have shifted. Don't be last — PolyVision alerts you the moment it happens. ⚡\n\npolyvision.app\n\n{hashtags}",

        # 9 — Analysis / market signal
        "Market signal: {usd} entered {platform} on {outcome} for '{market}' at {pct}. Whether it's conviction or a hedge, someone powerful thinks they know something. See every move like this with PolyVision. 🔍\n\npolyvision.app\n\n{hashtags}",

        # 10 — Contrarian / smartest or most expensive mistake
        "Either this trader is the smartest person in the room, or they just made a very expensive mistake. {usd} on {outcome} for '{market}' at {pct} on {platform}. Which is it? Follow the money with PolyVision. 🤔\n\npolyvision.app\n\n{hashtags}",

        # 11 — Conversational / conviction money
        "Someone just quietly dropped {usd} on {outcome} for '{market}' at {pct} on {platform}. That's not a casual trade — that's conviction money. PolyVision surfaces moves like this before the news does. 🌊\n\npolyvision.app\n\n{hashtags}",

        # 12 — Community / thousands already tracking
        "The whales are active on {platform}. {usd} just landed on {outcome} for '{market}' ({pct}). Thousands of traders use PolyVision to see exactly where the smart money is going. Are you one of them? {emoji}\n\npolyvision.app\n\n{hashtags}",

        # 13 — Urgency / these don't stay hidden long
        "Right now, someone just placed {usd} on {outcome} for '{market}' on {platform} at {pct}. These opportunities don't stay hidden for long — get real-time whale alerts before the market moves. ⏰\n\npolyvision.app\n\n{hashtags}",

        # 14 — Sarcastic / oh, nothing major
        "Oh, nothing major. Just {usd} quietly betting {outcome} for '{market}' at {pct} on {platform}. Totally normal. 😅 Track every \"normal\" move like this live on PolyVision.\n\npolyvision.app\n\n{hashtags}",

        # 15 — Missed it / while you scrolled
        "While most people scrolled past their feed, a whale dropped {usd} on {outcome} for '{market}' ({pct}) on {platform}. PolyVision users got the alert instantly. Stop missing signals that matter. {emoji}\n\npolyvision.app\n\n{hashtags}",

        # 16 — Why / we don't know their reasoning but we know the move
        "Why would someone bet {usd} on {outcome} for '{market}' at {pct} on {platform}? We don't know their reasoning — but we know the move. Track every whale trade in real-time with PolyVision. 🔎\n\npolyvision.app\n\n{hashtags}",

        # 17 — Aspirational / seat at the table
        "This is how fortunes are made on {platform}: {usd} on {outcome} for '{market}' at {pct}. One massive bet, one massive conviction. Want to be in the room when it happens? PolyVision is your seat at the table. {emoji}\n\npolyvision.app\n\n{hashtags}",

        # 18 — PolyVision flagged this / branded
        "PolyVision just flagged this: {usd} on {outcome} for '{market}' at {pct} via {platform}. {emoji} Our users saw it the moment it happened. Want in on the next one?\n\npolyvision.app\n\n{hashtags}",

        # 19 — Intelligence / this is what market intelligence looks like
        "This is what market intelligence looks like: {usd} on {outcome} for '{market}' at {pct} on {platform}. Real money, real conviction. PolyVision tracks every move so you never trade in the dark. 📡\n\npolyvision.app\n\n{hashtags}",

        # 20 — Edge / there are two types of traders
        "There are two types of traders: those who see moves like this coming, and those who find out after. {usd} on {outcome} for '{market}' ({pct}) just hit {platform}. PolyVision puts you in the first group. {emoji}\n\npolyvision.app\n\n{hashtags}",
    ]

    template = random.choice(templates)
    
    # Calculate how much space the template uses WITHOUT the market title
    template_blank = template.format(
        usd=usd_str, outcome=outcome, market="", pct=pct, 
        platform=platform, emoji=emoji, hashtags=hashtags
    )
    
    # Twitter hard limit is 280, but their API secretly counts URLs as 23 chars
    # regardless of actual length, and emojis count as 2 chars. Our polyvision.app
    # link is 14 chars, so that's a +9 hidden penalty. We use 255 to be perfectly safe.
    chars_left = 255 - len(template_blank)
    
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
