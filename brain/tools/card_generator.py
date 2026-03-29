#!/usr/bin/env python3
"""
card_generator.py — PolyVision Layer 3 Tool

Generates a branded 1200×675 PNG trade card for attachment to X (Twitter) posts.
Uses Pillow (PIL) only — no external services, works headlessly on Railway.

v3: "Receipt" styling. Left pane context, Right pane numbers. Geometric
outcome icons (avoiding linux emoji bugs), win-rate pill badge, grid texture,
and layered "Glow" effects for $250k+ Whale and $1M+ Mega Whale trades.
"""

import io
import os
import logging
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageFilter

log = logging.getLogger(__name__)

# ── Brand Colours ─────────────────────────────────────────────────────────────
BG_DARK   = (10,  13,  18)    # deep navy black
BG_CARD   = (18,  23,  32)    # card surface
BG_RECEIPT= (24,  30,  42)    # right pane surface
BG_BADGE  = (30,  40,  56)    # badge background
MINT      = (0,   230, 150)   # YES accent
ROSE      = (255, 70,  100)   # NO accent
AMBER     = (245, 166, 35)    # neutral
GOLD      = (255, 215, 0)     # Mega Whale
WHITE     = (235, 242, 250)   # primary text
MUTED     = (120, 135, 150)   # secondary text
DIM       = (60,  72,  86)    # separators

W, H = 1200, 675

# ── Fonts ─────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_HERE, "..", "assets", "fonts")
BUNDLED_BOLD    = os.path.join(_FONT_DIR, "NotoSans-Bold.ttf")
BUNDLED_REGULAR = os.path.join(_FONT_DIR, "NotoSans-Regular.ttf")

_SYS_BOLD = ["/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]
_SYS_REGULAR = ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/System/Library/Fonts/Helvetica.ttc"]

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [BUNDLED_BOLD if bold else BUNDLED_REGULAR] + (_SYS_BOLD if bold else _SYS_REGULAR)
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()

def _truncate(text: str, font, draw: ImageDraw.Draw, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width: return text
    while text and draw.textlength(text + "…", font=font) > max_width: text = text[:-1]
    return text + "…"

def _fmt_usd(value: float) -> str:
    if value >= 1_000_000: return f"${value / 1_000_000:.1f}M"
    if value >= 1_000: return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"

# ── Visual Helpers ────────────────────────────────────────────────────────────

def _draw_grid_texture(draw: ImageDraw.Draw, w: int, h: int, color: tuple):
    """Draws a faint graph paper grid across the image."""
    step = 30
    for x in range(0, w, step):
        draw.line([(x, 0), (x, h)], fill=(*color, 8), width=1)
    for y in range(0, h, step):
        draw.line([(0, y), (w, y)], fill=(*color, 8), width=1)

def _draw_glow_text(base_img, draw, x, y, text, font, fill_color, glow_color, radius=12, intensity=2):
    """Draws text with a blurred drop-shadow/glow effect using composition."""
    # Create temp transparent canvas
    txt_layer = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_layer)
    txt_draw.text((x, y), text, font=font, fill=glow_color)
    
    # Blur to create glow
    blurred = txt_layer.filter(ImageFilter.GaussianBlur(radius=radius))
    
    # Composite multiple times to increase core brightness of the glow
    for _ in range(intensity):
        base_img.alpha_composite(blurred)
        
    # Finally, draw sharp text over the glow
    draw.text((x, y), text, font=font, fill=fill_color)

def _draw_outcome_icon(draw: ImageDraw.Draw, cx: int, cy: int, size: int, outcome: str, color: tuple):
    """Draws a geometric arrow/chevron centered at (cx, cy)."""
    r = size / 2.0
    if outcome == "NO":
        # Downward solid triangle
        pts = [(cx - r, cy - r*0.5), (cx + r, cy - r*0.5), (cx, cy + r*0.7)]
        draw.polygon(pts, fill=color)
    else:
        # Upward solid triangle
        pts = [(cx - r, cy + r*0.5), (cx + r, cy + r*0.5), (cx, cy - r*0.7)]
        draw.polygon(pts, fill=color)


# ── Main Generator ────────────────────────────────────────────────────────────
def generate_card(payload: dict) -> io.BytesIO:
    # ── Parsing ───────────────────────────────────────────────────────────────
    handle    = payload.get("trader_handle") or payload.get("handle") or "Anonymous"
    market    = payload.get("market_title", "Unknown Market")
    outcome   = str(payload.get("outcome", "YES")).upper()
    price     = float(payload.get("price", 0.5))
    usd_value = float(payload.get("usd_value", 0))
    source    = str(payload.get("source", "POLYMARKET")).upper()
    win_rate  = payload.get("wallet_win_rate")

    platform  = "Kalshi" if source == "KALSHI" else "Polymarket"
    accent    = ROSE if outcome == "NO" else MINT
    
    is_mega   = usd_value >= 1_000_000
    is_whale  = usd_value >= 50_000 or payload.get("alert_tier", "STANDARD") == "WHALE"
    is_super  = usd_value >= 250_000
    
    tier_label = "👑  MEGA WHALE" if is_mega else ("🐋  WHALE" if is_whale else "🔵  STANDARD")
    usd_str    = _fmt_usd(usd_value)
    pct_str    = f"@ {price:.0%}"

    # ── Canvas setup ──────────────────────────────────────────────────────────
    img  = Image.new("RGBA", (W, H), (*BG_DARK, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    # 1. Background Grid & Glow Texture
    _draw_grid_texture(draw, W, H, accent)
    
    # Soft radial gradient bounding from the bottom-right
    r, g, b = accent if not is_mega else GOLD
    for i in range(150):
        a = int(12 * (1 - i / 150))
        draw.rectangle([(W - i*5, H - i*3), (W, H)], fill=(r, g, b, a))

    # ── Container Frames ──────────────────────────────────────────────────────
    PAD = 40
    # Base unified card outline
    draw.rounded_rectangle([PAD, PAD, W-PAD, H-PAD], radius=24, fill=(*BG_CARD, 255))
    
    # Left vs Right Pane Definitions
    LEFT_X = PAD + 40
    LEFT_W = 460
    RIGHT_X = LEFT_X + LEFT_W + 40
    RIGHT_W = (W - PAD - 40) - RIGHT_X
    
    # The "Receipt" Pane Background (Right)
    pane_rect = [RIGHT_X, PAD + 2, W - PAD - 2, H - PAD - 2]
    # We'll use a slightly lighter, distinct box to hold the critical data
    # (sharp on left edge, rounded on right edge to match outer box)
    # Pillow allows separate corners in later versions, but to be safe:
    draw.rounded_rectangle([RIGHT_X, PAD + 2, W - PAD - 2, H - PAD - 2], radius=22, fill=(*BG_RECEIPT, 255))
    draw.rectangle([RIGHT_X, PAD + 2, RIGHT_X + 22, H - PAD - 2], fill=(*BG_RECEIPT, 255)) # Square off left edge
    draw.line([(RIGHT_X, PAD + 2), (RIGHT_X, H - PAD - 2)], fill=(*DIM, 255), width=2)     # Vertical divider
    
    # Outer stroke (drawn over the pane)
    draw.rounded_rectangle([PAD, PAD, W-PAD, H-PAD], radius=24, outline=(*accent, 60), width=2)

    # ── Left Pane: Identity & Context ─────────────────────────────────────────
    f_logo    = _font(28, bold=True)
    f_handle  = _font(48, bold=True)
    f_market  = _font(32, bold=False)
    f_footer  = _font(22, bold=False)
    
    # Logo
    draw.text((LEFT_X, PAD + 40), "⬥ PolyVision", font=f_logo, fill=MINT)
    
    # Trader Handle
    hy = PAD + 180
    safe_handle = _truncate(handle, f_handle, draw, LEFT_W)
    draw.text((LEFT_X, hy), safe_handle, font=f_handle, fill=WHITE)
    
    # Win Rate Badge
    wy = hy + 64
    f_wr = _font(20, bold=True)
    # Win rate display logic:
    # - None → TBD (xray returned no data at all)
    # - 0.0  → TBD (no closed positions found, not a real 0% win rate)
    # - >0   → show actual % with color coding
    if win_rate is not None and win_rate > 0.0:
        wr_str = f"Win Rate: {win_rate:.0%}"
        if win_rate >= 0.55:
            bg_color = (*MINT, 255); text_color = (0, 0, 0, 255)
        elif win_rate <= 0.45:
            bg_color = (*ROSE, 255); text_color = WHITE
        else:
            bg_color = (*MUTED, 255); text_color = WHITE
    else:
        wr_str = "Win Rate: TBD"
        bg_color = (*DIM, 255); text_color = WHITE
        
    wr_w = draw.textlength(wr_str, font=f_wr) + 24
    draw.rounded_rectangle([LEFT_X, wy, LEFT_X + wr_w, wy + 32], radius=16, fill=bg_color)
    draw.text((LEFT_X + 12, wy + 4), wr_str, font=f_wr, fill=text_color)
    
    # Market Title
    my = wy + 60
    wrapped = textwrap.wrap(market, width=32)[:3]
    for i, line in enumerate(wrapped):
        draw.text((LEFT_X, my + i*44), line, font=f_market, fill=MUTED)
        
    # Footer Left
    draw.text((LEFT_X, H - PAD - 60), platform, font=f_footer, fill=MUTED)


    # ── Right Pane: The Trade Slip (Magnitude Logic) ──────────────────────────
    
    # Tier Badge (Top Right)
    f_badge = _font(22, bold=True)
    badge_w = draw.textlength(tier_label, font=f_badge) + 32
    badge_x = W - PAD - 40 - badge_w
    badge_y = PAD + 40
    badge_color = (*GOLD, 255) if is_mega else (*accent, 255)
    
    # Only draw full fill badge for mega whales, outline otherwise
    if is_mega:
        draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + 40], radius=12, fill=badge_color)
        draw.text((badge_x + 16, badge_y + 8), tier_label, font=f_badge, fill=(0,0,0,255))
    else:
        draw.rounded_rectangle([badge_x, badge_y, badge_x + badge_w, badge_y + 40], radius=12, fill=(*BG_BADGE, 255), outline=badge_color, width=2)
        draw.text((badge_x + 16, badge_y + 8), tier_label, font=f_badge, fill=WHITE)


    # Magnitude sizing for $ Value
    if is_mega:
        hero_sz = 140
        hero_fill = (*GOLD, 255)
        do_glow = True
        glow_rad = 20
        glow_col = (*GOLD, 100)
    elif is_super:
        hero_sz = 120
        hero_fill = (*accent, 255)
        do_glow = True
        glow_rad = 15
        glow_col = (*accent, 120)
    else:
        hero_sz = 95
        hero_fill = (*accent, 255)
        do_glow = False
        glow_rad = 0
        glow_col = None

    f_hero = _font(hero_sz, bold=True)
    f_outcome = _font(52, bold=True)  # Reduced from 64 to prevent line bleed
    f_pct = _font(40, bold=False)     # Reduced from 48
    
    # Laying out the text blocks horizontally in a row centered in right pane
    out_w = draw.textlength(outcome, font=f_outcome)
    pct_w = draw.textlength(pct_str, font=f_pct)
    
    # We'll stack it:
    # 1. HUGE Dollar Amount (Top)
    # 2. [ICON] OUTCOME @ PCT (Bottom)
    
    # $ Amount centered inside राइट block horizontally
    hero_w = draw.textlength(usd_str, font=f_hero)
    hx = RIGHT_X + (RIGHT_W - hero_w) // 2
    hy = PAD + 200 # Fixed vertical anchoring
    
    if do_glow:
        _draw_glow_text(img, draw, hx, hy, usd_str, f_hero, hero_fill, glow_col, radius=glow_rad, intensity=3)
    else:
        draw.text((hx, hy), usd_str, font=f_hero, fill=hero_fill)
        
    # Second Row: [ICON] OUTCOME  @ PCT
    icon_sz = 30
    gap = 16
    RIGHT_EDGE = W - PAD - 40  # hard right wall

    # Calculate max width available for the outcome text label
    MAX_OUT_W = RIGHT_EDGE - RIGHT_X - icon_sz - gap - 24  # 24px right padding
    safe_outcome = outcome
    while draw.textlength(safe_outcome, font=f_outcome) > MAX_OUT_W and len(safe_outcome) > 1:
        safe_outcome = safe_outcome[:-1]
    if safe_outcome != outcome:
        while safe_outcome and draw.textlength(safe_outcome + "…", font=f_outcome) > MAX_OUT_W:
            safe_outcome = safe_outcome[:-1]
        safe_outcome += "…"

    out_w = draw.textlength(safe_outcome, font=f_outcome)
    pct_w = draw.textlength(pct_str, font=f_pct)

    # Determine if we have room for the pct label too
    row2_total_w = icon_sz + gap + out_w + 20 + pct_w
    show_pct = (RIGHT_X + row2_total_w + 24) <= RIGHT_EDGE
    if not show_pct:
        row2_total_w = icon_sz + gap + out_w

    row2_x = RIGHT_X + (RIGHT_W - row2_total_w) // 2
    
    # Force left boundary: never bleed over the center line
    if row2_x < RIGHT_X + 24:
        row2_x = RIGHT_X + 24
        
    row2_y = hy + hero_sz + 40
    
    # 1. Icon
    _draw_outcome_icon(draw, cx=row2_x + icon_sz//2, cy=row2_y + 30, size=icon_sz, outcome=outcome, color=(*accent, 255))
    
    # 2. Outcome text (safe, truncated)
    out_txt_x = row2_x + icon_sz + gap
    draw.text((out_txt_x, row2_y), safe_outcome, font=f_outcome, fill=(*accent, 255))
    
    # 3. Pct (only if it fits)
    if show_pct:
        pct_txt_x = out_txt_x + out_w + 20
        draw.text((pct_txt_x, row2_y + 12), pct_str, font=f_pct, fill=MUTED)
    
    # Footer Right
    tagline = "polyvision.app"
    tag_w = draw.textlength(tagline, font=f_footer)
    draw.text((W - PAD - 40 - tag_w, H - PAD - 60), tagline, font=f_footer, fill=MUTED)

    # ── Export ────────────────────────────────────────────────────────────────
    # Convert RGBA to RGB for standard PNG without transparency issues (just flatten)
    final_img = Image.new("RGB", img.size, (0, 0, 0))
    final_img.paste(img, mask=img.split()[3])
    
    buf = io.BytesIO()
    final_img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    log.info(f"[CardGen] v3 Receipt Card: {handle[:20]} | {usd_str} {outcome}")
    return buf

# ── Preview Tests ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO)
    os.makedirs(".tmp", exist_ok=True)
    
    base_payload = {
        "trader_handle": "GamblingIsAllYouNeed",
        "market_title": "Canucks vs. Flames",
        "outcome": "CANUCKS",
        "price": 0.41,
        "usd_value": 70_848,
        "source": "POLYMARKET",
        "alert_tier": "WHALE",
        "wallet_win_rate": 0.61,  # Good win rate
    }

    print("Generating 3 preview variants...")
    
    # 1. Standard Whale
    buf = generate_card(base_payload)
    with open(".tmp/preview_standard.png", "wb") as f: f.write(buf.read())
    
    # 2. Super Whale ($350k - glowing)
    p2 = base_payload.copy()
    p2["usd_value"] = 350_000
    p2["outcome"] = "NO"
    p2["wallet_win_rate"] = 0.35 # Bad win rate
    buf2 = generate_card(p2)
    with open(".tmp/preview_superwhale.png", "wb") as f: f.write(buf2.read())
    
    # 3. Mega Whale ($1.2M - gold)
    p3 = base_payload.copy()
    p3["usd_value"] = 1_250_000
    p3["wallet_win_rate"] = None # TBD win rate
    buf3 = generate_card(p3)
    with open(".tmp/preview_megawhale.png", "wb") as f: f.write(buf3.read())
    
    print("✅ Previews saved in .tmp/")
