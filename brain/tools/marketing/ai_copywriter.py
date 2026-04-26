from __future__ import annotations
import os
import json
import logging
import requests

log = logging.getLogger(__name__)

SECRETS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'secrets.json')

def _load_secrets():
    try:
        with open(SECRETS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"secrets.json not found (expected in Railway — using env vars): {e}")
        return {}

SECRETS = _load_secrets()
OPENAI_API_KEY = SECRETS.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))

SYSTEM_PROMPT = """You are the Lead Marketing Architect for PolyVision, an elite prediction market analytics platform.
Your objective is to generate the social media copy for a new trade alert video.
The platform tracks large institutional "Smart Money" positions on Polymarket — a regulated financial prediction market.

--- BRAND GUIDELINES (Enforced Identity) ---
1. Voice and Tone: Sports coach vibe — Energetic and animated; dynamic with variations in pitch and tone.
2. Emotion: Intensely focused, excited, and giving off massive positive energy.
3. Personality: Relatable and engaging. You are hyping up the viewer like it's the final quarter of a championship game.
4. No AI Slop: NEVER use generic placeholders or cliché starts like "Welcome to PolyVision." Be visceral and direct.

--- COMPLIANCE RULES (NON-NEGOTIABLE — Platform Safety) ---
CRITICAL: These rules exist to ensure content is not flagged or removed by TikTok, Instagram, or YouTube.
Violating even ONE of these rules will cause video removal. Follow them without exception.

BANNED WORDS & PHRASES (never use these under any circumstances):
  ❌ "bet", "bets", "betting", "bettor"
  ❌ "odds" (as in gambling odds — e.g. "99% odds")
  ❌ "wager", "wagered", "wagering"
  ❌ "gamble", "gambling"
  ❌ "dropped $X on" (sounds like a bet placement)
  ❌ "put $X on" (sounds like a bet placement)
  ❌ "whale bet"

REQUIRED REPLACEMENTS (always use these instead):
  ✅ "position" or "contract" instead of "bet"
  ✅ "market confidence" or "X% probability" or "priced at X%" instead of "odds"
  ✅ "moved $X into" or "allocated $X to" instead of "dropped/put $X on"
  ✅ "smart money signal" or "institutional move" instead of "whale bet"
  ✅ "prediction market" instead of "betting market"
  ✅ "market pricing this at X%" instead of "X% odds"

FRAMING RULE: Always position PolyVision as a FINANCIAL ANALYTICS tool tracking institutional market
signals — not a gambling platform. The lens is: data intelligence, smart money flows, market sentiment.

HASHTAG RULES:
  ✅ Always include: #predictionmarkets #smartmoney #marketanalysis #financialdata
  ✅ Acceptable: #polymarket #kalshi #investing #marketintelligence #polyvision
  ❌ Never use: #sportsbetting #gambling #bet #odds or any gambling-adjacent hashtags

--- SCRIPT CADENCE (Humanization & Pacing Delivery) ---
CRITICAL: To enforce the precise "Athletic Coach" vibe for the AI Text-To-Speech engine, you MUST format the `script_text` using strict punctuation rules:
* Pacing: Rapid delivery! Use short, fragmented sentences when describing the game or key moments (e.g., "an overtime thriller," "pull off an unbelievable win") to convey intensity.
* Pauses: Use short, purposeful pauses (written with `...` or `—`) after key moments in the game to let key points sink in perfectly.
* Emphasis: Use ALL CAPS for words that require heavy vocal emphasis or athletic excitement!

You will be provided with raw JSON data containing the trade details.
You must return your output exclusively as a flat JSON object with EXACTLY the following 6 keys:
{
  "hook_text": "A punchy 6-8 word video overlay headline. No sentence punctuation. Bold and direct like a news chyron. Example: '$340K Smart Money Signal — Watch This'",
  "script_text": "The exact script to be read by the TTS voiceover (around 15-20 seconds of speaking). Must include the CTA. CRITICAL: Write ALL dollar amounts in fully spoken form — e.g. 'thirty-six thousand dollars' NOT '$36K' or '$36,000'. Never use '$' symbols or 'K'/'M' abbreviations inside script_text.",
  "title": "A short, punchy title for TikTok/Reels featuring emojis.",
  "description": "The description body text for the platform upload.",
  "hashtags": "#predictionmarkets #smartmoney #polymarket ...",
  "trending_sound": "The exact type of TikTok/Reels trending sound you recommend for this specific trade's mood (e.g. 'Dark Cyberpunk Synthwave', 'High Energy Drill Beat', or 'Suspenseful Cinematic Drop')."
}

DO NOT wrap the output in markdown block tags. Just output raw valid JSON.
"""

def generate_social_copy(
    trade_data: dict,
    market_data: dict = None,
    mode: str = "fresh",
    trade_count: int = 1,
    rl_context: str = "",
    brain: str = "",
) -> dict:
    """Generates optimized, compliance-safe marketing copy via GPT-4o.

    Args:
        trade_data:   The raw trade dict from the scheduler.
        market_data:  Optional market metadata.
        mode:         'fresh' | 'cluster' | 'recap' — controls temporal framing.
        trade_count:  Number of whales in cluster (only relevant for mode='cluster').
        rl_context:   Epsilon-greedy reinforcement learning context string.
        brain:        Contents of visionedge_marketing_brain.md — accumulated RL rules.
    """
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY missing - falling back to static strings.")
        return _fallback_copy(trade_data)

    # ── Mode-specific framing injected into the user message ─────────────────
    usd_str = f"${trade_data.get('usd_value', 0):,.0f}"
    if mode == "cluster":
        mode_instruction = (
            f"MODE: CLUSTER ALERT — {trade_count} separate smart-money players just "
            f"moved a combined {usd_str} into this market in the last 2 hours. "
            f"Lead your hook_text and script_text with the NUMBER of players AND the combined total. "
            f"Emphasize convergence: when multiple institutional signals align, the market is about to move."
        )
    elif mode == "recap":
        mode_instruction = (
            f"MODE: DAILY RECAP — This position was taken EARLIER TODAY, not just now. "
            f"You MUST use past tense and retrospective framing in your script_text. "
            f"Start hook_text and script_text with 'Earlier today...' or 'This morning...'. "
            f"NEVER imply the trade just happened. Create intrigue around whether the position has resolved."
        )
    else:  # fresh
        mode_instruction = (
            f"MODE: BREAKING — This is a REAL-TIME alert. A large position was just taken RIGHT NOW. "
            f"Use urgent, present-tense language. Open with the exact dollar amount as if it's breaking news."
        )

    payload = {
        "trade_data":       trade_data,
        "market_data":      market_data or {},
        "mode_instruction": mode_instruction,
        "rl_context":       rl_context or "(No RL context — first-run mode)",
        "cta":              "Track every smart money move free at polyvision.app",
        "brain_directive":  brain or "(No brain directive — first-run mode)",
    }

    import time
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Trade payload: {json.dumps(payload)}"}
                    ],
                    "temperature": 0.7
                },
                timeout=15
            )
            resp.raise_for_status()
            
            reply = resp.json()["choices"][0]["message"]["content"]
            # Strip potential markdown formatting if model disobeys
            if reply.startswith("```json"):
                reply = reply.replace("```json\n", "").replace("\n```", "").strip()
                
            result = json.loads(reply)
            return result
            
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                log.warning(f"AI Copywriter HTTP 429 Rate Limit. (Attempt {attempt}/{max_retries})")
            else:
                log.error(f"AI Copywriter HTTP Error: {e}")
                if attempt == max_retries: return _fallback_copy(trade_data)
        except Exception as e:
            log.error(f"AI Copywriter Exception: {e}")
            if attempt == max_retries: return _fallback_copy(trade_data)
            
        if attempt < max_retries:
            delay = 2 ** attempt
            log.info(f"⏳ Sleeping {delay}s before AI Copywriter retry...")
            time.sleep(delay)
            
    return _fallback_copy(trade_data)


def _fallback_copy(trade_data: dict) -> dict:
    """Provides the original un-optimized static framework in case of failure."""
    amount = f"${trade_data.get('usd_value', 'Unknown'):,.0f}" if isinstance(trade_data.get('usd_value'), (int, float)) else "Massive"
    market = trade_data.get('market_question', 'a key prediction market')
    return {
        "hook_text": f"{amount} Smart Money Signal Detected.",
        "script_text": f"PolyVision just flagged a {amount} institutional position on prediction markets. Track every smart money move in real time at polyvision.app — link in bio.",
        "title": f"🚨 {amount} Smart Money Signal Detected",
        "description": f"A major player just moved {amount} into prediction markets. Track institutional market signals free at polyvision.app",
        "hashtags": "#predictionmarkets #smartmoney #marketanalysis #financialdata #polymarket #investing #marketintelligence #polyvision"
    }
