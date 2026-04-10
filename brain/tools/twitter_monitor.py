#!/usr/bin/env python3
"""
twitter_monitor.py — PolyVision Layer 3 Tool
Implementation of the "Reply-Value" (Path A) Strategy.
Monitors major prediction market accounts and automatically drops highly contextual 
PolyVision whale alerts as replies to intercept massive organic reach.
"""

import os
import argparse
import logging
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_URL                   = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL                = os.getenv("DATABASE_URL", "")
OPENAI_API_KEY              = os.getenv("OPENAI_API_KEY", "")

TWITTER_API_KEY             = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_KEY_SECRET      = os.getenv("TWITTER_API_KEY_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

TARGET_ACCOUNTS = ["Polymarket", "Kalshi", "unusual_whales", "NateSilver538"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [monitor] %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ── Database & Cache ─────────────────────────────────────────────────────────
def _already_replied(tweet_id: str) -> bool:
    try:
        import redis as redis_lib
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        key = f"twitter:replied_to:{tweet_id}"
        if r.get(key):
            return True
        return False
    except Exception as e:
        log.warning(f"Redis check failed: {e}")
        return True # Fail safe: don't double reply if Redis is down

def _mark_as_replied(tweet_id: str):
    try:
        import redis as redis_lib
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        key = f"twitter:replied_to:{tweet_id}"
        r.setex(key, 86400 * 7, "1") # Keep for 7 days
    except Exception as e:
        log.warning(f"Redis mark failed: {e}")

def get_latest_whale_trade() -> dict | None:
    if not DATABASE_URL:
        return None
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT t.market_title, t.outcome, t.price, t.usd_value, t.side, 
                           w.handle, w.wallet_address
                    FROM trades t 
                    LEFT JOIN wallets w ON t.wallet_address = w.wallet_address
                    ORDER BY t.created_at DESC
                    LIMIT 1;
                """)
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.error(f"DB failed: {e}")
        return None

# ── AI Reply Generation ───────────────────────────────────────────────────────
def generate_reply(tweet_text: str, trade: dict) -> str | None:
    if not OPENAI_API_KEY:
        log.warning("No OPENAI_API_KEY")
        return None

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    usd = trade.get('usd_value', 0)
    market = trade.get('market_title', 'Unknown Market')
    outcome = trade.get('outcome', 'Unknown')
    price = trade.get('price', 0)
    pct = f"{float(price):.0%}"
    
    prompt = f"""
You are the @PolyVisionApp bot, a high-end prediction market intelligence tool. 
A major account just tweeted: "{tweet_text}"

We just detected a massive whale trade tracking this space:
Market: "{market}"
Prediction: {outcome} @ {pct}
Size: ${usd:,.0f} USD

Write ONE highly engaging reply (max 200 chars) to this tweet. 
Drop the whale data to shock the readers, ending with a hook to check polyvision.app to follow the smart money.
Do NOT use robotic templates, emojis at the exact start, or hashtags. Be ruthless and professional.
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.7,
            timeout=15
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return None

# ── Main Orchestrator ─────────────────────────────────────────────────────────
def run_monitor(dry_run: bool = False):
    log.info("Starting X Reply-Value Monitor Pass...")
    if not all([TWITTER_API_KEY, TWITTER_API_KEY_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        log.warning("Missing Twitter credentials.")
        return

    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_KEY_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        
        query = " OR ".join([f"from:{acc}" for acc in TARGET_ACCOUNTS]) + " -is:retweet -is:reply"
        log.info(f"Querying: {query}")
        
        response = client.search_recent_tweets(query=query, max_results=10)
        
        if not response.data:
            log.info("No recent tweets found for target accounts.")
            return

        for tweet in response.data:
            tid = str(tweet.id)
            if _already_replied(tid):
                continue
                
            log.info(f"Checking new tweet {tid}: {tweet.text[:50]}...")
            
            trade = get_latest_whale_trade()
            if not trade:
                log.warning("No recent trade found to drop. Skipping.")
                continue

            reply_text = generate_reply(tweet.text, trade)
            if not reply_text:
                continue

            if dry_run:
                log.info(f"[DRY-RUN] Would reply to {tid} with:")
                log.info(f"   {reply_text}")
            else:
                log.info(f"Replying to {tid}...")
                client.create_tweet(text=reply_text, in_reply_to_tweet_id=tid)
                log.info("✅ Reply successfully posted.")
                
            _mark_as_replied(tid)
            # Only do 1 reply per run to avoid spam limits
            break

    except Exception as e:
        log.error(f"Tweepy error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    run_monitor(dry_run=args.dry_run)
