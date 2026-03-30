#!/usr/bin/env python3
"""
video_factory.py — PolyVision Layer 3 Tool
Generates a complete Short-form Video Package (TikTok/Reels) using OpenAI TTS.
Sends the audio (.mp3), visual (.png), and caption directly to the user's inbox
for hyper-optimized, native algorithmic uploading.
"""

import os
import io
import json
import base64
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM    = os.getenv("RESEND_FROM", "onboarding@resend.dev")
TARGET_EMAIL   = os.getenv("BRIEFING_EMAIL_TO", "adair.clark@gmail.com")

def _generate_tiktok_script(payload: dict) -> dict:
    """Uses GPT-4o-mini to write a viral 15-second TikTok script and caption."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        usd = float(payload.get('usd_value', 0))
        outcome = payload.get('outcome', 'Yes')
        market = payload.get('market_title', 'an unknown market')
        win_rate = payload.get('wallet_win_rate')
        
        wr_str = f"{win_rate:.0%}" if win_rate else "an unknown"

        prompt = (
            "You are a viral TikTok scriptwriter specializing in high-energy finance and crypto content. "
            "Your goal is to write a fast, engaging 15-second voiceover script (roughly 30-40 words) about a massive prediction market or crypto trade that just occurred.\n\n"
            f"Fact Sheet:\n"
            f"- Trade Size: ${usd:,.0f} USD\n"
            f"- Action: Bought {outcome}\n"
            f"- Market: \"{market}\"\n"
            f"- Whale's Historic Win Rate: {wr_str}\n\n"
            "Rules:\n"
            "1. Start with a massive hook (e.g. 'Holy crap, someone just threw...').\n"
            "2. Ensure it is conversational and spoken (no hashtags in the script text).\n"
            "3. Provide exactly two things in a JSON format:\n"
            "   - 'script': The exact words to be read by the TTS voice.\n"
            "   - 'caption': The text caption for the TikTok post (include relevant hashtags here).\n\n"
            "Return STRICTLY a JSON object matching this schema:\n"
            "{\n  \"script\": \"...\",\n  \"caption\": \"...\"\n}"
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            temperature=0.8,
            timeout=15
        )
        return json.loads(resp.choices[0].message.content.strip())
    except Exception as e:
        log.error(f"[VideoFactory] Failed to generate script: {e}")
        return {
            "script": f"Someone just dropped ${usd:,.0f} on {outcome} for {market}. Track these whale wallets live with the PolyVision app.",
            "caption": f"Whale Alert 🚨 ${usd:,.0f} moved on {market}. #polymarket #crypto #trading"
        }

def _generate_tts_audio(script_text: str) -> bytes:
    """Uses OpenAI's native TTS to generate a high-energy masculine voiceover (.mp3)."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        response = client.audio.speech.create(
            model="tts-1",
            voice="onyx",  # Onyx is a deep, masculine, narrative-style voice
            input=script_text
        )
        return response.content
    except Exception as e:
        log.error(f"[VideoFactory] Failed to generate TTS audio: {e}")
        return None

def dispatch_video_package(payload: dict, img_buf: io.BytesIO):
    """Orchestrator: Generates content and emails it natively via Resend."""
    if not RESEND_API_KEY or not OPENAI_API_KEY:
        log.warning("[VideoFactory] Missing API keys. Skipping package dispatch.")
        return

    log.info(f"[VideoFactory] Generating TikTok package for ${payload.get('usd_value', 0):,.0f} trade...")
    
    # 1. Generate Voiceover Script & Text Caption
    content = _generate_tiktok_script(payload)
    script_text = content.get("script", "")
    caption = content.get("caption", "#polyvision #trading")
    
    # 2. Render MP3 Voiceover Audio
    audio_bytes = _generate_tts_audio(script_text)
    if not audio_bytes:
        log.warning("[VideoFactory] Aborting email dispatch due to audio failure.")
        return
        
    # 3. Serialize Attachments (MP3 and PNG)
    mp3_b64 = base64.b64encode(audio_bytes).decode('utf-8')
    img_b64 = base64.b64encode(img_buf.getvalue()).decode('utf-8')

    html_content = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; color: #111827;">
        <h2 style="color: #ea4335;">🎥 TikTok / Reels Video Package</h2>
        <p>A massive algorithmic trade has hit the tape. Your AI-synthesized short-form content is attached below.</p>
        
        <p><strong>Voiceover Script (The Audio Attached):</strong></p>
        <div style="background: #f3f4f6; padding: 16px; border-radius: 8px; margin-bottom: 24px; font-style: italic;">
            "{script_text}"
        </div>
        
        <p><strong>Post Caption (Copy & Paste):</strong></p>
        <div style="background: #f3f4f6; padding: 16px; border-radius: 8px;">
            {caption}
        </div>

        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;" />
        <p style="font-size: 13px; color: #6b7280;">
            <strong>How to post:</strong> Save both the <code>.mp3</code> and <code>.png</code> to your phone. Open TikTok, use the Green Screen effect for the <code>.png</code>, and add the audio file as the video sound.
        </p>
    </div>
    """

    data = {
        "from": RESEND_FROM,
        "to": [TARGET_EMAIL],
        "subject": f"TikTok Package: ${payload.get('usd_value', 0):,.0f} moved on {payload.get('market_title', 'Unknown Market')[:30]}...",
        "html": html_content,
        "attachments": [
            {
                "content": img_b64,
                "filename": "trade_card.png"
            },
            {
                "content": mp3_b64,
                "filename": "voiceover.mp3"
            }
        ]
    }

    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json=data,
            timeout=15
        )
        r.raise_for_status()
        log.info("[VideoFactory] ✅ TikTok package successfully dispatched to inbox.")
    except Exception as e:
        log.error(f"[VideoFactory] Failed to email package: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import argparse
    parser = argparse.ArgumentParser(description="PolyVision Video Factory")
    parser.add_argument("--test", action="store_true", help="Run with fixture payload and send test email")
    args = parser.parse_args()

    if args.test:
        print("🧪 Running Video Factory test payload...\n")
        TEST_PAYLOAD = {
            "alert_id":               "test-video-001",
            "alert_tier":             "CLUSTER",
            "trader_handle":          "The Oracle of Oregon",
            "wallet_address":         "0xDeAdBeEf1234567890abcdef",
            "market_title":           "Will the Fed cut rates in March 2026?",
            "outcome":                "Yes",
            "price":                  0.72,
            "usd_value":              250000.00,
            "wallet_win_rate":        0.73,
            "source":                 "POLYMARKET"
        }
        
        # Synthesize a pure black image buffer for the test since we don't have the canvas generator locally
        from PIL import Image
        import io
        img = Image.new('RGB', (1080, 1920), color = (0, 0, 0))
        img_buf = io.BytesIO()
        img.save(img_buf, format='PNG')
        img_buf.seek(0)
        
        dispatch_video_package(TEST_PAYLOAD, img_buf)
        print("✅ Test execution complete! Check your inbox.")
