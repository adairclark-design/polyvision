#!/usr/bin/env python3
"""
card_generator.py — PolyVision Layer 3 Tool

v4: "Dynamic 4-Variant Card Engine"
Generates a branded 1200×675 PNG trade card for attachment to X (Twitter) posts.
Uses Pillow (PIL) only — no external services, works headlessly on Railway.

Variants (auto-selected by trade tier + randomness):
  - Terminal : Mega Whale ($1M+)  — Gold/Black Bloomberg terminal aesthetic
  - Pulse    : Super Whale ($250k+) — Radial glow burst, high drama
  - Receipt  : Whale ($50k+)       — Upgraded glassmorphism two-pane layout
  - Stealth  : Standard / Kalshi   — Minimal near-black single accent line

Visual upgrades across all variants:
  - Hash-based per-market accent color (every market looks unique)
  - Probability gauge bar (replaces raw "@ 53%" text)
  - Market category auto-tag (POLITICS / FINANCE / SPORTS / OTHER)
  - Urgency timestamp ("Detected just now")
  - Win-rate segmented bar visualization
"""

import io
import os
import math
import random
import hashlib
import logging
import textwrap
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont, ImageFilter

log = logging.getLogger(__name__)

# ── Canvas  ───────────────────────────────────────────────────────────────────
W, H = 1200, 675

# ── Brand Foundation Palette ──────────────────────────────────────────────────
BG_VOID     = (6,   8,   12)    # deepest black
BG_DARK     = (10,  13,  18)    # base background
BG_CARD     = (16,  21,  30)    # card surface
BG_PANE     = (22,  29,  44)    # secondary pane
BG_BADGE    = (28,  38,  56)    # badge background
MINT        = (0,   230, 150)   # YES / primary brand accent
ROSE        = (255, 65,  95)    # NO accent
AMBER       = (245, 166, 35)    # NEUTRAL / warning
GOLD        = (255, 215, 0)     # MEGA tier
ELECTRIC    = (80,  160, 255)   # info / links
WHITE       = (235, 242, 250)   # primary text
MUTED       = (110, 128, 148)   # secondary text
DIM         = (42,  54,  70)    # separators / borders
NEAR_BLACK  = (14,  18,  26)    # near-black for stealth

# Market category palette
CAT_COLORS = {
    "POLITICS": (180, 80,  255),   # purple
    "FINANCE":  (80,  200, 120),   # green
    "SPORTS":   (255, 140, 40),    # orange
    "CRYPTO":   (100, 180, 255),   # electric blue
    "OTHER":    (140, 150, 165),   # muted
}

# ── Fonts ─────────────────────────────────────────────────────────────────────
_HERE     = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_HERE, "..", "assets", "fonts")
BUNDLED_BOLD    = os.path.join(_FONT_DIR, "NotoSans-Bold.ttf")
BUNDLED_REGULAR = os.path.join(_FONT_DIR, "NotoSans-Regular.ttf")
_SYS_BOLD       = ["/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",    "/System/Library/Fonts/Helvetica.ttc"]
_SYS_REGULAR    = ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/System/Library/Fonts/Helvetica.ttc"]

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [BUNDLED_BOLD if bold else BUNDLED_REGULAR] + (_SYS_BOLD if bold else _SYS_REGULAR)
    for path in candidates:
        try: return ImageFont.truetype(path, size)
        except (IOError, OSError): continue
    return ImageFont.load_default()

# ── Utility Helpers ───────────────────────────────────────────────────────────
def _truncate(text: str, font, draw: ImageDraw.Draw, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w: return text
    while text and draw.textlength(text + "…", font=font) > max_w: text = text[:-1]
    return text + "…"

def _fmt_usd(value: float) -> str:
    """Format USD value compactly, stripping unnecessary .0 decimals.
    e.g. $420,000 → $420K  (not $420.0K)
         $1,000,000 → $1M  (not $1.0M)
         $1,250,000 → $1.3M
         $1,500 → $1.5K
    """
    if value >= 1_000_000:
        formatted = value / 1_000_000
        return f"${formatted:.0f}M" if formatted == int(formatted) else f"${formatted:.1f}M"
    if value >= 1_000:
        formatted = value / 1_000
        return f"${formatted:.0f}K" if formatted == int(formatted) else f"${formatted:.1f}K"
    return f"${value:,.0f}"

def _pct_str(price: float) -> str:
    if price >= 0.995: return "99%+"
    if price <= 0.005: return "<1%"
    return f"{price:.0%}"

def _market_accent(market_title: str, base_accent: tuple) -> tuple:
    """Derive a slightly unique hue per market by shifting the base accent using hash."""
    h = int(hashlib.md5(market_title.encode()).hexdigest(), 16)
    shift = (h % 40) - 20  # -20 to +20 shift on each channel
    r = max(0, min(255, base_accent[0] + shift))
    g = max(0, min(255, base_accent[1] + (shift // 2)))
    b = max(0, min(255, base_accent[2] - shift))
    return (r, g, b)

def _categorize(market_title: str) -> str:
    """Auto-classify a market title into a display category."""
    title_lower = market_title.lower()
    politics_terms = ["trump", "biden", "election", "president", "congress", "senate", "vote",
                      "democrat", "republican", "fed ", "tariff", "war", "nato", "supreme court",
                      "impeach", "indicted", "poll", "inaugurat", "executive"]
    finance_terms  = ["rate", "inflation", "gdp", "recession", "fed rate", "dow", "s&p", "nasdaq",
                      "market", "stock", "economy", "unemployment", "cpi", "jobs report"]
    crypto_terms   = ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol", "xrp",
                      "doge", "coin", "defi", "nft", "blockchain", "token"]
    sports_terms   = ["nfl", "nba", "mlb", "nhl", "fifa", "premier league", "super bowl",
                      "championship", " vs ", " vs. ", "league", "playoffs", "final", "match",
                      "game", "season", "trade deadline", "mvp", "draft", "score"]
    for t in politics_terms:
        if t in title_lower: return "POLITICS"
    for t in crypto_terms:
        if t in title_lower: return "CRYPTO"
    for t in finance_terms:
        if t in title_lower: return "FINANCE"
    for t in sports_terms:
        if t in title_lower: return "SPORTS"
    return "OTHER"

CATEGORY_ICONS = {
    "POLITICS": "POLITICS",
    "FINANCE":  "FINANCE",
    "SPORTS":   "SPORTS",
    "CRYPTO":   "CRYPTO",
    "OTHER":    "MARKET",
}

# ── Shared Drawing Primitives ─────────────────────────────────────────────────
def _glow_layer(base_img: Image.Image, draw: ImageDraw.Draw, x: int, y: int,
                text: str, font, fill: tuple, glow: tuple, radius: int = 14, passes: int = 3):
    """Renders text with a blurred glow halo for dramatic effect."""
    txt_layer = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(txt_layer)
    td.text((x, y), text, font=font, fill=(*glow[:3], 180))
    blurred = txt_layer.filter(ImageFilter.GaussianBlur(radius=radius))
    for _ in range(passes):
        base_img.alpha_composite(blurred)
    draw.text((x, y), text, font=font, fill=fill)

def _draw_probability_bar(draw: ImageDraw.Draw, x: int, y: int, width: int, height: int,
                          probability: float, accent: tuple, outcome: str):
    """Draws a sleek probability gauge with filled/unfilled segments."""
    BAR_SEGS = 20
    seg_w = (width - (BAR_SEGS - 1) * 3) // BAR_SEGS
    filled = max(1, min(BAR_SEGS, round(probability * BAR_SEGS)))

    # unfilled background bars
    for i in range(BAR_SEGS):
        sx = x + i * (seg_w + 3)
        color = (*accent, 200) if i < filled else (*DIM, 255)
        draw.rounded_rectangle([sx, y, sx + seg_w, y + height], radius=3, fill=color)

    # Probability label
    f_pct_label = _font(22, bold=True)
    pct_label = _pct_str(probability)
    draw.text((x + width + 14, y - 2), pct_label, font=f_pct_label, fill=(*accent, 255))

def _draw_win_rate_bar(draw: ImageDraw.Draw, x: int, y: int, width: int,
                       win_rate, accent: tuple):
    """Draws a compact win rate bar. Handles None/0 gracefully."""
    f_label = _font(19, bold=True)
    f_tiny  = _font(17, bold=False)

    if win_rate is not None and win_rate > 0.0:
        filled_w = int(width * win_rate)
        # Background track
        draw.rounded_rectangle([x, y, x + width, y + 10], radius=5, fill=(*DIM, 255))
        # Filled portion
        bar_color = MINT if win_rate >= 0.55 else (ROSE if win_rate <= 0.45 else AMBER)
        draw.rounded_rectangle([x, y, x + filled_w, y + 10], radius=5, fill=(*bar_color, 255))
        draw.text((x, y + 16), f"Win Rate: {win_rate:.0%}", font=f_label, fill=(*bar_color, 255))
    else:
        draw.text((x, y + 4), "Win Rate  ·  Insufficient Data", font=f_tiny, fill=(*DIM, 255))

def _draw_category_tag(draw: ImageDraw.Draw, x: int, y: int, category: str):
    """Draws a compact market category pill badge."""
    color = CAT_COLORS.get(category, CAT_COLORS["OTHER"])
    label = CATEGORY_ICONS.get(category, "MARKET")
    f_tag = _font(18, bold=True)
    tw = draw.textlength(label, font=f_tag)
    pad_x, pad_y = 14, 7
    draw.rounded_rectangle(
        [x, y, x + tw + pad_x * 2, y + 32],
        radius=16, fill=(*color, 30), outline=(*color, 140), width=1
    )
    draw.text((x + pad_x, y + pad_y), label, font=f_tag, fill=(*color, 255))
    return int(tw + pad_x * 2) # return tag width for chaining

def _urgency_label() -> str:
    """Returns a human-readable freshness label."""
    labels = ["Detected just now", "Live signal", "Just hit the wire", "Breaking: just posted"]
    return random.choice(labels)

def _draw_logo(draw: ImageDraw.Draw, x: int, y: int, accent: tuple):
    f = _font(26, bold=True)
    draw.text((x, y), "◈ PolyVision", font=f, fill=(*accent, 255))

def _draw_scanlines(draw: ImageDraw.Draw, w: int, h: int, alpha: int = 6):
    """Draws ultra-subtle horizontal scan lines for depth."""
    for y in range(0, h, 4):
        draw.line([(0, y), (w, y)], fill=(255, 255, 255, alpha))

def _draw_dot_grid(draw: ImageDraw.Draw, w: int, h: int, color: tuple, spacing: int = 28):
    """Draws a dot-matrix grid texture."""
    for gx in range(0, w, spacing):
        for gy in range(0, h, spacing):
            draw.ellipse([gx - 1, gy - 1, gx + 1, gy + 1], fill=(*color, 18))

def _fit_handle(draw: ImageDraw.Draw, handle: str, max_width: int, sizes=(46, 36, 28)) -> tuple:
    """
    Font-size cascade: tries each size in 'sizes' until the handle text fits
    within max_width WITHOUT truncation. Returns (font, fitted_text).
    Falls back to truncation at the smallest size only if all sizes are too large.
    """
    for sz in sizes:
        f = _font(sz, bold=True)
        if draw.textlength(handle, font=f) <= max_width:
            return f, handle
    # Final fallback: truncate at smallest size only
    f = _font(sizes[-1], bold=True)
    return f, _truncate(handle, f, draw, max_width)

# ══════════════════════════════════════════════════════════════════════════════
#  VARIANT 1: TERMINAL  (Mega Whale $1M+) — Gold / Black Bloomberg wall
# ══════════════════════════════════════════════════════════════════════════════
def _render_terminal(img: Image.Image, draw: ImageDraw.Draw, data: dict, accent: tuple):
    """Full-width gold terminal aesthetic. Maximum prestige statement."""
    usd_str  = data["usd_str"]
    outcome  = data["outcome"]
    market   = data["market"]
    platform = data["platform"]
    handle   = data["handle"]
    pct      = data["pct"]
    price    = data["price"]
    category = data["category"]
    win_rate = data["win_rate"]
    PAD      = 48

    # ── Gold scan lines on near-black ─────────────────────────────────────────
    _draw_scanlines(draw, W, H, alpha=5)

    # Horizontal accent separator (top & bottom)
    draw.rectangle([(0, PAD + 60), (W, PAD + 62)], fill=(*GOLD, 80))
    draw.rectangle([(0, H - PAD - 62), (W, H - PAD - 60)], fill=(*GOLD, 40))

    # ── Left column: Identity block ────────────────────────────────────────────
    _draw_logo(draw, PAD, PAD + 14, GOLD)
    _draw_category_tag(draw, PAD, PAD + 90, category)

    f_handle, fitted = _fit_handle(draw, handle, 460, sizes=(52, 40, 30))
    f_mkt    = _font(28, bold=False)
    f_plat   = _font(22, bold=False)

    draw.text((PAD, PAD + 150), fitted, font=f_handle, fill=WHITE)
    _draw_win_rate_bar(draw, PAD, PAD + 222, 320, win_rate, GOLD)

    # Market title — wrapped
    for i, line in enumerate(textwrap.wrap(market, 36)[:3]):
        draw.text((PAD, PAD + 278 + i * 38), line, font=f_mkt, fill=MUTED)

    draw.text((PAD, H - PAD - 48), platform, font=f_plat, fill=MUTED)

    # ── Center: Massive dollar glow display ────────────────────────────────────
    f_mega   = _font(160, bold=True)
    usd_w    = draw.textlength(usd_str, font=f_mega)
    center_x = (W - usd_w) // 2
    center_y = (H - 160) // 2 - 20

    _glow_layer(img, draw, center_x, center_y, usd_str, f_mega, GOLD, GOLD, radius=28, passes=5)

    # Outcome row below dollar
    f_out = _font(52, bold=True)
    icon_col = MINT if outcome != "NO" else ROSE
    out_label = f"▲ {outcome}" if outcome != "NO" else f"▼ {outcome}"
    out_w = draw.textlength(out_label, font=f_out)
    draw.text(((W - out_w) // 2, center_y + 170), out_label, font=f_out, fill=(*icon_col, 255))

    # Probability gauge
    _draw_probability_bar(draw, (W - 380) // 2, center_y + 237, 280, 12, price, GOLD, outcome)

    # ── Right column: Tier + urgency ───────────────────────────────────────────
    f_tier   = _font(28, bold=True)
    f_urgent = _font(20, bold=False)
    tier_text = "◆  MEGA WHALE"
    tier_w = draw.textlength(tier_text, font=f_tier)

    draw.text((W - PAD - tier_w, PAD + 14), tier_text, font=f_tier, fill=(*GOLD, 255))
    draw.text((W - PAD - draw.textlength("polyvision.app", font=f_urgent), H - PAD - 48),
              "polyvision.app", font=f_urgent, fill=(*GOLD, 180))

    urgency = _urgency_label()
    urg_w = draw.textlength(urgency, font=f_urgent)
    draw.text((W - PAD - urg_w, H - PAD - 74), urgency, font=f_urgent, fill=(*MUTED, 200))


# ══════════════════════════════════════════════════════════════════════════════
#  VARIANT 2: PULSE  (Super Whale $250k+) — Radial burst, maximum energy
# ══════════════════════════════════════════════════════════════════════════════
def _render_pulse(img: Image.Image, draw: ImageDraw.Draw, data: dict, accent: tuple):
    """Concentric glow rings emanating from the USD hero number."""
    usd_str  = data["usd_str"]
    outcome  = data["outcome"]
    market   = data["market"]
    platform = data["platform"]
    handle   = data["handle"]
    price    = data["price"]
    category = data["category"]
    win_rate = data["win_rate"]
    PAD      = 48
    accent_a = (*accent, 255)

    # ── Radial glow rings centered around USD amount ───────────────────────────
    cx, cy = W // 2, H // 2 - 30
    for i in range(8, 0, -1):
        radius = 80 + i * 55
        alpha  = max(4, 35 - i * 4)
        ring_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring_layer)
        rd.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                   outline=(*accent, alpha), width=2)
        img.alpha_composite(ring_layer)

    # Thin radial lines
    _draw_dot_grid(draw, W, H, accent, spacing=32)

    # ── Logo top-left ──────────────────────────────────────────────────────────
    _draw_logo(draw, PAD, PAD + 14, accent)
    _draw_category_tag(draw, PAD, PAD + 60, category)

    # ── Hero USD — dead center with glow ──────────────────────────────────────
    f_hero = _font(130, bold=True)
    usd_w  = draw.textlength(usd_str, font=f_hero)
    hx = (W - usd_w) // 2
    hy = (H - 130) // 2 - 50
    _glow_layer(img, draw, hx, hy, usd_str, f_hero, accent, accent, radius=24, passes=4)

    # Outcome row
    f_out   = _font(48, bold=True)
    out_col = MINT if outcome != "NO" else ROSE
    arrow   = "▲" if outcome != "NO" else "▼"
    out_str = f"{arrow} {outcome}"
    out_w   = draw.textlength(out_str, font=f_out)
    draw.text(((W - out_w) // 2, hy + 140), out_str, font=f_out, fill=(*out_col, 255))

    # Probability bar
    _draw_probability_bar(draw, (W - 320) // 2, hy + 206, 240, 10, price, accent, outcome)

    # ── Bottom strip: handle + win-rate left, tier right ──────────────────────
    f_mkt    = _font(24, bold=False)
    f_plat   = _font(20, bold=False)
    f_tier   = _font(24, bold=True)

    draw.line([(PAD, H - PAD - 120), (W - PAD, H - PAD - 120)], fill=(*DIM, 255), width=1)
    f_handle, fitted = _fit_handle(draw, data['handle'], 520, sizes=(38, 30, 24))
    draw.text((PAD, H - PAD - 110), fitted, font=f_handle, fill=WHITE)
    _draw_win_rate_bar(draw, PAD, H - PAD - 60, 300, win_rate, accent)

    tier_label = "●  SUPER WHALE"
    tier_w = draw.textlength(tier_label, font=f_tier)
    draw.text((W - PAD - tier_w, H - PAD - 110), tier_label, font=f_tier, fill=accent_a)

    # Market title truncated at bottom right
    mkt_short = _truncate(market, f_mkt, draw, 420)
    mkt_w     = draw.textlength(mkt_short, font=f_mkt)
    draw.text((W - PAD - mkt_w, H - PAD - 70), mkt_short, font=f_mkt, fill=MUTED)

    # Footer row — kept well below the win rate label (win rate label sits at H-PAD-44)
    urgency = _urgency_label()
    urg_w   = draw.textlength(urgency, font=f_plat)
    draw.text((W - PAD - urg_w, H - PAD - 18), urgency, font=f_plat, fill=(*MUTED, 180))
    draw.text((PAD, H - PAD - 18), f"polyvision.app  ·  {platform}", font=f_plat, fill=(*MUTED, 180))


# ══════════════════════════════════════════════════════════════════════════════
#  VARIANT 3: RECEIPT  (Whale $50k+) — Upgraded glassmorphism two-pane
# ══════════════════════════════════════════════════════════════════════════════
def _render_receipt(img: Image.Image, draw: ImageDraw.Draw, data: dict, accent: tuple):
    """Premium two-pane layout with glassmorphism. The flagship card style."""
    usd_str  = data["usd_str"]
    outcome  = data["outcome"]
    market   = data["market"]
    platform = data["platform"]
    handle   = data["handle"]
    price    = data["price"]
    category = data["category"]
    win_rate = data["win_rate"]
    PAD = 40

    accent_glow = (*accent, 120)

    # ── Dot grid texture ───────────────────────────────────────────────────────
    _draw_dot_grid(draw, W, H, accent, spacing=30)

    # Subtle corner radial gradient (bottom-right)
    r, g, b = accent
    for i in range(120):
        a = max(0, int(20 * (1 - i / 120)))
        draw.rectangle([(W - i * 6, H - i * 4), (W, H)], fill=(r, g, b, a))

    # ── Outer card shell ──────────────────────────────────────────────────────
    draw.rounded_rectangle([PAD, PAD, W - PAD, H - PAD], radius=24, fill=(*BG_CARD, 255))
    draw.rounded_rectangle([PAD, PAD, W - PAD, H - PAD], radius=24, outline=(*accent, 45), width=2)

    # ── Right pane (data receipt) ─────────────────────────────────────────────
    SPLIT_X = 500
    draw.rounded_rectangle([SPLIT_X, PAD + 2, W - PAD - 2, H - PAD - 2],
                            radius=22, fill=(*BG_PANE, 255))
    draw.rectangle([SPLIT_X, PAD + 2, SPLIT_X + 22, H - PAD - 2], fill=(*BG_PANE, 255))
    draw.line([(SPLIT_X, PAD + 2), (SPLIT_X, H - PAD - 2)], fill=(*accent, 40), width=2)

    # ── LEFT PANE ─────────────────────────────────────────────────────────────
    LX = PAD + 38
    LW = SPLIT_X - LX - 16

    _draw_logo(draw, LX, PAD + 32, accent)
    _draw_category_tag(draw, LX, PAD + 76, category)

    f_handle, fitted = _fit_handle(draw, handle, LW, sizes=(46, 36, 28))
    f_mkt    = _font(28, bold=False)
    f_plat   = _font(20, bold=False)

    draw.text((LX, PAD + 136), fitted, font=f_handle, fill=WHITE)
    _draw_win_rate_bar(draw, LX, PAD + 200, 280, win_rate, accent)

    for i, line in enumerate(textwrap.wrap(market, 30)[:3]):
        draw.text((LX, PAD + 252 + i * 42), line, font=f_mkt, fill=MUTED)

    draw.text((LX, H - PAD - 52), platform, font=f_plat, fill=(*MUTED, 200))

    # ── RIGHT PANE ────────────────────────────────────────────────────────────
    RX      = SPLIT_X + 40
    RW      = W - PAD - 40 - RX
    R_RIGHT = W - PAD - 40

    # Tier badge
    f_badge   = _font(20, bold=True)
    tier_text = "◆ WHALE"
    badge_w   = draw.textlength(tier_text, font=f_badge) + 28
    bx        = R_RIGHT - badge_w
    draw.rounded_rectangle([bx, PAD + 36, bx + badge_w, PAD + 68],
                            radius=14, fill=(*BG_BADGE, 255), outline=(*accent, 160), width=1)
    draw.text((bx + 14, PAD + 44), tier_text, font=f_badge, fill=(*accent, 255))

    # Hero dollar amount
    f_hero = _font(100, bold=True)
    usd_w  = draw.textlength(usd_str, font=f_hero)
    hx     = RX + (RW - usd_w) // 2
    hy     = PAD + 100
    _glow_layer(img, draw, hx, hy, usd_str, f_hero, (*accent, 255), accent_glow, radius=16, passes=3)

    # Outcome row
    f_out   = _font(48, bold=True)
    out_col = MINT if outcome != "NO" else ROSE
    arrow   = "▲" if outcome != "NO" else "▼"
    out_str = f"{arrow}  {outcome}"
    out_w   = draw.textlength(out_str, font=f_out)
    ox      = RX + (RW - out_w) // 2
    draw.text((ox, hy + 112), out_str, font=f_out, fill=(*out_col, 255))

    # Probability bar
    bar_w = min(RW - 40, 280)
    bar_x = RX + (RW - bar_w) // 2
    _draw_probability_bar(draw, bar_x, hy + 176, bar_w, 10, price, accent, outcome)

    # Divider line
    draw.line([(RX, H - PAD - 96), (R_RIGHT, H - PAD - 96)], fill=(*DIM, 255), width=1)

    # Footer
    f_footer = _font(18, bold=False)
    urg_w    = draw.textlength(_urgency_label(), font=f_footer)
    urgency  = _urgency_label()
    draw.text((RX, H - PAD - 80), urgency, font=f_footer, fill=(*MUTED, 160))
    pv_w = draw.textlength("polyvision.app", font=f_footer)
    draw.text((R_RIGHT - pv_w, H - PAD - 50), "polyvision.app", font=f_footer, fill=(*MUTED, 200))


# ══════════════════════════════════════════════════════════════════════════════
#  VARIANT 4: STEALTH  (Standard / Kalshi) — Minimal, sophisticated
# ══════════════════════════════════════════════════════════════════════════════
def _render_stealth(img: Image.Image, draw: ImageDraw.Draw, data: dict, accent: tuple):
    """Near-black card with single accent ribbon. Understated but premium."""
    usd_str  = data["usd_str"]
    outcome  = data["outcome"]
    market   = data["market"]
    platform = data["platform"]
    handle   = data["handle"]
    price    = data["price"]
    category = data["category"]
    win_rate = data["win_rate"]
    PAD = 52

    # ── Left accent bar ────────────────────────────────────────────────────────
    draw.rectangle([(PAD, PAD), (PAD + 4, H - PAD)], fill=(*accent, 220))

    # Top & bottom hairlines
    draw.rectangle([(PAD, PAD), (W - PAD, PAD + 1)], fill=(*accent, 60))
    draw.rectangle([(PAD, H - PAD - 1), (W - PAD, H - PAD)], fill=(*accent, 60))

    # Very light dot texture
    _draw_dot_grid(draw, W, H, accent, spacing=38)

    INNER_X = PAD + 34

    # ── Logo ──────────────────────────────────────────────────────────────────
    _draw_logo(draw, INNER_X, PAD + 18, accent)

    # ── Category + platform on same row ───────────────────────────────────────
    cat_w = _draw_category_tag(draw, INNER_X, PAD + 60, category)

    f_plat = _font(19, bold=False)
    draw.text((INNER_X + cat_w + 18, PAD + 67), platform, font=f_plat, fill=(*MUTED, 180))

    # ── Full-width market title ────────────────────────────────────────────────
    f_mkt = _font(38, bold=True)
    for i, line in enumerate(textwrap.wrap(market, 48)[:2]):
        draw.text((INNER_X, PAD + 116 + i * 52), line, font=f_mkt, fill=WHITE)

    # ── USD + outcome on same horizontal row ──────────────────────────────────
    f_hero  = _font(84, bold=True)
    f_out   = _font(42, bold=True)
    out_col = MINT if outcome != "NO" else ROSE
    arrow   = "▲" if outcome != "NO" else "▼"

    hero_y  = PAD + 238
    draw.text((INNER_X, hero_y), usd_str, font=f_hero, fill=(*accent, 255))

    # Outcome sits to the right of the dollar + vertically centered
    usd_w   = draw.textlength(usd_str, font=f_hero)
    out_str = f"{arrow} {outcome}"
    draw.text((INNER_X + usd_w + 32, hero_y + 24), out_str, font=f_out, fill=(*out_col, 255))

    # ── Probability bar ────────────────────────────────────────────────────────
    _draw_probability_bar(draw, INNER_X, hero_y + 100, 300, 10, price, accent, outcome)

    # ── Bottom strip ──────────────────────────────────────────────────────────
    f_footer  = _font(18, bold=False)
    draw.line([(INNER_X, H - PAD - 106), (W - PAD, H - PAD - 106)], fill=(*DIM, 255), width=1)
    f_handle, fitted = _fit_handle(draw, handle, W - PAD - INNER_X - 100, sizes=(28, 22, 18))
    draw.text((INNER_X, H - PAD - 94), fitted, font=f_handle, fill=MUTED)
    _draw_win_rate_bar(draw, INNER_X, H - PAD - 54, 260, win_rate, accent)

    urgency = _urgency_label()
    urg_w   = draw.textlength(urgency, font=f_footer)
    pv_w    = draw.textlength("polyvision.app", font=f_footer)
    draw.text((W - PAD - urg_w, H - PAD - 88), urgency, font=f_footer, fill=(*MUTED, 140))
    draw.text((W - PAD - pv_w,  H - PAD - 58), "polyvision.app", font=f_footer, fill=(*MUTED, 180))


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def generate_card(payload: dict) -> io.BytesIO:
    """
    Generate a branded PNG trade card and return it as a BytesIO buffer.
    Variant is chosen based on USD tier + light randomness for variety.
    """
    # ── Parse payload ─────────────────────────────────────────────────────────
    handle    = (payload.get("trader_handle") or payload.get("handle") or "Anonymous Whale")[:40]
    market    = payload.get("market_title", "Unknown Market")
    outcome   = str(payload.get("outcome", "YES")).upper()
    price     = float(payload.get("price", 0.5))
    usd_value = float(payload.get("usd_value", 0))
    source    = str(payload.get("source", "POLYMARKET")).upper()
    win_rate  = payload.get("wallet_win_rate")
    tier      = payload.get("alert_tier", "STANDARD")

    platform  = "Kalshi" if source == "KALSHI" else "Polymarket"
    category  = _categorize(market)

    # ── Base accent color ─────────────────────────────────────────────────────
    base_accent = ROSE if outcome == "NO" else MINT
    accent      = _market_accent(market, base_accent)

    # ── Tier classification ────────────────────────────────────────────────────
    is_mega   = usd_value >= 1_000_000
    is_super  = usd_value >= 250_000
    is_whale  = usd_value >= 50_000 or tier in ("WHALE", "CLUSTER")

    # ── Variant selection (with light randomness for variety at borderlines) ──
    if is_mega:
        variant = "terminal"
    elif is_super:
        variant = random.choice(["pulse", "pulse", "receipt"])  # mostly pulse
    elif is_whale:
        variant = random.choice(["receipt", "receipt", "stealth"])  # mostly receipt
    else:
        variant = "stealth"

    # ── Data bundle ───────────────────────────────────────────────────────────
    data = {
        "handle":   handle,
        "market":   market,
        "outcome":  outcome,
        "price":    price,
        "usd_str":  _fmt_usd(usd_value),
        "pct":      _pct_str(price),
        "platform": platform,
        "category": category,
        "win_rate": win_rate,
    }

    # ── Canvas ────────────────────────────────────────────────────────────────
    bg_color = BG_VOID if variant == "terminal" else (NEAR_BLACK if variant == "stealth" else BG_DARK)
    img  = Image.new("RGBA", (W, H), (*bg_color, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    # ── Dispatch to variant renderer ──────────────────────────────────────────
    if variant == "terminal":
        _render_terminal(img, draw, data, GOLD)
    elif variant == "pulse":
        _render_pulse(img, draw, data, accent)
    elif variant == "receipt":
        _render_receipt(img, draw, data, accent)
    else:
        _render_stealth(img, draw, data, accent)

    # ── Export ────────────────────────────────────────────────────────────────
    final = Image.new("RGB", img.size, (0, 0, 0))
    final.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    final.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    log.info(f"[CardGen] v4 [{variant.upper()}] {handle[:20]} | {_fmt_usd(usd_value)} {outcome} | {category}")
    return buf


# ══════════════════════════════════════════════════════════════════════════════
#  PREVIEW GENERATOR  (python tools/card_generator.py)
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    os.makedirs(".tmp", exist_ok=True)

    previews = [
        # (filename, payload)
        ("preview_terminal_megawhale.png", {
            "trader_handle": "The Oracle of New York",
            "market_title":  "Will the Fed cut rates before July 2026?",
            "outcome": "YES", "price": 0.72, "usd_value": 1_250_000,
            "source": "POLYMARKET", "alert_tier": "WHALE", "wallet_win_rate": 0.69,
        }),
        ("preview_pulse_superwhale.png", {
            "trader_handle": "GamblingIsAllYouNeed",
            "market_title":  "Will Trump sign the tariff rollback before Q3?",
            "outcome": "NO", "price": 0.38, "usd_value": 480_000,
            "source": "POLYMARKET", "alert_tier": "WHALE", "wallet_win_rate": 0.54,
        }),
        ("preview_receipt_yes.png", {
            "trader_handle": "Tmao12345",
            "market_title":  "Flyers vs. Red Wings — Game 4",
            "outcome": "RED WINGS", "price": 0.53, "usd_value": 82_500,
            "source": "POLYMARKET", "alert_tier": "WHALE", "wallet_win_rate": None,
        }),
        ("preview_receipt_no.png", {
            "trader_handle": "The Strategist of Oregon",
            "market_title":  "Will Bitcoin hit $120k by end of 2026?",
            "outcome": "NO", "price": 0.31, "usd_value": 67_000,
            "source": "POLYMARKET", "alert_tier": "STANDARD", "wallet_win_rate": 0.42,
        }),
        ("preview_stealth_polymarket.png", {
            "trader_handle": "AnonWhale_f3a229",
            "market_title":  "Will SCOTUS overturn Chevron deference ruling?",
            "outcome": "YES", "price": 0.61, "usd_value": 28_000,
            "source": "POLYMARKET", "alert_tier": "STANDARD", "wallet_win_rate": 0.58,
        }),
        ("preview_stealth_kalshi.png", {
            "trader_handle": "Kalshi Whale",
            "market_title":  "Will US unemployment stay below 4.5% in May?",
            "outcome": "YES", "price": 0.81, "usd_value": 9_500,
            "source": "KALSHI", "alert_tier": "STANDARD", "wallet_win_rate": None,
        }),
    ]

    print(f"Generating {len(previews)} card variants...")
    for fname, payload in previews:
        buf = generate_card(payload)
        path = os.path.join(".tmp", fname)
        with open(path, "wb") as f: f.write(buf.read())
        print(f"  ✅ {fname}")

    print(f"\nAll previews saved to .tmp/")
