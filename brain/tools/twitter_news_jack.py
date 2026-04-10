#!/usr/bin/env python3
"""
twitter_news_jack.py — PolyVision Layer 3 Tool
Implementation of the News-Triggered Tweeting (News-Jacking) strategy.
Fetches top headlines from major financial/political RSS feeds, uses an LLM to extract
the core topic, searches the PolyVision Database for a massive whale trade related to that topic,
and generates a viral tweet showing how the "smart money" positioned themselves before the news broke.
"""

import os
import time
import json
import logging
import argparse
import hashlib
import psycopg2
import psycopg2.extras
from typing import Optional
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

RSS_FEEDS = [
    # ── Google News (most permissive CDN, works on datacenter IPs) ─────────────
    "https://news.google.com/rss/search?q=prediction+markets&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=polymarket+kalshi+betting&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=trump+tariffs+federal+reserve+economy&hl=en-US&gl=US&ceid=US:en",
    # ── Reuters (reliable on server IPs) ──────────────────────────────────────
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.reuters.com/reuters/businessNews",
    # ── Primary feeds (may 403 on Railway IPs, but kept as bonus) ─────────────
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=120000015&id=100003114",
    "https://finance.yahoo.com/news/rssindex",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

FETCH_TIMEOUT_SECS = 8  # Per-feed HTTP timeout — prevents one slow feed hanging the whole run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [newsjack] %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ── Redis Tracking ────────────────────────────────────────────────────────────
def _hash_link(link: str) -> str:
    return hashlib.md5(link.encode()).hexdigest()

def _already_processed(link: str) -> bool:
    try:
        import redis as redis_lib
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        key = f"twitter:newsjacked:{_hash_link(link)}"
        return bool(r.get(key))
    except Exception as e:
        log.warning(f"Redis check failed: {e}")
        return True # Failsafe to prevent spam

def _mark_as_processed(link: str):
    try:
        import redis as redis_lib
        r = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        key = f"twitter:newsjacked:{_hash_link(link)}"
        r.setex(key, 86400 * 3, "1") # Keep for 3 days
    except Exception as e:
        log.warning(f"Redis mark failed: {e}")


# ── Core Logic ───────────────────────────────────────────────────────────────
def get_rss_headlines() -> list:
    """Fetch the top 5 most recent articles from all configured RSS feeds.
    Each feed is fetched in isolation — a failure for one does not block others.
    Google News feeds are tried first as they are most permissive on datacenter IPs.
    """
    try:
        import feedparser
    except ImportError:
        log.error("feedparser is not installed. Please run: pip install feedparser")
        return []

    articles = []
    successful_feeds = 0
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(
                feed_url,
                agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                request_headers={"Accept": "application/rss+xml, application/xml, */*"},
            )
            if feed.bozo and not feed.entries:
                log.debug(f"RSS feed returned no usable entries (may be blocked or malformed): {feed_url[:60]}")
                continue
            count = 0
            for entry in feed.entries[:5]:
                articles.append({
                    "title":   entry.get("title", ""),
                    "link":    entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                })
                count += 1
            if count > 0:
                log.info(f"RSS: {count} articles from {feed_url[:60]}")
                successful_feeds += 1
        except Exception as e:
            log.warning(f"Failed to fetch RSS from {feed_url[:60]}: {e}")
    log.info(f"Ingested {len(articles)} total headlines from {successful_feeds}/{len(RSS_FEEDS)} feeds.")
    return articles

def extract_keyword(headline: str, summary: str) -> Optional[str]:
    """Uses GPT-4o-mini to extract a single relevant search keyword from the news story."""
    if not OPENAI_API_KEY:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""
I have a breaking news headline:
Title: {headline}
Summary: {summary}

Your job is to identify the single most important entity, topic, or proper noun in this news that could be traded on a prediction market.
Return ONLY ONE completely isolated word or short phrase. No punctuation, no quotes, no conversational text.
Examples: "Trump", "Interest Rates", "Bitcoin", "OpenAI", "Supreme Court", "Inflation"
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=15,
            temperature=0.0
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"Keyword extraction failed: {e}")
        return None

def find_correlating_trade(keyword: str) -> Optional[dict]:
    """Search the PolyVision trades table for a massive recent trade matching the keyword."""
    if not DATABASE_URL or not keyword:
        return None
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Search for trades over $10,000 in the last 72 hours where the market title matches the keyword
                search_term = f"%{keyword}%"
                cur.execute("""
                    SELECT t.market_title, t.outcome, t.price, t.usd_value, t.side, 
                           w.handle, w.wallet_address
                    FROM trades t 
                    LEFT JOIN wallets w ON t.wallet_address = w.wallet_address
                    WHERE t.created_at >= NOW() - INTERVAL '72 hours'
                      AND t.usd_value >= 10000
                      AND t.market_title ILIKE %s
                    ORDER BY t.usd_value DESC
                    LIMIT 1;
                """, (search_term,))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.error(f"Database search failed: {e}")
        return None

def generate_newsjack_tweet(article: dict, trade: dict) -> Optional[str]:
    """Synthesize the breaking news and the whale trade into a viral Tweet."""
    if not OPENAI_API_KEY:
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
BREAKING NEWS just dropped:
Headline: "{article['title']}"

We searched our database and found that a massive "smart money" whale positioned themselves heavily right before this news broke.
Whale Trade Data:
- Market: "{market}"
- Bet: {outcome} @ {pct} probability
- Size: ${usd:,.0f} USD

Write ONE viral, highly engaging tweet (max 250 characters) that connects the breaking news to this whale's prescient behavior. 
Make the reader feel FOMO that they weren't tracking this whale beforehand.
End the tweet with exactly: "polyvision.app"
Do NOT use corny emojis at the absolute beginning of the tweet. Be professional, slick, and slightly adversarial towards retail traders being left behind.
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.8
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"GPT-4o tweet generation failed: {e}")
        return None


def run_news_jack(dry_run: bool = False):
    log.info("Starting X News-Jacking Engine Pass...")
    if not all([TWITTER_API_KEY, TWITTER_API_KEY_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]) and not dry_run:
        log.warning("Missing Twitter credentials. Aborting.")
        return

    articles = get_rss_headlines()
    log.info(f"Ingested {len(articles)} recent headlines from RSS.")
    
    import tweepy
    if not dry_run:
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_KEY_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
    else:
        client = None

    posted_count = 0
    for article in articles:
        if posted_count >= 1: # Max 1 news jack per run to prevent timeline flooding
            break
            
        link = article.get("link", "")
        if not link or _already_processed(link):
            continue

        _mark_as_processed(link) # Mark early so we don't spam if there's a crash downstream
        
        headline = article.get("title", "")
        keyword = extract_keyword(headline, article.get("summary", ""))
        
        if not keyword:
            continue
            
        log.info(f"Targeting keyword: [{keyword}] from headline: '{headline[:40]}...'")
        
        trade_match = find_correlating_trade(keyword)
        if not trade_match:
            log.info(f"     No major whale trade found matching [{keyword}]. Skipping news.")
            continue
            
        log.info(f"     ✅ Correlated massive trade found! ${trade_match.get('usd_value', 0):,.0f} USD.")
        
        tweet_text = generate_newsjack_tweet(article, trade_match)
        if not tweet_text:
            continue
            
        if dry_run:
            log.info(f"\n[DRY-RUN] News-Jacked Tweet Ready:\n{tweet_text}\n")
        else:
            try:
                log.info("Publishing News-Jacked Tweet to Timeline...")
                client.create_tweet(text=tweet_text)
                log.info("✅ Tweet successfully published.")
            except Exception as e:
                log.error(f"Failed to post to X: {e}")
                
        posted_count += 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_news_jack(dry_run=args.dry_run)
