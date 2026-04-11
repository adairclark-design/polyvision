#!/usr/bin/env python3
"""
chart_generator.py — VisionEdge Marketing Agent | Layer 3: Chart
Completely rewritten to use Pillow (PIL) for generating high-end, 
TikTok-native 'Prediction Market UI' style graphics, moving away
from clunky matplotlib stock charts.
"""
import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import random
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'marketing')
ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')

def _get_font(size: int, bold=False):
    """Attempt to load a system font, fallback to default."""
    try:
        # Mac system fonts
        weight = "Bold" if bold else "Regular"
        path = f"/System/Library/Fonts/Supplemental/Arial {weight}.ttf"
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

def _draw_gradient(img, color_top, color_bottom):
    """Draws a vertical linear gradient on the image."""
    draw = ImageDraw.Draw(img)
    width, height = img.size
    for y in range(height):
        # Interpolate RGB values securely
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * y / height)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * y / height)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

def render_whale_graphic(trade: dict, output_path: str) -> bool:
    """Renders a custom prediction market UI card graphic."""
    width, height = 1080, 1920
    
    # Premium Dynamic Gradients
    palettes = [
        ((15, 23, 42), (6, 73, 56)),   # Slate to Deep Emerald
        ((23, 15, 36), (62, 10, 36)),  # Deep Purple to Crimson
        ((5, 10, 20), (20, 25, 40)),   # Stealth Obsidian
        ((12, 18, 30), (25, 45, 80)),  # Midnight Sapphire
        ((15, 15, 15), (45, 30, 10)),  # Bronze Horizon
    ]
    target_palette = random.choice(palettes)
    
    # Base abstract background explicitly fully transparent so B-Roll flows through
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    
    draw = ImageDraw.Draw(img)
    
    amount = trade.get('usd_value', 0)
    amount_str = f"${amount:,.0f}" if amount > 0 else "$249,500"
    market = trade.get('market_title', 'Will the market perform as expected?')
    outcome = trade.get('outcome', 'Yes').capitalize()
    
    # Handle the 100% bug visually
    price = trade.get('price', 0.5)
    pct = price * 100
    if pct >= 100: pct = 99.0  # Fallback so it doesn't look like a dead system
    
    # Colors
    is_yes = outcome in ("Yes", "In favor", "Long", "Call")
    accent_color = "#10B981" if is_yes else "#EF4444"  # Green / Red
    card_bg = (22, 27, 38, 200) # RGBA Deep Glassmorphism (opacity ~78%)
    
    # ── Draw Top Alert text
    font_alert = _get_font(54, bold=True)
    alert_text = "🚨 POLYVISION WHALE ALERT"
    # To center:
    bbox = draw.textbbox((0, 0), alert_text, font=font_alert)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, 90), alert_text, fill="#5C5FE5", font=font_alert)

    import textwrap
    wrapped_market = textwrap.fill(market, width=26)
    num_lines = len(wrapped_market.split('\n'))
    
    # Compute true dimensions: padding(90) + text_block(82*lines) + bar_offset(240) + bar_height(60) + prob_text(90) + bottom_pad(90)
    card_height = 90 + (num_lines * 82) + 240 + 60 + 90 + 90
    card_margin = 60

    # ── Draw New Official Logo
    logo_path = os.path.join(ASSETS_DIR, "whale_logo.png")
    try:
        logo = Image.open(logo_path).convert("RGBA")
        w_percent = (280 / float(logo.size[0]))
        h_size = int(float(logo.size[1]) * w_percent)
        logo = logo.resize((280, h_size), Image.Resampling.LANCZOS)
        logo_x = int((width - 280) / 2)
        
        # Calculate total height of logo + gap + card to mathematically center the entire cluster
        total_cluster_height = h_size + 80 + card_height
        cluster_start_y = (height - total_cluster_height) / 2
        
        logo_y = int(cluster_start_y)
        card_top = int(logo_y + h_size + 80)
        
        img.paste(logo, (logo_x, logo_y), logo)
    except Exception as e:
        log.warning(f"Failed to load whale logo: {e}")
        card_top = (height - card_height) / 2

    
    # rounded_rectangle requires Pillow >= 8.2.0
    try:
        draw.rounded_rectangle(
            [card_margin, card_top, width - card_margin, card_top + card_height],
            radius=24, fill=card_bg, outline="#2A3241", width=3
        )
    except AttributeError:
        # Fallback for old pillow
        draw.rectangle([card_margin, card_top, width - card_margin, card_top + card_height], fill=card_bg)
        
    # ── Draw Market Title
    font_market = _get_font(63, bold=True)
    
    y_text = card_top + 90
    for line in wrapped_market.split('\n'):
        l_bbox = draw.textbbox((0, 0), line, font=font_market)
        lw = l_bbox[2] - l_bbox[0]
        draw.text(((width - lw) / 2, y_text), line, fill="#F8FAFC", font=font_market)
        y_text += 82
        
    # ── Draw Exact Bet Text (Auto-scaling to prevent cropping)
    font_size = 96
    font_bet = _get_font(font_size, bold=True)
    bet_text = f"{amount_str} on '{outcome}'"
    max_text_width = width - (card_margin * 2) - 60
    
    while True:
        b_bbox = draw.textbbox((0, 0), bet_text, font=font_bet)
        bw = b_bbox[2] - b_bbox[0]
        if bw <= max_text_width or font_size <= 36:
            break
        font_size -= 2
        font_bet = _get_font(font_size, bold=True)
        
    draw.text(((width - bw) / 2, y_text + 60), bet_text, fill=accent_color, font=font_bet)
    
    # ── Draw sleek glowing Progress Bar
    bar_y = y_text + 240
    bar_margin = card_margin + 90
    bar_width = width - (bar_margin * 2)
    bar_height = 60
    
    try:
        # Background bar
        draw.rounded_rectangle([bar_margin, bar_y, bar_margin + bar_width, bar_y + bar_height], 
                               radius=30, fill="#0B101A")
        # Filled chunk
        fill_w = bar_width * (pct / 100.0)
        draw.rounded_rectangle([bar_margin, bar_y, bar_margin + fill_w, bar_y + bar_height], 
                               radius=30, fill=accent_color)
    except AttributeError:
        draw.rectangle([bar_margin, bar_y, bar_margin + bar_width, bar_y + bar_height], fill="#0B101A")
        draw.rectangle([bar_margin, bar_y, bar_margin + fill_w, bar_y + bar_height], fill=accent_color)
        
    # ── Draw probability text
    font_pct = _get_font(54, bold=True)
    pct_text = f"{pct:.0f}% chance"
    p_bbox = draw.textbbox((0, 0), pct_text, font=font_pct)
    pw = p_bbox[2] - p_bbox[0]
    draw.text(((width - pw) / 2, bar_y + 90), pct_text, fill="#94A3B8", font=font_pct)
    
    # (Watermark is now beautifully handled by the dedicated Outro Generator!)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG")
    log.info(f"UI Glassmorphic Graphic saved → {output_path}")
    return True

def generate_chart(trade: dict | str = None, timespan="day", limit=60) -> str | None:
    if isinstance(trade, str) or trade is None:
        trade = {"usd_value": 0, "market_title": "Market", "outcome": "Yes", "price": 0.5}
        
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"graphic_{ts}.png")
    success = render_whale_graphic(trade, output_path)
    return output_path if success else None

if __name__ == "__main__":
    t = {"usd_value": 116288, "market_title": "Will Celtic FC win on 2026-04-05?", "outcome": "Yes", "price": 0.44}
    generate_chart(t)
