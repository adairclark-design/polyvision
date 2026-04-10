#!/usr/bin/env python3
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
ASSETS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'assets')

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
    width, height = 1080, 1920
    
    # ── Match Genuine Logo Background (13, 17, 23)
    img = Image.new('RGB', (width, height), (13, 17, 23))
    draw = ImageDraw.Draw(img)
    
    logo_rendered = False
    
    # Load the highly proprietary custom PolyVision Brand Logo
    try:
        logo_path = os.path.join(ASSETS_DIR, 'whale_logo.png')
        logo = Image.open(logo_path).convert("RGBA")
        
        # Scale the custom brand logo beautifully
        logo.thumbnail((675, 675), Image.Resampling.LANCZOS)
        lw, lh = logo.size
        
        # Absolute center horizontally
        center_x = int((width - lw) / 2)
        center_y = int((height - lh) / 2) - 120
        
        # Paste with transparent alpha mask identically
        img.paste(logo, (center_x, center_y), logo)
        logo_rendered = True
        
        # Place the master PolyVision text block
        font_main = _get_font(120, bold=True)
        m_text = "PolyVision"
        m_bbox = draw.textbbox((0, 0), m_text, font=font_main)
        mw = m_bbox[2] - m_bbox[0]
        
        # Electric glow PolyVision primary text
        draw.text(((width - mw) / 2, center_y + lh + 60), m_text, fill="#00E6F0", font=font_main)
        
        # Place the URL text underneath for final CTA authority
        font_url = _get_font(60, bold=True)
        u_text = "polyvision.app"
        u_bbox = draw.textbbox((0, 0), u_text, font=font_url)
        uw = u_bbox[2] - u_bbox[0]
        draw.text(((width - uw) / 2, center_y + lh + 210), u_text, fill="#3B82F6", font=font_url)
        
    except Exception as e:
        log.warning(f"Could not render absolute logo match: {e}")
            
    if not logo_rendered:
        # Extreme fallback
        font_logo = _get_font(80, bold=True)
        ltext = "POLYVISION"
        l_bbox = draw.textbbox((0, 0), ltext, font=font_logo)
        draw.text(((width - (l_bbox[2]-l_bbox[0])) / 2, height / 2 - 100), ltext, fill="#00E6F0", font=font_logo)

    ts = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"outro_{ts}.png")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG")
    log.info(f"Outro Graphic saved → {output_path}")
    return output_path

if __name__ == "__main__":
    generate_outro()
