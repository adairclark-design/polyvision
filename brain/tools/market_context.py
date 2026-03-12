#!/usr/bin/env python3
"""
market_context.py — PolyVision Layer 3 Tool
Fetches live news context for a Polymarket market title using the Tavily
Search API. Results are cached in Redis for 5 minutes to minimise cost and
latency when multiple whale alerts fire on the same market.

Usage:
    python tools/market_context.py --test
    python tools/market_context.py "Will the Fed cut rates in March 2026?"
"""

import os
import sys
import json
import time
import hashlib
import logging
import argparse
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY   = os.getenv("TAVILY_API_KEY", "")
REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL        = int(os.getenv("CONTEXT_CACHE_TTL", "300"))   # 5 minutes
TAVILY_ENDPOINT  = "https://api.tavily.com/search"
MAX_CONTEXT_LEN  = 400   # chars injected into GPT prompt
MAX_RESULTS      = 3
REQUEST_TIMEOUT  = 8     # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [context] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


def _cache_key(market_title: str) -> str:
    h = hashlib.md5(market_title.lower().strip().encode()).hexdigest()[:12]
    return f"context:tavily:{h}"


def _get_redis():
    """Lazy Redis import — graceful if Redis is unavailable."""
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        r.ping()
        return r
    except Exception as e:
        log.debug(f"Redis unavailable for context cache: {e}")
        return None


def _query_tavily(market_title: str) -> dict | None:
    """
    Call Tavily API and return a condensed context dict:
      { "context": <str>, "sources": [<url>, ...] }
    """
    if not TAVILY_API_KEY:
        log.warning("TAVILY_API_KEY not set — skipping live context fetch.")
        return None

    # Build a tight search query from the market title
    query = f"latest news {market_title} prediction market 2026"

    headers = {
        "Content-Type": "application/json",
    }
    body = {
        "api_key":       TAVILY_API_KEY,
        "query":         query,
        "search_depth":  "basic",
        "max_results":   MAX_RESULTS,
        "include_answer": True,          # Tavily returns a synthesised answer
        "include_raw_content": False,
    }

    try:
        resp = requests.post(
            TAVILY_ENDPOINT,
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        log.warning("Tavily request timed out.")
        return None
    except requests.exceptions.HTTPError as e:
        log.warning(f"Tavily HTTP error: {e}")
        return None
    except Exception as e:
        log.warning(f"Tavily error: {e}")
        return None

    # Prefer the synthesised `answer` field; fall back to top result content
    context_text = data.get("answer", "")
    if not context_text:
        results = data.get("results", [])
        if results:
            context_text = results[0].get("content", "")

    if not context_text:
        log.info("Tavily returned no usable content.")
        return None

    # Trim to budget
    context_text = context_text.strip()[:MAX_CONTEXT_LEN]

    # Collect source URLs
    sources = [r.get("url", "") for r in data.get("results", []) if r.get("url")][:3]

    log.info(f"Tavily context ({len(context_text)} chars): {context_text[:80]}...")
    return {
        "context":   context_text,
        "sources":   sources,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def get_market_context(market_title: str) -> dict | None:
    """
    Returns a context dict for the given market title, using Redis cache.
    Returns None if Tavily is unavailable or the key is not set.

    Return shape:
        {
            "context":    str,   # 1-2 sentence news blurb for GPT prompt
            "sources":    list,  # citation URLs for dashboard display
            "fetched_at": str,   # ISO timestamp
        }
    """
    if not TAVILY_API_KEY:
        return None

    # Try cache first
    r = _get_redis()
    key = _cache_key(market_title)

    if r:
        cached = r.get(key)
        if cached:
            try:
                result = json.loads(cached)
                log.info(f"Context cache HIT for '{market_title[:40]}'")
                return result
            except json.JSONDecodeError:
                pass

    # Cache miss — hit Tavily
    log.info(f"Context cache MISS — querying Tavily for '{market_title[:40]}'")
    result = _query_tavily(market_title)

    if result and r:
        try:
            r.setex(key, CACHE_TTL, json.dumps(result))
        except Exception as e:
            log.debug(f"Failed to cache context: {e}")

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run with fixture market title")
    parser.add_argument("market_title", nargs="?", help="Market title to look up")
    args = parser.parse_args()

    title = args.market_title or ("Will the Fed cut rates in March 2026?" if args.test else None)
    if not title:
        print("Usage: python market_context.py \"<market title>\"  OR  --test")
        sys.exit(1)

    print(f"🔍 Fetching context for: \"{title}\"\n")
    result = get_market_context(title)
    if result:
        print(f"📰 Context ({len(result['context'])} chars):")
        print(f"   {result['context']}\n")
        print(f"🔗 Sources:")
        for s in result["sources"]:
            print(f"   {s}")
    else:
        print("⚠️  No context returned (check TAVILY_API_KEY or network).")
