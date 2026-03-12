#!/usr/bin/env python3
"""
ai_summarizer.py — PolyVision Layer 3 Tool
Calls GPT-4o to generate a professional, 280-char-max summary of a
WhaleAlertPayload. Enforces the forbidden-term blocklist and appends
the standard disclaimer.

Architecture SOP: architecture/04_ai_summarizer.md
Usage:
    python tools/ai_summarizer.py --test        # run with fixture payload
    python tools/ai_summarizer.py < alert.json  # pipe a WhaleAlertPayload
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
MAX_SUMMARY_LEN = 280
MAX_RETRIES     = 2
DISCLAIMER_TAG  = "Trade at your own risk."

FORBIDDEN_TERMS = [
    "gambl", "bet ", "betting", "wager", "casino",
    "punt", "degen", "ape in", "moonshot",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [summarizer] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(".tmp/summarizer.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)


# ── Forbidden Term Check ──────────────────────────────────────────────────────
def contains_forbidden(text: str) -> str | None:
    """Returns the first forbidden term found in text, or None if clean."""
    low = text.lower()
    for term in FORBIDDEN_TERMS:
        if term in low:
            return term
    return None


# ── Fallback Summary ──────────────────────────────────────────────────────────
def fallback_summary(payload: dict) -> str:
    handle    = payload.get("trader_handle", "Unknown Trader")
    usd_value = payload.get("usd_value", 0)
    market    = payload.get("market_title", "an undisclosed market")
    outcome   = payload.get("outcome", "")
    s = (
        f"{handle} entered a ${usd_value:,.0f} {outcome} position "
        f"on '{market}'. {DISCLAIMER_TAG}"
    )
    return s[:MAX_SUMMARY_LEN]


# ── GPT-4o Summarizer ─────────────────────────────────────────────────────────
def build_prompt(payload: dict, extra_instruction: str = "") -> list[dict]:
    handle   = payload.get("trader_handle", "Unknown")
    win_rate = payload.get("wallet_win_rate")
    roi_30d  = payload.get("wallet_roi_30d")
    market   = payload.get("market_title", "")
    outcome  = payload.get("outcome", "")
    price    = payload.get("price", 0)
    usd      = payload.get("usd_value", 0)
    copy     = payload.get("copy_trade_recommended", False)

    win_str = f"{win_rate:.0%}" if win_rate is not None else "N/A"
    roi_str = f"{roi_30d:+.1%}" if roi_30d is not None else "N/A"

    user_msg = (
        f"Trader: {handle} (Win Rate: {win_str}, 30d ROI: {roi_str})\n"
        f"Market: \"{market}\"\n"
        f"Position: {outcome} @ ${price:.2f} implied probability\n"
        f"Size: ${usd:,.0f} USD\n"
        f"Copy Trade Recommended: {'Yes' if copy else 'No'}"
    )
    if extra_instruction:
        user_msg = f"{extra_instruction}\n\n{user_msg}"

    return [
        {
            "role": "system",
            "content": (
                "You are a professional financial analyst covering prediction markets. "
                "Write a single, factual, analytical sentence (max 280 characters) summarizing "
                "this trade. Use Bloomberg Terminal tone — professional, urgent, analytical. "
                "Do NOT use the words: gambling, betting, wager, casino, punt, moonshot, ape, degen. "
                f"Always end with: \"{DISCLAIMER_TAG}\""
            ),
        },
        {"role": "user", "content": user_msg},
    ]


def generate_summary(payload: dict) -> str:
    """Generate GPT-4o summary with retry and fallback logic."""
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY not set. Using fallback summary.")
        return fallback_summary(payload)

    from openai import OpenAI, RateLimitError, APITimeoutError
    client = OpenAI(api_key=OPENAI_API_KEY)

    extra_instruction = ""
    for attempt in range(MAX_RETRIES):
        try:
            messages = build_prompt(payload, extra_instruction)
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=150,
                temperature=0.3,
                timeout=15,
            )
            text = resp.choices[0].message.content.strip()

            # Enforce max length
            text = text[:MAX_SUMMARY_LEN]

            # Ensure disclaimer present
            if DISCLAIMER_TAG not in text:
                text = (text.rstrip(".") + f". {DISCLAIMER_TAG}")[:MAX_SUMMARY_LEN]

            # Check for forbidden terms
            found = contains_forbidden(text)
            if found:
                log.warning(f"Attempt {attempt+1}: Forbidden term '{found}' found. Retrying.")
                extra_instruction = f"Do not use the word '{found}', rephrase accordingly."
                continue

            return text

        except RateLimitError:
            log.warning("Rate limited by OpenAI. Backing off 10s.")
            time.sleep(10)
        except APITimeoutError:
            log.warning(f"OpenAI timeout on attempt {attempt+1}.")
        except Exception as e:
            log.error(f"OpenAI error on attempt {attempt+1}: {e}")
            break

    log.warning("All GPT attempts failed. Using fallback summary.")
    return fallback_summary(payload)


def summarize(payload: dict) -> dict:
    """Enriches a WhaleAlertPayload with an AI-generated summary."""
    os.makedirs(".tmp", exist_ok=True)
    summary = generate_summary(payload)
    payload["ai_summary"] = summary
    log.info(f"Summary generated: {summary[:80]}...")
    return payload


# ── Test Fixture ──────────────────────────────────────────────────────────────
TEST_PAYLOAD = {
    "alert_id":               "test-alert-001",
    "alert_tier":             "WHALE",
    "trader_handle":          "The Oracle of Oregon",
    "wallet_address":         "0xDeAdBeEf1234567890abcdef",
    "market_title":           "Will the Fed cut rates in March 2026?",
    "outcome":                "Yes",
    "price":                  0.72,
    "usd_value":              50000.00,
    "wallet_win_rate":        0.73,
    "wallet_roi_30d":         0.18,
    "copy_trade_recommended": True,
    "disclaimer":             "Whales can hedge. Following a trade is at your own risk.",
    "ai_summary":             None,
}


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision AI Summarizer")
    parser.add_argument("--test", action="store_true", help="Run with fixture payload")
    args = parser.parse_args()

    if args.test:
        print("🧪 Running AI summarizer with test fixture...\n")
        result = summarize(TEST_PAYLOAD.copy())
        print(f"Summary ({len(result['ai_summary'])} chars):")
        print(f"  \"{result['ai_summary']}\"\n")
        assert result["ai_summary"] is not None
        assert len(result["ai_summary"]) <= MAX_SUMMARY_LEN, "Summary exceeds 280 chars!"
        assert DISCLAIMER_TAG in result["ai_summary"], "Disclaimer missing!"
        forbidden = contains_forbidden(result["ai_summary"])
        assert not forbidden, f"Forbidden term found: {forbidden}"
        print("✅ All assertions passed.")
    else:
        raw = sys.stdin.read().strip()
        payload = json.loads(raw)
        result = summarize(payload)
        print(json.dumps(result, indent=2))
