#!/usr/bin/env python3
"""
tts_generator.py — VisionEdge Marketing Agent | Layer 3: TTS
Generates a professional voiceover MP3 for a given script using
the OpenAI TTS API (routed through OpenRouter credentials).

Voices: alloy (neutral), echo (deep), shimmer (clear).
Default: shimmer — authoritative, clear, trading-context credible.
"""
import os
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SECRETS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'secrets.json')
OUTPUT_DIR   = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'marketing')

def _load_secrets():
    try:
        with open(SECRETS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"secrets.json not found (expected in Railway — using env vars): {e}")
        return {}

SECRETS       = _load_secrets()
# OpenAI TTS key — falls back to OPENAI_API_KEY if not explicitly set
OPENAI_API_KEY = SECRETS.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))


def generate_voiceover(
    script: str,
    voice: str = "ash",
    output_path: str | None = None
) -> str | None:
    """
    Generate a TTS voiceover MP3 from script text.

    Args:
        script:       The spoken text.
        voice:        OpenAI TTS voice: alloy | echo | fable | onyx | nova | shimmer
        output_path:  If None, auto-generates path in .tmp/marketing/.

    Returns:
        Absolute path to the MP3 file, or None on failure.
    """
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY not set — cannot generate TTS.")
        return None

    if not output_path:
        ts          = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(OUTPUT_DIR, f"voiceover_{ts}.mp3")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        resp = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model": "tts-1",        # Changed from HD to Base. Drops token cost by 50% and breathes more organically.
                "input": script,
                "voice": voice,
                "response_format": "mp3",
                "speed": 1.05,   # Accelerated to 1.05x enforcing the Brand Guidelines' urgent authority cadence
            },
            timeout=30,
        )
        resp.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(resp.content)

        log.info(f"Voiceover saved → {output_path} ({len(resp.content):,} bytes)")
        return output_path

    except Exception as e:
        log.error(f"TTS generation failed: {e}")
        return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    test_script = (
        "Our AI just spotted a massive bearish engulfing candle on NVDA "
        "right at the 0.618 Fibonacci resistance. Most traders are still "
        "long. VisionEdge AI begs to differ. Full analysis at VisionEdge dot app."
    )
    script = sys.argv[1] if len(sys.argv) > 1 else test_script
    path   = generate_voiceover(script)
    print(f"Voiceover: {path}")
