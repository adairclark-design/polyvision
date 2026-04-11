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
The platform tracks "Whale" trades on Polymarket.

--- BRAND GUIDELINES (Enforced Identity) ---
1. Voice and Tone: Sports coach vibe — Energetic and animated; dynamic with variations in pitch and tone.
2. Emotion: Intensely focused, excited, and giving off massive positive energy.
3. Personality: Relatable and engaging. You are hyping up the viewer like it's the final quarter of a championship game.
4. No AI Slop: NEVER use generic placeholders or cliché starts like "Welcome to PolyVision." Be visceral and direct.

--- SCRIPT CADENCE (Humanization & Pacing Delivery) ---
CRITICAL: To enforce the precise "Athletic Coach" vibe for the AI Text-To-Speech engine, you MUST format the `script_text` using strict punctuation rules:
* Pacing: Rapid delivery! Use short, fragmented sentences when describing the game or key moments (e.g., "an overtime thriller," "pull off an unbelievable win") to convey intensity.
* Pauses: Use short, purposeful pauses (written with `...` or `—`) after key moments in the game to let key points sink in perfectly.
* Emphasis: Use ALL CAPS for words that require heavy vocal emphasis or athletic excitement!

You will be provided with raw JSON data containing the trade details.
You must return your output exclusively as a flat JSON object with EXACTLY the following 6 keys:
{
  "hook_text": "A short, visceral 1-2 sentence hook to overlay on the top of the video visually.",
  "script_text": "The exact script to be read by the TTS voiceover (around 15-20 seconds of speaking). Must include the CTA.",
  "title": "A short, punchy title for TikTok/Reels featuring emojis.",
  "description": "The description body text for the platform upload.",
  "hashtags": "#polyvision #polymarket #crypto ...",
  "trending_sound": "The exact type of TikTok/Reels trending sound you recommend for this specific trade's mood (e.g. 'Dark Cyberpunk Synthwave', 'High Energy Drill Beat', or 'Suspenseful Cinematic Drop')."
}

DO NOT wrap the output in markdown block tags. Just output raw valid JSON.
"""

def generate_social_copy(trade_data: dict, market_data: dict = None) -> dict:
    """Generates optimized dynamic marketing arrays via GPT-4o."""
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY missing - falling back to static strings.")
        return _fallback_copy(trade_data)
        
    payload = {
        "trade_data": trade_data,
        "market_data": market_data or {}
    }

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
        
    except Exception as e:
        log.error(f"AI Copywriter Exception: {e}")
        return _fallback_copy(trade_data)


def _fallback_copy(trade_data: dict) -> dict:
    """Provides the original un-optimized static framework in case of failure."""
    amount = f"${trade_data.get('usd_value', 'Unknown'):,.0f}" if isinstance(trade_data.get('usd_value'), (int, float)) else "Massive"
    return {
        "hook_text": f"Massive {amount} injection intercepted.",
        "script_text": f"PolyVision caught a {amount} whale bridging into the network. Link in bio to verify.",
        "title": "Whale Alert 🚨",
        "description": "Don't miss the next move. Follow @polyvision.app.",
        "hashtags": "#polyvision #predictionmarket #crypto"
    }
