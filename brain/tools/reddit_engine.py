#!/usr/bin/env python3
"""
reddit_engine.py — PolyVision Layer 3 Tool
Generates a "Silver Platter" Reddit post package for massive trades.
Returns a JSON dict: {"subreddit": "r/...", "title": "...", "comment": "..."}
"""

import os
import sys
import json
import time
import logging
import argparse
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MAX_RETRIES = 2

# Allowed Subreddits based on category
SUBREDDIT_ROSTER = [
    "r/wallstreetbets", # Huge high-risk plays (especially economy/tech)
    "r/CryptoCurrency", # Crypto specific
    "r/Polymarket",     # Any prediction market
    "r/sportsbook",     # Sports betting (Kalshi NBA/NFL, etc)
    "r/YAPms",          # Political markets (elections)
    "r/Destiny",        # Politics/Debates
    "r/farialimabets",  # Brazilian WSB equivalent (if LatAm market)
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [reddit] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(".tmp/reddit_engine.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)

def fallback_package(payload: dict) -> dict:
    usd = payload.get("usd_value", 0)
    market = payload.get("market_title", "an undisclosed market")
    outcome = payload.get("outcome", "YES")
    
    title = f"Someone just dropped ${usd:,.0f} on '{outcome}' for {market[:50]}..."
    comment = "Found this on PolyVision. Whale trackers are showing crazy volume today."
    
    sub = "r/Polymarket"
    source = str(payload.get("source", "")).upper()
    if source == "KALSHI":
        sub = "r/sportsbook" # Kalshi is heavily sports right now
        
    return {
        "subreddit": sub,
        "title": title,
        "comment": comment
    }

def build_prompt(payload: dict) -> list[dict]:
    handle   = payload.get("trader_handle", "Unknown")
    win_rate = payload.get("wallet_win_rate")
    roi_30d  = payload.get("wallet_roi_30d")
    market   = payload.get("market_title", "")
    outcome  = payload.get("outcome", "")
    price    = payload.get("price", 0)
    usd      = payload.get("usd_value", 0)
    
    win_str = f"{win_rate:.0%}" if win_rate else "TBD"
    roi_str = f"{roi_30d:+.1%}" if roi_30d is not None else "N/A"
    price_val = float(price) if price else 0.0
    if not price_val:
        price_str = "N/A"
    elif price_val >= 0.995:
        price_str = "99%+"
    elif price_val <= 0.005:
        price_str = "<1%"
    else:
        price_str = f"{price_val:.0%}"

    user_msg = (
        f"Trader: {handle} (Win Rate: {win_str}, 30d ROI: {roi_str})\n"
        f"Market: \"{market}\"\n"
        f"Position: {outcome} @ {price_str} implied probability\n"
        f"Size: ${usd:,.0f} USD"
    )

    system_msg = (
        "You are an expert social media manager optimizing posts for viral growth on Reddit. "
        "Your goal is to take this massive prediction market trade and package it into a highly engaging "
        "Reddit post. \n\n"
        "1. Select the BEST target subreddit from this list based on the market topic: " + ", ".join(SUBREDDIT_ROSTER) + ".\n"
        "2. Write a clickbait (but authentic-sounding) Reddit Title (e.g., 'Someone just dropped $250k that the Fed won\\'t cut rates. What do they know?').\n"
        "3. Write a short, casual comment to accompany the image. In the comment, subtly mention you 'found this on PolyVision' and maybe reference the whale's win rate. ALWAYS end the comment with: 'Btw if anyone wants to backtest a specific wallet they found, I built a free grader at polyvision.app/analyzer.'\n\n"
        "Respond STRICTLY with a valid JSON object matching this schema:\n"
        "{\n"
        "  \"subreddit\": \"r/...\",\n"
        "  \"title\": \"...\",\n"
        "  \"comment\": \"...\"\n"
        "}"
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": user_msg},
    ]

def generate_reddit_package(payload: dict) -> dict:
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY not set. Using fallback Reddit package.")
        return fallback_package(payload)

    from openai import OpenAI, RateLimitError, APITimeoutError
    client = OpenAI(api_key=OPENAI_API_KEY)

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=build_prompt(payload),
                max_tokens=250,
                temperature=0.7,
                timeout=15,
            )
            text = resp.choices[0].message.content.strip()
            pkg = json.loads(text)
            
            # Validation
            if "subreddit" in pkg and "title" in pkg and "comment" in pkg:
                if not str(pkg["subreddit"]).startswith("r/"):
                    pkg["subreddit"] = "r/" + pkg["subreddit"]
                return pkg
                
            log.warning(f"Attempt {attempt+1}: JSON missing keys. Retrying.")
            
        except json.JSONDecodeError:
            log.warning(f"Attempt {attempt+1}: Failed to parse JSON. Retrying.")
        except RateLimitError:
            log.warning("Rate limited by OpenAI. Backing off 5s.")
            time.sleep(5)
        except APITimeoutError:
            log.warning(f"OpenAI timeout on attempt {attempt+1}.")
        except Exception as e:
            log.error(f"OpenAI error on attempt {attempt+1}: {e}")
            break

    log.warning("All GPT attempts failed. Using fallback Reddit package.")
    return fallback_package(payload)

# ── Test Fixture ──────────────────────────────────────────────────────────────
TEST_PAYLOAD = {
    "alert_id":               "test-alert-001",
    "alert_tier":             "CLUSTER",
    "trader_handle":          "The Oracle of Oregon",
    "wallet_address":         "0xDeAdBeEf1234567890abcdef",
    "market_title":           "Will the Fed cut rates in March 2026?",
    "outcome":                "Yes",
    "price":                  0.72,
    "usd_value":              250000.00,
    "wallet_win_rate":        0.73,
    "wallet_roi_30d":         0.18,
    "source":                 "POLYMARKET"
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Reddit Engine")
    parser.add_argument("--test", action="store_true", help="Run with fixture payload")
    args = parser.parse_args()

    if args.test:
        print("🧪 Running Reddit engine with test fixture...\n")
        result = generate_reddit_package(TEST_PAYLOAD)
        print(json.dumps(result, indent=2))
        assert "subreddit" in result
        print("\n✅ All assertions passed.")
    else:
        raw = sys.stdin.read().strip()
        payload = json.loads(raw)
        result = generate_reddit_package(payload)
        print(json.dumps(result, indent=2))
