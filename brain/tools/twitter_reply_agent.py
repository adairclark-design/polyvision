#!/usr/bin/env python3
"""
twitter_reply_agent.py — PolyVision Layer 3 Tool

Automated Mention-Reply Agent with a 5-Layer Budget Gate.
Monitors @PolyVisionApp mentions and responds with intelligent, data-driven replies
acting as an adversarial customer-support / debate agent.

COST PROTECTION — 5 hard Redis caps (mathematically bounded):
  1. Per-tweet dedup       : Never reply to the same tweet twice (7-day TTL)
  2. Per-thread cap        : Max 3 bot replies per conversation thread (48h TTL)
  3. Per-user daily cap    : Max 2 interactions per unique user per 24h
  4. Global daily ceiling  : Hard max 25 total replies per calendar day
  5. Bot-guard             : Never reply to bots or to @PolyVisionApp itself

Worst-case daily cost:
  25 replies × ~100 output tokens × $0.000015/token ≈ $0.04 GPT
  25 X API writes × ~$0.000333/write               ≈ $0.008 X API
  ──────────────────────────────────────────────────────────────────
  Hard max ≈ $0.05/day | $1.50/month regardless of troll volume
"""

import os
import logging
import argparse
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
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

# ── Budget Gate Limits ────────────────────────────────────────────────────────
MAX_REPLIES_PER_THREAD  = 3    # Max times bot replies in one conversation thread
MAX_REPLIES_PER_USER    = 2    # Max times bot replies to one unique user per 24h
MAX_REPLIES_PER_DAY     = 25   # Global hard ceiling per UTC calendar day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [reply_agent] %(levelname)s: %(message)s"
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  LAYER 1–5: REDIS BUDGET GATE
# ─────────────────────────────────────────────────────────────────────────────

def _redis():
    """Return a Redis client. Raises on connection failure."""
    import redis as redis_lib
    return redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=4)

def _budget_gate(tweet_id: str, conversation_id: str, author_id: str, author_name: str) -> tuple[bool, str]:
    """
    Run all 5 budget layers. Returns (allowed: bool, reason: str).
    'allowed' = True means it's safe to reply.
    """
    try:
        r = _redis()

        # Layer 5 — Bot-guard (never reply to self or other bots)
        if "PolyVisionApp" in author_name or "bot" in author_name.lower():
            return False, "bot_guard"

        # Layer 1 — Per-tweet dedup
        if r.get(f"reply:tweet:{tweet_id}"):
            return False, "already_replied_tweet"

        # Layer 2 — Per-thread cap
        thread_key   = f"reply:thread:{conversation_id}:count"
        thread_count = int(r.get(thread_key) or 0)
        if thread_count >= MAX_REPLIES_PER_THREAD:
            return False, f"thread_cap_reached ({thread_count}/{MAX_REPLIES_PER_THREAD})"

        # Layer 3 — Per-user daily cap
        user_key   = f"reply:user:{author_id}:count"
        user_count = int(r.get(user_key) or 0)
        if user_count >= MAX_REPLIES_PER_USER:
            return False, f"user_cap_reached ({user_count}/{MAX_REPLIES_PER_USER})"

        # Layer 4 — Global daily ceiling
        today       = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_key   = f"reply:daily:{today}:total"
        daily_count = int(r.get(daily_key) or 0)
        if daily_count >= MAX_REPLIES_PER_DAY:
            return False, f"daily_ceiling_reached ({daily_count}/{MAX_REPLIES_PER_DAY})"

        return True, "approved"

    except Exception as e:
        log.warning(f"Redis budget gate failed (blocking as failsafe): {e}")
        return False, "redis_error_failsafe"


def _consume_budget(tweet_id: str, conversation_id: str, author_id: str):
    """Increment all budget counters after a successful reply."""
    try:
        r = _redis()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Layer 1 — mark this tweet as replied
        r.setex(f"reply:tweet:{tweet_id}", 86400 * 7, "1")

        # Layer 2 — increment thread counter (48h TTL)
        thread_key = f"reply:thread:{conversation_id}:count"
        r.incr(thread_key)
        r.expire(thread_key, 86400 * 2)

        # Layer 3 — increment user counter (24h TTL)
        user_key = f"reply:user:{author_id}:count"
        r.incr(user_key)
        r.expire(user_key, 86400)

        # Layer 4 — increment global daily counter (resets at midnight UTC)
        daily_key = f"reply:daily:{today}:total"
        r.incr(daily_key)
        r.expire(daily_key, 86400)

    except Exception as e:
        log.warning(f"Redis budget consume failed: {e}")


def _get_budget_status() -> dict:
    """Returns current budget counters for logging/monitoring."""
    try:
        r = _redis()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_count = int(r.get(f"reply:daily:{today}:total") or 0)
        return {
            "daily_used":      daily_count,
            "daily_remaining": max(0, MAX_REPLIES_PER_DAY - daily_count),
            "daily_limit":     MAX_REPLIES_PER_DAY,
        }
    except Exception:
        return {"daily_used": "?", "daily_remaining": "?", "daily_limit": MAX_REPLIES_PER_DAY}


# ─────────────────────────────────────────────────────────────────────────────
#  WHALE DATA CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

def _get_recent_whale_trades(limit: int = 3) -> list:
    """Fetch the top recent whale trades to give the LLM rich context."""
    if not DATABASE_URL:
        return []
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT t.market_title, t.outcome, t.price, t.usd_value,
                           w.handle
                    FROM trades t
                    LEFT JOIN wallets w ON t.wallet_address = w.wallet_address
                    WHERE t.created_at >= NOW() - INTERVAL '24 hours'
                      AND t.usd_value >= 5000
                    ORDER BY t.usd_value DESC
                    LIMIT %s;
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.warning(f"DB fetch failed: {e}")
        return []


def _format_trades_context(trades: list) -> str:
    """Format trades list into a clean string for LLM injection."""
    if not trades:
        return "No major trades detected in the last 24 hours."
    lines = []
    for t in trades:
        usd = t.get("usd_value", 0)
        market = t.get("market_title", "Unknown")
        outcome = t.get("outcome", "?")
        price = float(t.get("price", 0))
        handle = t.get("handle") or "Anonymous Whale"
        lines.append(f"- {handle}: ${usd:,.0f} on {outcome} @ {price:.0%} in \"{market}\"")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  LLM PIPELINE: CLASSIFY INTENT → GENERATE REPLY
# ─────────────────────────────────────────────────────────────────────────────

def _classify_intent(tweet_text: str) -> str:
    """
    Uses gpt-4o-mini (cheap) to classify the user's intent.
    Returns one of: QUESTION | SKEPTIC | INTERESTED | TROLL | OTHER
    """
    if not OPENAI_API_KEY:
        return "QUESTION"  # Default assumption
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this tweet reply mentioning @PolyVisionApp into ONE of these categories:\n"
                    f"QUESTION (asking for information), SKEPTIC (doubting the data), "
                    f"INTERESTED (wants to learn more), TROLL (hostile/spam), OTHER.\n\n"
                    f"Tweet: \"{tweet_text}\"\n\n"
                    f"Return ONLY the single category word, nothing else."
                )
            }],
            max_tokens=5,
            temperature=0.0,
            timeout=8,
        )
        result = resp.choices[0].message.content.strip().upper()
        valid  = {"QUESTION", "SKEPTIC", "INTERESTED", "TROLL", "OTHER"}
        return result if result in valid else "OTHER"
    except Exception as e:
        log.warning(f"Intent classification failed: {e}")
        return "QUESTION"


def _generate_reply(tweet_text: str, intent: str, trades_context: str) -> str | None:
    """
    Uses gpt-4o to generate an adversarial-but-professional contextual reply.
    Persona: ruthless data intelligence tool, never mean but always winning the argument.
    """
    if not OPENAI_API_KEY:
        return None

    # Persona instructions vary by intent
    intent_guidance = {
        "QUESTION":    "Answer their question directly and data-backed. End with a hook to polyvision.app.",
        "SKEPTIC":     "Challenge their skepticism politely but aggressively with the whale data. Make them feel they're missing critical intelligence.",
        "INTERESTED":  "Give them a compelling insight from the whale data to deepen their interest and drive them to polyvision.app.",
        "TROLL":       "Dismiss them with one data-driven sentence that makes the onlookers laugh. Never insult personally — let the data humiliate them.",
        "OTHER":       "Engage briefly with the data and invite them to polyvision.app for more context.",
    }
    guidance = intent_guidance.get(intent, intent_guidance["OTHER"])

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        prompt = f"""You are @PolyVisionApp — a ruthless, data-driven prediction market intelligence bot.
A real user just replied to one of your posts with this message:
"{tweet_text}"

Their intent has been classified as: {intent}
Instruction: {guidance}

Latest PolyVision whale data to reference (use to add value):
{trades_context}

Write ONE reply (strict max 220 characters). Rules:
- Never personally attack or insult the user
- Lean into the data — let the numbers do the talking
- Do NOT use robotic template phrases or hashtags
- End with polyvision.app if it fits naturally
- Be terse, confident, and slightly arrogant (Wall Street intelligence, not Twitter drama)
"""
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.75,
            timeout=15,
        )
        reply = resp.choices[0].message.content.strip().strip('"')
        return reply if len(reply) <= 280 else reply[:276] + "..."
    except Exception as e:
        log.error(f"Reply generation failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def run_reply_agent(dry_run: bool = False):
    log.info("━━ @PolyVisionApp Mention-Reply Agent Pass ━━")

    # Budget status report
    budget = _get_budget_status()
    log.info(f"Daily budget: {budget['daily_used']}/{budget['daily_limit']} replies used "
             f"({budget['daily_remaining']} remaining)")

    if budget.get("daily_remaining") == 0:
        log.info("Daily ceiling already reached. Skipping pass.")
        return

    if not all([TWITTER_API_KEY, TWITTER_API_KEY_SECRET,
                TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
        log.warning("Twitter credentials missing. Aborting.")
        return

    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_KEY_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET,
        )

        # Search for all recent mentions of @PolyVisionApp
        query = "@PolyVisionApp -is:retweet -from:PolyVisionApp"
        log.info(f"Searching mentions: {query}")

        response = client.search_recent_tweets(
            query=query,
            max_results=10,
            expansions=["author_id", "conversation_id"],
            tweet_fields=["conversation_id", "author_id", "text"],
            user_fields=["username"],
        )

        if not response.data:
            log.info("No @PolyVisionApp mentions found.")
            return

        # Build author lookup map from includes
        author_map = {}
        if response.includes and "users" in response.includes:
            for user in response.includes["users"]:
                author_map[str(user.id)] = user.username

        # Pre-fetch whale data once (avoid N DB calls)
        trades    = _get_recent_whale_trades(limit=3)
        trade_ctx = _format_trades_context(trades)
        log.info(f"Loaded {len(trades)} whale trade(s) for context.")

        replies_this_pass = 0

        for tweet in response.data:
            tweet_id       = str(tweet.id)
            author_id      = str(tweet.author_id)
            conversation_id = str(getattr(tweet, "conversation_id", tweet_id))
            author_name    = author_map.get(author_id, "unknown")
            tweet_text     = tweet.text

            log.info(f"Evaluating mention {tweet_id} from @{author_name}: '{tweet_text[:60]}...'")

            # ── Budget Gate ────────────────────────────────────────────────────
            allowed, reason = _budget_gate(tweet_id, conversation_id, author_id, author_name)
            if not allowed:
                log.info(f"  ✗ Blocked by budget gate: {reason}")
                continue

            # ── Classify Intent ────────────────────────────────────────────────
            intent = _classify_intent(tweet_text)
            log.info(f"  ► Intent classified: {intent}")

            # Skip known trolls silently (saves GPT-4o call)
            if intent == "TROLL" and not dry_run:
                # Still consume the tweet dedup slot so we don't re-evaluate it
                _consume_budget(tweet_id, conversation_id, author_id)
                log.info("  ✗ Troll detected. Silently marked as handled.")
                continue

            # ── Generate Reply ─────────────────────────────────────────────────
            reply_text = _generate_reply(tweet_text, intent, trade_ctx)
            if not reply_text:
                log.warning("  ✗ LLM reply generation failed. Skipping.")
                continue

            log.info(f"  ✓ Reply ({len(reply_text)} chars): {reply_text}")

            # ── Post or Dry-Run ────────────────────────────────────────────────
            if dry_run:
                log.info(f"\n[DRY-RUN] Would post reply to @{author_name} (tweet {tweet_id}):\n"
                         f"   \"{reply_text}\"\n")
            else:
                try:
                    client.create_tweet(
                        text=reply_text,
                        in_reply_to_tweet_id=tweet_id,
                    )
                    log.info(f"  ✅ Reply posted to @{author_name}.")
                except Exception as post_err:
                    log.error(f"  ✗ Failed to post reply: {post_err}")
                    continue

            # ── Consume Budget ─────────────────────────────────────────────────
            _consume_budget(tweet_id, conversation_id, author_id)
            replies_this_pass += 1

            # One reply per agent pass to avoid rate hammering
            break

        log.info(f"━━ Pass complete. Replied to {replies_this_pass} mention(s) this run. ━━")

    except Exception as e:
        log.error(f"Reply agent error: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Mention-Reply Agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify and generate replies without posting")
    args = parser.parse_args()
    run_reply_agent(dry_run=args.dry_run)
