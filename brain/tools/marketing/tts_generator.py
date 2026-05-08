#!/usr/bin/env python3
from __future__ import annotations
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


def _expand_numbers(text: str) -> str:
    """
    Safety net: expand abbreviated dollar amounts to spoken form before TTS.
    Handles the most common formats the AI copywriter might produce.
    '$36K'   → 'thirty-six thousand dollars'
    '$1.2M'  → 'one point two million dollars'
    '$36,428'→ 'thirty-six thousand dollars'  (rounds to nearest thousand)
    """
    import re

    _ones  = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
               'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen',
               'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
    _tens  = ['', '', 'twenty', 'thirty', 'forty', 'fifty',
               'sixty', 'seventy', 'eighty', 'ninety']

    def _int_to_words(n: int) -> str:
        if n == 0:    return 'zero'
        if n < 20:    return _ones[n]
        if n < 100:   return _tens[n // 10] + (f'-{_ones[n % 10]}' if n % 10 else '')
        if n < 1000:  return _ones[n // 100] + ' hundred' + (f' {_int_to_words(n % 100)}' if n % 100 else '')
        if n < 1_000_000:
            return _int_to_words(n // 1000) + ' thousand' + (f' {_int_to_words(n % 1000)}' if n % 1000 else '')
        return _int_to_words(n // 1_000_000) + ' million' + (f' {_int_to_words(n % 1_000_000)}' if n % 1_000_000 else '')

    def _replace(m):
        raw = m.group(0).replace(',', '').replace('$', '')
        if 'M' in raw.upper():
            val = float(raw.upper().replace('M', ''))
            if val == int(val):
                return f'{_int_to_words(int(val))} million dollars'
            return f'{val} million dollars'
        if 'K' in raw.upper():
            val = float(raw.upper().replace('K', ''))
            rounded = round(val)
            return f'{_int_to_words(rounded)} thousand dollars'
        # Plain number with commas
        val = int(float(raw))
        if val >= 1000:
            rounded = round(val / 1000) * 1000
            return f'{_int_to_words(rounded // 1000)} thousand dollars'
        return f'{_int_to_words(val)} dollars'

    # Match $36K, $1.2M, $36,428, $100
    return re.sub(r'\$[\d,]+(?:\.\d+)?[KkMm]?', _replace, text)


def generate_voiceover(
    script: str,
    voice: str = "onyx",      # Deep authoritative male — ideal for financial/trading content
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

    import time

    def _call_tts_api(model: str, payload_extra: dict) -> str | None:
        """Make one TTS API call. Returns output_path on success, None on failure."""
        for attempt in range(1, 4):
            try:
                payload = {
                    "model":           model,
                    "input":           _expand_numbers(script),
                    "voice":           voice,
                    "response_format": "mp3",
                    **payload_extra,
                }
                resp = requests.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                             "Content-Type":  "application/json"},
                    json=payload,
                    timeout=40,
                )
                resp.raise_for_status()
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                log.info(f"[TTS:{model}] Voiceover saved → {output_path} ({len(resp.content):,} bytes)")
                return output_path

            except requests.exceptions.HTTPError as e:
                status = getattr(resp, "status_code", "?")
                log.error(f"[TTS:{model}] HTTP {status} on attempt {attempt}/3: {e}")
                if status == 429 and attempt < 3:
                    # Use longer fixed delays — OpenAI RPM windows reset over 60s.
                    # 2^attempt (2s, 4s) is far too short to clear the rate limit.
                    backoff = 15 * attempt  # 15s after attempt 1, 30s after attempt 2
                    log.warning(f"[TTS:{model}] Rate-limited. Waiting {backoff}s before retry...")
                    time.sleep(backoff)
                    continue
                return None   # non-429 errors or final attempt — bail
            except Exception as e:
                log.error(f"[TTS:{model}] Exception on attempt {attempt}/3: {e}")
                if attempt < 3:
                    backoff = 15 * attempt
                    log.warning(f"[TTS:{model}] Retrying in {backoff}s...")
                    time.sleep(backoff)
        return None

    # ── Primary: gpt-4o-mini-tts with expressive instructions ─────────────────
    # NOTE: gpt-4o-mini-tts does NOT support the 'speed' parameter.
    # Pacing is controlled entirely via the 'instructions' field.
    result = _call_tts_api(
        model="gpt-4o-mini-tts",
        payload_extra={
            "instructions": (
                "You are a fired-up sports commentator delivering a breaking financial news alert. "
                "Your delivery must be HIGH ENERGY and URGENT — like you just saw something unbelievable. "
                "Hit ALL CAPS words with heavy vocal stress and genuine excitement. "
                "Treat '...' as a dramatic pause — breathe and let the silence land before continuing. "
                "Treat '—' as a sharp tonal drop followed by a punchy emphasis. "
                "Keep the pace fast and driven, but always let the dollar amount breathe. "
                "Sound like you genuinely can't believe what you're seeing. Not robotic. Not flat. ALIVE."
            ),
        },
    )
    if result:
        return result

    # ── Fallback: tts-1-hd (supports 'speed', no 'instructions') ─────────────
    # Triggered when gpt-4o-mini-tts is unavailable or returns a non-retryable error.
    # Wait 20s before hitting the fallback — same API key, same rate limit pool.
    log.warning("[TTS] gpt-4o-mini-tts failed — waiting 20s before tts-1-hd fallback...")
    time.sleep(20)
    log.warning("[TTS] Attempting tts-1-hd at 1.1x speed.")
    result = _call_tts_api(
        model="tts-1-hd",
        payload_extra={"speed": 1.1},
    )
    if result:
        return result

    log.error("[TTS] Both TTS models failed. No audio will be generated.")
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
