#!/usr/bin/env python3
"""
twitter_threader.py — PolyVision Layer 3 Tool
Generates and posts a Daily Recap Thread on X/Twitter summarizing the top 5 largest whale trades of the past 24 hours.
Designed to maximize algorithmic reach while rigidly preserving daily API limits.
"""

import os
import time
import json
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Twitter V2 OAuth 1.0a credentials
TWITTER_API_KEY             = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_KEY_SECRET      = os.getenv("TWITTER_API_KEY_SECRET", "")
TWITTER_ACCESS_TOKEN        = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

def fetch_top_trades() -> list:
    """Fetch the top 5 highest USD volume trades from the past 24 hours."""
    if not DATABASE_URL:
        log.warning("[Threader] No DATABASE_URL provided. Cannot fetch trades.")
        return []
        
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT t.market_title, t.outcome, t.price, t.usd_value, t.side, 
                           w.handle, w.wallet_address
                    FROM trades t 
                    JOIN wallets w ON t.wallet_address = w.wallet_address
                    WHERE t.created_at >= NOW() - INTERVAL '24 hours'
                    ORDER BY t.usd_value DESC
                    LIMIT 5;
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.error(f"[Threader] Failed to query PostgreSQL: {e}")
        return []

def generate_thread_copy(trades: list) -> list:
    """Pass exactly 5 trades to GPT-4o-mini to return a JSON array of perfectly structured thread tweets."""
    if not OPENAI_API_KEY:
        log.error("[Threader] OPENAI_API_KEY is missing. Aborting thread generation.")
        return []
    if not trades:
        log.warning("[Threader] No trades provided to generate_thread_copy.")
        return []

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Format the payload to inject into the OpenAI prompt
    trades_text = ""
    for idx, t in enumerate(trades, 1):
        usd = t.get('usd_value', 0)
        market = t.get('market_title', 'Unknown Market')
        outcome = t.get('outcome', 'Unknown')
        price = float(t.get('price', 0))
        
        if price >= 0.995:
            price_str = "99%+"
        elif price <= 0.005:
            price_str = "<1%"
        else:
            price_str = f"{price:.0%}"
            
        handle = t.get('handle', 'Unknown Whale')
        wallet = t.get('wallet_address', '')
        
        trades_text += (
            f"Trade {idx}:\n"
            f"- Persona: {handle}\n"
            f"- Target Wallet Address: {wallet}\n"
            f"- Market: \"{market}\"\n"
            f"- Prediction: {outcome} @ {price_str}\n"
            f"- Position Size: ${usd:,.0f} USD\n\n"
        )
        
    prompt = (
        "You are an elite, algorithmic-focused Twitter Ghostwriter for @PolyVisionApp. "
        "Your task is to write a highly engaging multi-tweet thread summarizing the top 5 largest "
        "prediction market whale trades of the past 24 hours.\n\n"
        "Input Data:\n" + trades_text +
        "Rules:\n"
        "1. First tweet must be a viral Hook summarizing the volume (e.g. 'Over $X was deployed by massive wallets in the last 24h. Here are the 5 biggest bets: 🧵').\n"
        "2. The next tweets must break down exactly 1 trade per tweet in highly engaging, readable formats. Importantly, mention their Persona Handle and visually tag/include their Wallet Address snippet so users can copy-paste and verify on-chain.\n"
        "3. The final tweet in the array must be a CTA (Call To Action): 'Want real-time live alerts before the market moves? Get the PolyVision Discord bot: polyvision.app | Or grade your own wallet\\'s all-time win rate against these whales natively at polyvision.app/analyzer'.\n"
        "4. Strict Character limit per tweet: 270 chars max.\n\n"
        "Output Requirements:\n"
        "Return STRICTLY a JSON object matching this exact schema, with no markdown code blocks wrapping it:\n"
        "{\n"
        "  \"tweets\": [\n"
        "    \"[Tweet 1 text...]\",\n"
        "    \"[Tweet 2 text...]\"\n"
        "  ]\n"
        "}"
    )
    
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[{"role": "user", "content": prompt}],
            max_tokens=650,
            temperature=0.7,
            timeout=20,
        )
        data = json.loads(resp.choices[0].message.content.strip())
        return data.get("tweets", [])
    except Exception as e:
        log.error(f"[Threader] Failed to generate OpenAI tweets: {e}")
        return []

def execute_twitter_thread(tweets: list, dry_run: bool = False):
    """Publish an array of tweets sequentially via Tweepy V2, ensuring they append seamlessly as replies."""
    if not tweets:
        log.warning("[Threader] Empty tweets array. Aborting.")
        return
        
    if dry_run:
        log.info(f"--- 🧪 DRY RUN TWITTER THREAD ({len(tweets)} tweets) ---")
        for i, t in enumerate(tweets, 1):
            log.info(f"[{i}/{len(tweets)}] {t}")
        return

    if not all([TWITTER_API_KEY, TWITTER_API_KEY_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        log.error("[Threader] Missing Twitter API credentials. Check environment variables.")
        return

    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_KEY_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        
        log.info(f"[Threader] Assembling {len(tweets)}-tweet thread...")
        
        # Publish Hook (First Tweet)
        first_tweet = client.create_tweet(text=tweets[0])
        previous_tweet_id = first_tweet.data['id']
        log.info(f"[Threader] Successfully published Hook: {previous_tweet_id}")
        
        # Sequentially thread remaining tweets
        for i, tweet_copy in enumerate(tweets[1:], 2):
            time.sleep(2) # Avoid aggressive API tripping
            reply = client.create_tweet(text=tweet_copy, in_reply_to_tweet_id=previous_tweet_id)
            previous_tweet_id = reply.data['id']
            log.info(f"[Threader] Successfully threaded part {i}: {previous_tweet_id}")
            
        log.info("[Threader] thread executed flawlessly.")

    except Exception as e:
        log.error(f"[Threader] Tweepy encountered a critical error while threading: {e}")

def run_daily_recap(dry_run: bool = False):
    """Main Orchestrator: Fetches trades, generates thread, and posts entirely autonomously."""
    log.info("[Threader] Orchestrating Daily X Recap Thread...")
    
    trades = fetch_top_trades()
    if len(trades) < 3:
        log.warning(f"[Threader] Only {len(trades)} trades recorded in 24h. Aborting summary to preserve quality.")
        return
        
    tweet_array = generate_thread_copy(trades)
    execute_twitter_thread(tweet_array, dry_run=dry_run)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import argparse
    parser = argparse.ArgumentParser(description="PolyVision Twitter Threader")
    parser.add_argument("--dry-run", action="store_true", help="Format thread but bypass Twitter API")
    args = parser.parse_args()
    
    run_daily_recap(dry_run=args.dry_run)
