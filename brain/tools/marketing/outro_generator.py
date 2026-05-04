#!/usr/bin/env python3
from __future__ import annotations
"""
outro_generator.py
Generates a polished 720x1280 Outro screen natively in Python.
"""
import os
import io
import ssl
import logging
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'marketing')
ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'assets')

# ── Daily outro cache: regenerate once per UTC day, reuse across all videos ───────
_outro_cache: dict[str, str] = {}  # {"YYYY-MM-DD": "/path/to/outro.png"}

def _get_font(size: int, bold=False):
    try:
        weight = "Bold" if bold else "Regular"
        path = f"/System/Library/Fonts/Supplemental/Arial {weight}.ttf"
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

def _draw_gradient(img, color_top, color_bottom):
    draw = ImageDraw.Draw(img)
    width, height = img.size
    for y in range(height):
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * y / height)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * y / height)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))



def generate_outro() -> str | None:
    from datetime import datetime, timezone as _tz
    today_key = datetime.now(_tz.utc).strftime("%Y-%m-%d")

    # Return cached path if it was generated today and still exists
    cached = _outro_cache.get(today_key)
    if cached and os.path.exists(cached):
        log.info(f"[Outro] Using cached outro for {today_key}: {cached}")
        return cached

    width, height = 1080, 1920

    # ── Fully transparent canvas — text/logo float over the Kling background ──
    img  = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    logo_rendered = False

    try:
        logo_path = os.path.join(ASSETS_DIR, 'whale_logo.png')
        logo = Image.open(logo_path).convert("RGBA")

        # Large centered logo — grouped with text below as a single centered unit
        logo.thumbnail((500, 500), Image.Resampling.LANCZOS)
        lw, lh = logo.size

        # Measure text heights before laying out so we can center the whole group
        font_main = _get_font(108, bold=True)
        font_url  = _get_font(54,  bold=False)
        m_bbox = draw.textbbox((0, 0), "PolyVision",     font=font_main)
        u_bbox = draw.textbbox((0, 0), "polyvision.app", font=font_url)
        mh = m_bbox[3] - m_bbox[1]
        uh = u_bbox[3] - u_bbox[1]

        # Total group: logo + 60px gap + PolyVision + 30px gap + polyvision.app
        group_h  = lh + 60 + mh + 30 + uh
        group_y0 = int((height - group_h) / 2)   # true vertical center

        # Logo
        center_x = int((width - lw) / 2)
        center_y = group_y0
        img.paste(logo, (center_x, center_y), logo)
        logo_rendered = True

        # Soft blurred dark halo behind text only
        from PIL import ImageFilter
        halo_y = center_y + lh + 20
        halo_h = 280
        halo   = Image.new('RGBA', (width, halo_h), (0, 0, 0, 0))
        halo_d = ImageDraw.Draw(halo)
        halo_d.rounded_rectangle([60, 10, width - 60, halo_h - 10], radius=50, fill=(0, 0, 0, 130))
        halo   = halo.filter(ImageFilter.GaussianBlur(radius=22))
        img.paste(halo, (0, halo_y), halo)

        # "PolyVision" — electric cyan
        m_text = "PolyVision"
        mw     = m_bbox[2] - m_bbox[0]
        text_y = group_y0 + lh + 60
        draw.text(((width - mw) / 2 + 3, text_y + 3), m_text, fill=(0, 0, 0, 160), font=font_main)
        draw.text(((width - mw) / 2,     text_y),     m_text, fill="#00E6F0",       font=font_main)

        # "polyvision.app" — smaller, brand blue
        u_text = "polyvision.app"
        uw     = u_bbox[2] - u_bbox[0]
        url_y  = text_y + mh + 30
        draw.text(((width - uw) / 2 + 2, url_y + 2), u_text, fill=(0, 0, 0, 140), font=font_url)
        draw.text(((width - uw) / 2,     url_y),     u_text, fill="#3B82F6",       font=font_url)


    except Exception as e:
        log.warning(f"Could not render logo outro: {e}")

    if not logo_rendered:
        font_logo = _get_font(80, bold=True)
        ltext  = "POLYVISION"
        l_bbox = draw.textbbox((0, 0), ltext, font=font_logo)
        draw.text(((width - (l_bbox[2] - l_bbox[0])) / 2, height / 2 - 100), ltext, fill="#00E6F0", font=font_logo)

    ts           = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path  = os.path.join(OUTPUT_DIR, f"outro_{ts}.png")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG")
    log.info(f"Outro Graphic saved → {output_path}")
    _outro_cache[today_key] = output_path  # cache for the rest of the day
    return output_path

if __name__ == "__main__":
    generate_outro()
