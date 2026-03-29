#!/usr/bin/env python3
"""
card_generator.py — PolyVision Layer 3 Tool

Generates a branded 1200×675 PNG trade card for attachment to X (Twitter) posts.
Uses Pillow (PIL) only — no external services, no temp files, works headlessly on Railway.

Font strategy: loads bundled NotoSans TTF from brain/assets/fonts/ first,
then falls back to system fonts. This guarantees readable text on Railway.

Usage:
    from tools.card_generator import generate_card
    png_bytes = generate_card(payload)   # returns BytesIO

Self-annealing log:
    2026-03-29: v1 — Initial implementation. Pure Pillow, system font fallback.
    2026-03-29: v2 — Bundle NotoSans TTF to fix Railway font fallback rendering
                     tiny 10px bitmap text. Redesigned layout: hero stat row
                     centered on canvas, larger all text, better visual density.
"""

import io
import os
import logging
import textwrap
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# ── Brand Colours ─────────────────────────────────────────────────────────────
BG_DARK   = (10,  13,  18)    # deep navy black
BG_CARD   = (18,  23,  32)    # card surface
BG_BADGE  = (30,  40,  56)    # badge background
MINT      = (0,   230, 150)   # YES accent — slightly warmer green
ROSE      = (255, 70,  100)   # NO accent
AMBER     = (245, 166, 35)    # neutral/large
WHITE     = (235, 242, 250)   # primary text
MUTED     = (120, 135, 150)   # secondary text
DIM       = (60,  72,  86)    # very muted / separator

W, H = 1200, 675

# ── Bundled font paths ─────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_HERE, "..", "assets", "fonts")

BUNDLED_BOLD    = os.path.join(_FONT_DIR, "NotoSans-Bold.ttf")
BUNDLED_REGULAR = os.path.join(_FONT_DIR, "NotoSans-Regular.ttf")

# System font fallback candidates (Linux Railway + macOS)
_SYS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
_SYS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load font at given size. Tries bundled font first, then system, then default."""
    primary = BUNDLED_BOLD if bold else BUNDLED_REGULAR
    candidates = [primary] + (_SYS_BOLD if bold else _SYS_REGULAR)
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    log.warning(f"[CardGen] No TTF font found — falling back to bitmap default (text will be tiny). "
                f"Ensure brain/assets/fonts/NotoSans-Bold.ttf exists in the repo.")
    return ImageFont.load_default()


def _truncate(text: str, font, draw: ImageDraw.Draw, max_width: int) -> str:
    """Truncate text with ellipsis to fit within max_width pixels."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _fmt_usd(value: float) -> str:
    """Format large dollar amounts: $69,589 → $69.6K, $1,200,000 → $1.2M"""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def _draw_gradient(draw: ImageDraw.Draw, accent_rgb: tuple):
    """Draw a subtle radial-ish gradient glow from bottom-right corner."""
    r, g, b = accent_rgb
    # bottom-right warm glow
    for i in range(220):
        alpha = int(28 * (1 - i / 220))
        y_start = H - i * 3
        if y_start < 0:
            y_start = 0
        draw.rectangle(
            [(W - i * 6, y_start), (W, H)],
            fill=(r, g, b, alpha),
        )
    # very faint top-left opposite glow
    for i in range(80):
        alpha = int(10 * (1 - i / 80))
        draw.rectangle([(0, 0), (i * 8, i * 5)], fill=(r, g, b, alpha))


def generate_card(payload: dict) -> io.BytesIO:
    """
    Generate a branded PolyVision trade card image.
    Returns a BytesIO PNG buffer, ready for tweepy media_upload.
    """
    # ── Extract fields ────────────────────────────────────────────────────────
    handle    = payload.get("trader_handle") or payload.get("handle") or "Anonymous"
    market    = payload.get("market_title", "Unknown Market")
    outcome   = str(payload.get("outcome", "YES")).upper()
    price     = float(payload.get("price", 0.5))
    usd_value = float(payload.get("usd_value", 0))
    source    = str(payload.get("source", "POLYMARKET")).upper()
    tier      = str(payload.get("alert_tier", "STANDARD")).upper()
    win_rate  = payload.get("wallet_win_rate")

    platform   = "Kalshi" if source == "KALSHI" else "Polymarket"
    accent     = MINT if outcome in ("YES", "CANUCKS", "OILERS", "MAVS") or (
        outcome not in ("NO",) and outcome == outcome.upper() and outcome != "NO"
    ) else ROSE
    # Simpler: YES/NO binary; anything not "NO" treated as affirmative
    accent     = ROSE if outcome == "NO" else MINT

    is_whale   = tier == "WHALE" or usd_value >= 50_000
    tier_label = "🐋  WHALE" if is_whale else "🔵  STANDARD"
    pct_str    = f"@ {price:.0%}"
    usd_str    = _fmt_usd(usd_value)
    wr_str     = f"{win_rate:.0%} win rate" if win_rate else "Win Rate: TBD"

    # ── Canvas ────────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img, "RGBA")

    # Background gradient
    _draw_gradient(draw, accent)

    # ── Card frame ────────────────────────────────────────────────────────────
    PAD = 40
    draw.rounded_rectangle(
        [PAD, PAD, W - PAD, H - PAD],
        radius=22,
        fill=BG_CARD,
        outline=(*accent, 55),
        width=2,
    )

    # ── Fonts ─────────────────────────────────────────────────────────────────
    f_logo    = _font(28, bold=True)
    f_badge   = _font(20, bold=True)
    f_handle  = _font(52, bold=True)    # trader name — BIG
    f_winrate = _font(22, bold=False)
    f_market  = _font(30, bold=False)   # market name — medium
    f_hero    = _font(90, bold=True)    # $71.K — HERO size
    f_outcome = _font(64, bold=True)    # YES / NO
    f_pct     = _font(46, bold=False)   # @ 41%
    f_footer  = _font(19, bold=False)

    INNER_L = PAD + 44
    INNER_R = W - PAD - 44

    # ── PolyVision logo — top left ────────────────────────────────────────────
    logo_y = PAD + 30
    draw.text((INNER_L, logo_y), "⬥ PolyVision", font=f_logo, fill=MINT)

    # ── Tier badge — top right ────────────────────────────────────────────────
    badge_bbox  = draw.textbbox((0, 0), tier_label, font=f_badge)
    badge_w     = badge_bbox[2] - badge_bbox[0] + 32
    badge_h     = badge_bbox[3] - badge_bbox[1] + 16
    badge_x     = INNER_R - badge_w
    badge_y     = logo_y - 2
    draw.rounded_rectangle(
        [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
        radius=10,
        fill=BG_BADGE,
        outline=(*accent, 80),
        width=1,
    )
    draw.text((badge_x + 16, badge_y + 8), tier_label, font=f_badge, fill=WHITE)

    # ── Thin separator ────────────────────────────────────────────────────────
    sep_y = logo_y + 52
    draw.line([(INNER_L, sep_y), (INNER_R, sep_y)], fill=(*accent, 35), width=1)

    # ── Trader handle ─────────────────────────────────────────────────────────
    handle_y = sep_y + 30
    safe_handle = _truncate(handle, f_handle, draw, INNER_R - INNER_L)
    draw.text((INNER_L, handle_y), safe_handle, font=f_handle, fill=WHITE)

    # Win rate — inline right of handle, vertically centred
    wr_x = INNER_L + draw.textlength(safe_handle, font=f_handle) + 20
    wr_y = handle_y + (52 - 22) // 2 + 4   # vertically centre against f_handle
    draw.text((wr_x, wr_y), wr_str, font=f_winrate, fill=MUTED)

    # ── Market title ──────────────────────────────────────────────────────────
    market_y = handle_y + 68
    # Wrap across max 2 lines; truncate each line if too wide
    wrapped = textwrap.wrap(market, width=52)[:2]
    for i, line in enumerate(wrapped):
        safe = _truncate(line, f_market, draw, INNER_R - INNER_L)
        draw.text((INNER_L, market_y + i * 40), safe, font=f_market, fill=MUTED)

    # ── HERO stats row — SIZE  OUTCOME  @PCT ─────────────────────────────────
    # Positioned to fill the bottom-centre zone prominently
    hero_y = market_y + (len(wrapped) * 40) + 38

    # Dollar amount (huge)
    draw.text((INNER_L, hero_y), usd_str, font=f_hero, fill=accent)
    hero_usd_w = draw.textlength(usd_str, font=f_hero)

    # Outcome label (YES/NO or team name)
    outcome_x = INNER_L + hero_usd_w + 32
    # Vertically align with bottom of hero text
    hero_bbox = draw.textbbox((0, 0), usd_str, font=f_hero)
    hero_h = hero_bbox[3] - hero_bbox[1]
    out_bbox = draw.textbbox((0, 0), outcome, font=f_outcome)
    out_h    = out_bbox[3] - out_bbox[1]
    outcome_y = hero_y + (hero_h - out_h)        # bottom-align with hero number
    draw.text((outcome_x, outcome_y), outcome, font=f_outcome, fill=accent)
    outcome_w = draw.textlength(outcome, font=f_outcome)

    # Percentage (muted, right of outcome)
    pct_x = outcome_x + outcome_w + 24
    pct_bbox = draw.textbbox((0, 0), pct_str, font=f_pct)
    pct_h    = pct_bbox[3] - pct_bbox[1]
    pct_y    = hero_y + (hero_h - pct_h)         # bottom-align
    draw.text((pct_x, pct_y), pct_str, font=f_pct, fill=MUTED)

    # ── Footer line ──────────────────────────────────────────────────────────
    footer_y = H - PAD - 48
    draw.line([(INNER_L, footer_y - 14), (INNER_R, footer_y - 14)],
              fill=(*accent, 30), width=1)
    draw.text((INNER_L, footer_y), platform, font=f_footer, fill=MUTED)
    tagline = "Real-time Whale Intelligence  ⬥  polyvision.app"
    tag_w   = draw.textlength(tagline, font=f_footer)
    draw.text((INNER_R - tag_w, footer_y), tagline, font=f_footer, fill=MUTED)

    # ── Export ────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    log.info(f"[CardGen] v2 rendered: {handle[:30]} | {market[:40]} | {usd_str} {outcome}")
    return buf


# ── CLI preview ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO)

    sample = {
        "trader_handle":   "GamblingIsAllYouNeed",
        "market_title":    "Canucks vs. Flames",
        "outcome":         "CANUCKS",
        "price":           0.41,
        "usd_value":       70_848,
        "source":          "POLYMARKET",
        "alert_tier":      "WHALE",
        "wallet_win_rate": None,
    }

    if len(sys.argv) > 1:
        sample = json.loads(sys.argv[1])

    buf = generate_card(sample)
    out_path = ".tmp/card_preview_v2.png"
    os.makedirs(".tmp", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(buf.read())
    print(f"✅ Card v2 saved to {out_path}")
