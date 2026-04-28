from __future__ import annotations
import os
import json
import uuid
import random
import io
import ssl
import time
import logging
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'marketing')

# ── Cinematic prompt themes, grouped by market category ───────────────────────
# Prompts are optimized for Kling v2.6 Pro: specific, cinematic, with strong motion verbs.
THEMES_CRYPTO = [
    "extreme close-up of molten gold dripping into dark water, slow motion, cinematic lighting",
    "aerial shot of glowing circuit board city at night, neon green and blue lights, slow push in",
    "abstract silver liquid mercury flowing and morphing in slow motion, macro lens, dark studio",
    "slow motion shot of laser beams cutting through thick dark smoke in a server room",
    "cinematic orbit around a glowing neon-lit bitcoin symbol in deep space, slow rotation",
]
THEMES_POLITICAL = [
    "slow motion shot of an American flag rippling in strong wind at dusk, dramatic lighting",
    "cinematic aerial pullback from the Capitol Building at blue hour, mist in the air",
    "extreme slow motion chess pieces falling in sequence on a dark marble table",
    "cinematic close-up of a spinning globe with glowing country borders, slow rotation, dark BG",
    "slow dramatic push into a dark room with one spotlight illuminating a single decision table",
]
THEMES_SPORTS = [
    "cinematic slow motion shot of a soccer ball curling through a floodlit stadium at night",
    "extreme slow motion of confetti falling in a packed stadium, golden light rays",
    "dramatic slow push into an empty soccer stadium at night, floodlights blazing through fog",
    "slow motion aerial shot of a packed World Cup stadium, crowd lights glowing in the dark",
    "cinematic slow motion of cleats hitting wet grass in a floodlit pitch, water droplets spraying",
]
THEMES_DEFAULT = [
    "cinematic slow dolly shot through a dark luxury trading floor with glowing Bloomberg terminals",
    "extreme close-up of a stock ticker tape unfurling in slow motion, golden numbers glowing",
    "slow motion aerial pullback from a glowing city skyline at 3am, golden light trails",
    "cinematic slow pan across a dark glass skyscraper reflecting city lights at night",
    "macro close-up of hundred dollar bills raining in slow motion in a dark room, dramatic light",
    "slow motion shot of a luxury watch ticking in extreme close-up, dark cinematic background",
    "aerial slow push over a dark ocean at night with glowing city lights on the horizon",
    "cinematic slow motion shot of gold bars being stacked in a vault, dramatic rim lighting",
    "slow dramatic zoom on a glowing red stock market graph on a dark trading screen",
    "extreme slow motion of a hand placing a chess king on a dark marble board, spotlight from above",
]

THEMES = THEMES_DEFAULT  # legacy alias

_CRYPTO_KEYWORDS    = {"btc", "eth", "bitcoin", "ethereum", "crypto", "sol", "xrp", "doge", "defi", "nft"}
_POLITICAL_KEYWORDS = {"trump", "election", "president", "senate", "congress", "fed", "rate", "policy",
                       "vote", "democrat", "republican", "white house", "supreme", "tariff", "nato"}
_SPORTS_KEYWORDS    = {"nfl", "nba", "mlb", "nhl", "soccer", "fifa", "ufc", "mma", "tennis", "golf",
                       "dota", "lol", "esport", "game", "match", "winner", "celtics", "lakers",
                       "team liquid", "super bowl", "world cup", "playoff", "championship"}


def _pick_theme(market_hint: str = "") -> str:
    """Select a theme pool matching the market category."""
    hint = market_hint.lower()
    if any(k in hint for k in _CRYPTO_KEYWORDS):
        return random.choice(THEMES_CRYPTO)
    if any(k in hint for k in _POLITICAL_KEYWORDS):
        return random.choice(THEMES_POLITICAL)
    if any(k in hint for k in _SPORTS_KEYWORDS):
        return random.choice(THEMES_SPORTS)
    return random.choice(THEMES_DEFAULT)


VERTICAL_RATIO = "9:16"   # TikTok / Reels / Shorts format



# ── Fallback: local grid (emergency only) ─────────────────────────────────────
# ── Fallback: local grid (emergency only) ─────────────────────────────────────
def _generate_fallback_grid(width: int = 1080, height: int = 1920) -> str:
    # First, try to salvage a previously generated cinematic Kling video!
    import glob
    existing_bgs = glob.glob(os.path.join(OUTPUT_DIR, "kling_bg_*.mp4"))
    if existing_bgs:
        chosen = random.choice(existing_bgs)
        log.warning(f"[Offline] APIs unreachable. Reusing cached cinematic background: {chosen}")
        return chosen

    log.warning("Generating local fallback grid (all API paths exhausted and no cached videos found).")
    img = Image.new('RGB', (width, height), (10, 15, 30))
    draw = ImageDraw.Draw(img)
    for i in range(0, max(width, height), 100):
        alpha = 100 if i % 400 == 0 else 30
        if i < width:
            draw.line([(i, 0), (i, height)], fill=(0, 230, 240, alpha), width=3)
        if i < height:
            draw.line([(0, i), (width, i)], fill=(0, 230, 240, alpha), width=3)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"bg_fallback_{uuid.uuid4().hex[:6]}.png")
    img.save(out_path)
    return out_path


# ── Path 1: Kling v2.6 Pro via fal.ai (kinetic motion video) ─────────────────
def _generate_kling_background(theme: str, fal_key: str) -> str | None:
    """
    Generate a 5-second 9:16 kinetic background video using Kling v2.6 Pro.
    Returns local path to the MP4 file, or None on failure.
    Generation takes ~60–120 seconds.
    """
    try:
        import fal_client
    except ImportError:
        log.error("fal-client not installed.")
        return None

    os.environ["FAL_KEY"] = fal_key

    prompt = (
        f"Breathtaking vertical cinematic video of {theme}. "
        "Deep dark blues, slate tones, glowing neon cyans and electric purples. "
        "Smooth slow camera motion, fluid abstract movement. "
        "Premium finance tech aesthetic. Absolutely no text, no letters, no people."
    )

    log.info(f"[Kling] Requesting kinetic background: '{theme}'")
    try:
        handler = fal_client.submit(
            "fal-ai/kling-video/v2.6/pro/text-to-video",
            arguments={
                "prompt": prompt,
                "aspect_ratio": VERTICAL_RATIO,
                "duration": "5",          # 5 seconds — enough for seamless Creatomate loop
            },
        )
        result = handler.get()
        video_url = result["video"]["url"]
        log.info(f"[Kling] Video generated → {video_url}")

        # Download the MP4 locally
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx) as r:
            video_data = r.read()

        ts = __import__('datetime').datetime.now(
            __import__('datetime').timezone.utc
        ).strftime("%Y%m%d_%H%M%S")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path = os.path.join(OUTPUT_DIR, f"kling_bg_{ts}.mp4")
        with open(out_path, "wb") as f:
            f.write(video_data)

        log.info(f"[Kling] Background video saved → {out_path} ({len(video_data):,} bytes)")
        return out_path

    except Exception as e:
        log.error(f"[Kling] Generation failed: {e}")
        return None


# ── Path 2: DALL-E 3 (static HD image, fast & cheap) ─────────────────────────
def _generate_dalle_background(theme: str, api_key: str) -> str | None:
    """
    Generate a static HD background using DALL-E 3.
    Returns local path to the PNG file, or None on failure.
    """
    if not OpenAI:
        log.error("openai library not installed.")
        return None

    prompt = (
        f"A breathtaking vertical cinematic photo of {theme}. "
        "Deep dark blues, slate colors, and glowing neon cyans. "
        "Highly abstract, extremely professional, modern, premium finance tech aesthetic. "
        "Absolutely no text, no letters."
    )

    log.info(f"[DALL-E 3] Requesting static background: '{theme}'")
    import time
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            client = OpenAI(api_key=api_key)
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1792",
                quality="hd",
                n=1,
            )
            image_url = response.data[0].url

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx) as r:
                img_data = r.read()

            img = Image.open(io.BytesIO(img_data)).convert("RGB")
            ts = __import__('datetime').datetime.now(
                __import__('datetime').timezone.utc
            ).strftime("%Y%m%d_%H%M%S")
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            out_path = os.path.join(OUTPUT_DIR, f"dalle_bg_{ts}.jpg")
            img.save(out_path, "JPEG", quality=85)
            log.info(f"[DALL-E 3] Background image saved → {out_path}")
            return out_path

        except Exception as e:
            if "429" in str(e):
                log.warning(f"[DALL-E 3] HTTP 429 Rate Limit. (Attempt {attempt}/{max_retries})")
            else:
                log.error(f"[DALL-E 3] Generation Exception: {e}")

            if attempt == max_retries:
                log.error("[DALL-E 3] Generation failed: All retry attempts exhausted.")
                return None

            delay = 2 ** attempt
            log.info(f"⏳ Sleeping {delay}s before DALL-E 3 retry...")
            time.sleep(delay)
            
    return None


# ── Public Interface ───────────────────────────────────────────────────────────
def generate_background(market_hint: str = "") -> str:
    """
    Generate a background asset for the PolyVision video pipeline.

    Args:
        market_hint: Market title string. Routes theme to a category-appropriate
                     pool (crypto/political/sports/default) for visual variety.

    Priority order:
      1. Kling v2.6 Pro via fal.ai (kinetic MP4)
      2. DALL-E 3 (static PNG fallback)
      3. Local grid (emergency fallback)
    """
    secrets_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'secrets.json')
    secrets = {}
    if os.path.exists(secrets_path):
        try:
            with open(secrets_path) as f:
                secrets = json.load(f)
        except Exception:
            pass

    fal_key    = secrets.get("FAL_KEY", os.getenv("FAL_KEY", ""))
    openai_key = secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    theme      = _pick_theme(market_hint)
    log.info(f"[BG] Market: '{market_hint[:40]}' → theme: '{theme[:50]}'")

    # ── 1. Kling kinetic video (best quality) ─────────────────────────────────
    if fal_key:
        result = _generate_kling_background(theme, fal_key)
        if result:
            return result
        log.warning("[Kling] Failed — falling back to DALL-E 3.")

    # ── 2. DALL-E 3 static image ──────────────────────────────────────────────
    if openai_key and OpenAI:
        result = _generate_dalle_background(theme, openai_key)
        if result:
            return result
        log.warning("[DALL-E 3] Failed — using local grid fallback.")

    # ── 3. Emergency local grid ───────────────────────────────────────────────
    return _generate_fallback_grid()
