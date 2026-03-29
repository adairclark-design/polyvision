#!/usr/bin/env python3
"""
card_generator.py — PolyVision Layer 3 Tool

Generates a branded 1200×675 PNG trade card for attachment to X (Twitter) posts.
Uses Pillow (PIL) only — no external services, no temp files, works headlessly on Railway.

Usage:
    from tools.card_generator import generate_card
    png_bytes = generate_card(payload)   # returns BytesIO

Self-annealing log:
    2026-03-29: Initial implementation. Pure Pillow rendering, no fonts required
                beyond system defaults. Uses in-memory BytesIO — no disk writes.
                Headless-safe: no display required.
"""

import io
import textwrap
import logging
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# ── Brand Colours ─────────────────────────────────────────────────────────────
BG_DARK   = (13,  17,  23)    # #0d1117
BG_CARD   = (22,  27,  34)    # #161b22
BG_BADGE  = (33,  40,  50)    # ~#212832
MINT      = (0,   255, 163)   # #00ffa3  — YES accent
ROSE      = (255, 77,  109)   # #ff4d6d  — NO accent
AMBER     = (245, 166, 35)    # #f5a623  — neutral/large size
WHITE     = (230, 237, 243)   # #e6edf3  — primary text
MUTED     = (139, 148, 158)   # #8b949e  — secondary text
BORDER    = (255, 255, 255, 20)  # rgba border

W, H = 1200, 675

# ── Font loader (falls back to Pillow default if system fonts not available) ──
def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _truncate(text: str, font, draw: ImageDraw.Draw, max_width: int) -> str:
    """Truncate text to fit within max_width pixels."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _fmt_usd(value: float) -> str:
    """Format large dollar amounts compactly: $69,589 → $69.6K, $1,200,000 → $1.2M"""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def generate_card(payload: dict) -> io.BytesIO:
    """
    Generate a branded PolyVision trade card image.
    Returns a BytesIO object containing a PNG, ready for tweepy media_upload.

    Args:
        payload: WhaleAlertPayload dict from notifier/signal_engine.
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

    platform  = "Kalshi" if source == "KALSHI" else "Polymarket"
    accent    = MINT if outcome == "YES" else ROSE
    tier_label = "🐋 WHALE" if tier == "WHALE" or usd_value >= 50_000 else "🔵 STANDARD"
    pct_str   = f"{price:.0%}"
    usd_str   = _fmt_usd(usd_value)
    wr_str    = f"Win Rate: {win_rate:.0%}" if win_rate else ""

    # ── Canvas ────────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img, "RGBA")

    # ── Background gradient overlay (simple top-to-bottom) ───────────────────
    for y in range(H):
        alpha = int(30 * (1 - y / H))
        r, g, b = accent
        draw.line([(0, y), (W, y)], fill=(r, g, b, alpha))

    # ── Main card area ────────────────────────────────────────────────────────
    pad = 48
    card_rect = [pad, pad, W - pad, H - pad]
    draw.rounded_rectangle(card_rect, radius=20, fill=BG_CARD)
    draw.rounded_rectangle(card_rect, radius=20, outline=(*accent, 60), width=2)

    # ── PolyVision header ─────────────────────────────────────────────────────
    f_logo   = _font(26, bold=True)
    f_label  = _font(18, bold=False)
    f_handle = _font(38, bold=True)
    f_market = _font(22, bold=False)
    f_stat   = _font(56, bold=True)
    f_sub    = _font(19, bold=False)
    f_footer = _font(17, bold=False)

    logo_x, logo_y = pad + 28, pad + 28
    draw.text((logo_x, logo_y), "🐋 PolyVision", font=f_logo, fill=MINT)

    # Tier badge — top right
    t_text        = tier_label
    t_bbox        = draw.textbbox((0, 0), t_text, font=f_label)
    t_w           = t_bbox[2] - t_bbox[0] + 24
    t_h           = t_bbox[3] - t_bbox[1] + 12
    t_x           = W - pad - 28 - t_w
    t_y           = logo_y - 2
    draw.rounded_rectangle([t_x, t_y, t_x + t_w, t_y + t_h], radius=8, fill=BG_BADGE)
    draw.text((t_x + 12, t_y + 6), t_text, font=f_label, fill=WHITE)

    # Divider
    div_y = logo_y + 52
    draw.line([(pad + 28, div_y), (W - pad - 28, div_y)], fill=(*accent, 40), width=1)

    # ── Trader handle ─────────────────────────────────────────────────────────
    hx, hy = pad + 28, div_y + 36
    safe_handle = _truncate(handle, f_handle, draw, W - pad * 2 - 80)
    draw.text((hx, hy), safe_handle, font=f_handle, fill=WHITE)

    # Win rate beside handle (muted, smaller)
    if wr_str:
        wr_x = hx + draw.textlength(safe_handle, font=f_handle) + 18
        draw.text((wr_x, hy + 12), wr_str, font=f_label, fill=MUTED)

    # ── Market title ──────────────────────────────────────────────────────────
    mx, my = hx, hy + 60
    # Wrap long market names across 2 lines max
    wrapped = textwrap.wrap(market, width=60)[:2]
    for i, line in enumerate(wrapped):
        safe_line = _truncate(line, f_market, draw, W - pad * 2 - 80)
        draw.text((mx, my + i * 32), safe_line, font=f_market, fill=MUTED)

    # ── Big stat row: SIZE · OUTCOME · PRICE · PLATFORM ──────────────────────
    stat_y = my + (len(wrapped) * 32) + 44
    # Size
    draw.text((hx, stat_y), usd_str, font=f_stat, fill=accent)
    size_w = draw.textlength(usd_str, font=f_stat)

    # Outcome
    ox = hx + size_w + 28
    draw.text((ox, stat_y + 6), outcome, font=_font(44, bold=True), fill=accent)
    out_w = draw.textlength(outcome, font=_font(44, bold=True))

    # Price percentage
    px2 = ox + out_w + 28
    draw.text((px2, stat_y + 6), f"@ {pct_str}", font=_font(36, bold=False), fill=MUTED)

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_y = H - pad - 48
    draw.line([(pad + 28, footer_y - 16), (W - pad - 28, footer_y - 16)], fill=(*accent, 40), width=1)
    draw.text((pad + 28, footer_y), platform, font=f_footer, fill=MUTED)
    tagline = "Real-time Whale Intelligence → polyvision.app"
    tag_w   = draw.textlength(tagline, font=f_footer)
    draw.text((W - pad - 28 - tag_w, footer_y), tagline, font=f_footer, fill=MUTED)

    # ── Export ────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    log.info(f"[CardGen] Generated card: {handle} | {market[:40]} | {usd_str} {outcome}")
    return buf


# ── CLI: save a preview card for visual inspection ────────────────────────────
if __name__ == "__main__":
    import json, sys

    logging.basicConfig(level=logging.INFO)

    sample = {
        "trader_handle": "GamblingIsAllYouNeed",
        "market_title":  "Clippers vs. Pacers",
        "outcome":       "NO",
        "price":         0.9963,
        "usd_value":     69_589,
        "source":        "POLYMARKET",
        "alert_tier":    "WHALE",
        "wallet_win_rate": 0.40,
    }

    if len(sys.argv) > 1:
        sample = json.loads(sys.argv[1])

    buf = generate_card(sample)
    out_path = ".tmp/card_preview.png"
    import os; os.makedirs(".tmp", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(buf.read())
    print(f"✅ Card saved to {out_path}")
