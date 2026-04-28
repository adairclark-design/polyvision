#!/usr/bin/env python3
from __future__ import annotations
"""
chart_generator.py — VisionEdge Marketing Agent | Layer 3: Chart
Generates an animated probability bar card graphic using Pillow.

generate_chart(trade) now returns a DIRECTORY PATH containing 30 RGBA PNG
frames that video_factory.py assembles as an image sequence.  The bar fills
from 0 % → target % with a cubic ease-out over 1 second (30 frames @ 30 fps).
Falls back to a single static PNG if frame generation fails.
"""
import os
import json
import logging
import random
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'marketing')
ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')

# Number of animation frames (30 @ 30 fps = 1 second fill animation)
ANIM_FRAMES = 30


# ── Font helpers ──────────────────────────────────────────────────────────────

def _get_font(size: int, bold=False):
    """Load a system font — checks Linux (Railway) paths first, then Mac fallbacks."""
    weight = "Bold" if bold else "Regular"
    candidates = [
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{weight}.ttf",
        f"/usr/share/fonts/truetype/liberation2/LiberationSans-{weight}.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans-{weight}.ttf",
        f"/System/Library/Fonts/Supplemental/Arial {weight}.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    log.warning(f"[chart] No system font for size={size} — PIL default active.")
    return ImageFont.load_default()


# ── Easing ────────────────────────────────────────────────────────────────────

def _ease_out_cubic(t: float) -> float:
    """Cubic ease-out: fast start, smooth deceleration to final value."""
    return 1 - (1 - t) ** 3


# ── Base image renderer (static elements only) ────────────────────────────────

def _render_base_image(trade: dict) -> tuple[Image.Image, dict]:
    """
    Draw all static elements (everything EXCEPT the bar fill and percentage text).
    Returns (base_image, layout_params) for use in frame animation.
    """
    width, height = 1080, 1920

    is_cluster  = trade.get("_is_cluster", False)
    trade_count = int(trade.get("_trade_count", 1))

    # Transparent RGBA canvas — lets B-roll show through
    img  = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    amount     = trade.get('usd_value', 0)
    amount_str = f"${amount:,.0f}" if amount > 0 else "$249,500"
    market     = trade.get('market_title', 'Will the market perform as expected?')
    outcome    = trade.get('outcome', 'Yes').capitalize()

    price = trade.get('price', 0.5)
    pct   = price * 100
    if pct >= 100:
        pct = 99.0

    is_yes       = outcome in ("Yes", "In favor", "Long", "Call")
    accent_color = "#10B981" if is_yes else "#EF4444"
    card_bg      = (22, 27, 38, 200)

    # ── Alert badge ───────────────────────────────────────────────────────────
    font_alert = _get_font(54, bold=True)
    if is_cluster:
        alert_text  = "🐋 POLYVISION CLUSTER ALERT"
        alert_color = "#F59E0B"
    else:
        alert_text  = "🚨 POLYVISION WHALE ALERT"
        alert_color = "#5C5FE5"
    bbox = draw.textbbox((0, 0), alert_text, font=font_alert)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, 60), alert_text, fill=alert_color, font=font_alert)

    # ── Card geometry ─────────────────────────────────────────────────────────
    wrapped_market = textwrap.fill(market, width=26)
    num_lines  = len(wrapped_market.split('\n'))
    card_height = 90 + (num_lines * 82) + 240 + 60 + 90 + 90
    card_margin = 60
    card_top    = (height - card_height) / 2  # default if logo fails

    # ── Logo ──────────────────────────────────────────────────────────────────
    logo_path = os.path.join(ASSETS_DIR, "whale_logo.png")
    try:
        logo      = Image.open(logo_path).convert("RGBA")
        w_pct     = 280 / float(logo.size[0])
        h_size    = int(float(logo.size[1]) * w_pct)
        logo      = logo.resize((280, h_size), Image.Resampling.LANCZOS)
        logo_x    = int((width - 280) / 2)
        total_h   = h_size + 80 + card_height
        cluster_y = (height - total_h) / 2
        logo_y    = int(cluster_y)
        card_top  = int(logo_y + h_size + 80)
        img.paste(logo, (logo_x, logo_y), logo)
    except Exception as e:
        log.warning(f"Failed to load whale logo: {e}")

    # ── Card background ───────────────────────────────────────────────────────
    try:
        draw.rounded_rectangle(
            [card_margin, card_top, width - card_margin, card_top + card_height],
            radius=24, fill=card_bg, outline="#2A3241", width=3
        )
    except AttributeError:
        draw.rectangle(
            [card_margin, card_top, width - card_margin, card_top + card_height],
            fill=card_bg
        )

    # ── Market title ──────────────────────────────────────────────────────────
    font_market = _get_font(63, bold=True)
    y_text = card_top + 90
    for line in wrapped_market.split('\n'):
        l_bbox = draw.textbbox((0, 0), line, font=font_market)
        lw     = l_bbox[2] - l_bbox[0]
        draw.text(((width - lw) / 2, y_text), line, fill="#F8FAFC", font=font_market)
        y_text += 82

    # ── Bet text (auto-scaling) ───────────────────────────────────────────────
    font_size = 96
    font_bet  = _get_font(font_size, bold=True)
    bet_text  = f"🐋×{trade_count} — {amount_str} on '{outcome}'" if is_cluster else f"{amount_str} on '{outcome}'"
    max_text_width = width - (card_margin * 2) - 60
    bw = max_text_width  # placeholder
    while True:
        b_bbox = draw.textbbox((0, 0), bet_text, font=font_bet)
        bw     = b_bbox[2] - b_bbox[0]
        if bw <= max_text_width or font_size <= 36:
            break
        font_size -= 2
        font_bet  = _get_font(font_size, bold=True)
    draw.text(((width - bw) / 2, y_text + 60), bet_text, fill=accent_color, font=font_bet)

    win_rate = trade.get("wallet_win_rate", 0)
    if win_rate:
        font_wr = _get_font(48, bold=True)
        wr_text = f"Trader Historic Win Rate: {win_rate:.0%}"
        wr_bbox = draw.textbbox((0, 0), wr_text, font=font_wr)
        wr_w = wr_bbox[2] - wr_bbox[0]
        draw.text(((width - wr_w) / 2, y_text + 160), wr_text, fill="#94A3B8", font=font_wr)

    # ── Bar background (empty shell — fill is drawn per-frame) ────────────────
    bar_y      = y_text + 240
    bar_margin = card_margin + 90
    bar_width  = width - (bar_margin * 2)
    bar_height = 60
    try:
        draw.rounded_rectangle(
            [bar_margin, bar_y, bar_margin + bar_width, bar_y + bar_height],
            radius=30, fill="#0B101A"
        )
    except AttributeError:
        draw.rectangle(
            [bar_margin, bar_y, bar_margin + bar_width, bar_y + bar_height],
            fill="#0B101A"
        )

    layout = {
        "bar_y":      bar_y,
        "bar_margin": bar_margin,
        "bar_width":  bar_width,
        "bar_height": bar_height,
        "accent":     accent_color,
        "pct":        pct,
        "img_width":  width,
    }
    return img, layout


# ── Per-frame bar compositor ──────────────────────────────────────────────────

def _apply_bar_frame(base: Image.Image, layout: dict, progress: float) -> Image.Image:
    """Draw the animated bar fill + live percentage counter at given progress (0–1)."""
    frame = base.copy()
    draw  = ImageDraw.Draw(frame)

    bar_y      = layout["bar_y"]
    bar_margin = layout["bar_margin"]
    bar_width  = layout["bar_width"]
    bar_height = layout["bar_height"]
    accent     = layout["accent"]
    target_pct = layout["pct"]
    img_width  = layout["img_width"]

    current_pct = target_pct * progress
    fill_w      = bar_width * (current_pct / 100.0)

    if fill_w > 1:
        try:
            draw.rounded_rectangle(
                [bar_margin, bar_y, bar_margin + fill_w, bar_y + bar_height],
                radius=30, fill=accent
            )
        except (AttributeError, ValueError):
            draw.rectangle(
                [bar_margin, bar_y, bar_margin + fill_w, bar_y + bar_height],
                fill=accent
            )

    # Percentage counter
    font_pct = _get_font(54, bold=True)
    pct_text = f"{current_pct:.0f}% Market Probability"
    p_bbox   = draw.textbbox((0, 0), pct_text, font=font_pct)
    pw       = p_bbox[2] - p_bbox[0]
    draw.text(((img_width - pw) / 2, bar_y + 90), pct_text, fill="#94A3B8", font=font_pct)

    return frame


# ── Animated chart generator ──────────────────────────────────────────────────

def generate_chart_animated(trade: dict) -> str | None:
    """
    Generate ANIM_FRAMES RGBA PNG frames with a cubic ease-out bar fill animation.
    Returns the frames directory path, or None on failure.
    """
    try:
        base, layout = _render_base_image(trade)
        ts         = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        frames_dir = os.path.join(OUTPUT_DIR, f"chart_anim_{ts}")
        os.makedirs(frames_dir, exist_ok=True)

        for i in range(ANIM_FRAMES):
            t        = i / max(ANIM_FRAMES - 1, 1)
            progress = _ease_out_cubic(t)
            frame    = _apply_bar_frame(base, layout, progress)
            frame.save(os.path.join(frames_dir, f"frame_{i:03d}.png"), "PNG")

        log.info(f"[chart] Animated frames → {frames_dir} ({ANIM_FRAMES} frames)")
        return frames_dir

    except Exception as e:
        log.error(f"[chart] Animated generation failed: {e} — will fall back to static PNG.")
        return None


# ── Static fallback ───────────────────────────────────────────────────────────

def render_whale_graphic(trade: dict, output_path: str) -> bool:
    """Render the final (fully filled) frame as a single static PNG."""
    try:
        base, layout = _render_base_image(trade)
        final        = _apply_bar_frame(base, layout, progress=1.0)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        final.save(output_path, "PNG")
        log.info(f"[chart] Static graphic → {output_path}")
        return True
    except Exception as e:
        log.error(f"[chart] Static render failed: {e}")
        return False


# ── Public entry point ────────────────────────────────────────────────────────

def generate_chart(trade=None) -> str | None:
    """
    Returns either:
      - A directory path (str) containing animated frames  → video_factory detects via os.path.isdir()
      - A PNG file path (str) if animation failed           → backward-compat static path
      - None on total failure
    """
    if isinstance(trade, str) or trade is None:
        trade = {"usd_value": 0, "market_title": "Market", "outcome": "Yes", "price": 0.5}

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # Try animated first
    frames_dir = generate_chart_animated(trade)
    if frames_dir:
        return frames_dir

    # Fallback: static PNG
    ts          = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"graphic_{ts}.png")
    success     = render_whale_graphic(trade, output_path)
    return output_path if success else None


if __name__ == "__main__":
    t = {"usd_value": 116288, "market_title": "Will Celtic FC win on 2026-04-05?", "outcome": "Yes", "price": 0.44}
    result = generate_chart(t)
    print(f"Result: {result}")
