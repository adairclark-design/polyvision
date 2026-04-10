import os, sys, json
import requests

os.environ["CREATOMATE_API_KEY"] = os.getenv("CREATOMATE_API_KEY", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
os.environ["FAL_KEY"] = os.getenv("FAL_KEY", "")

from .marketing.chart_generator import generate_chart
from .marketing.tts_generator import generate_voiceover
from .marketing.background_generator import generate_background
from .marketing.video_factory import create_video, _upload_asset

from .marketing.ai_copywriter import generate_social_copy

def dispatch_video_alert(trade_payload=None, include_music=False):
    if trade_payload is None:
        trade_payload = {
            "usd_value": 211000, 
            "market_title": "Will the US and Iran sign a ceasefire by April 7?", 
            "outcome": "Yes", 
            "price": 0.45
        }
    
    tradeData = trade_payload
    print("Generating dynamic Brand Guidelines Copy natively...")
    copy_matrix = generate_social_copy(tradeData)
    hook_text = copy_matrix.get("hook_text", "Massive whale movement detected.")
    script_text = copy_matrix.get("script_text", "PolyVision intercepted a whale trade natively. Link in bio to see more details.")
    email_title = copy_matrix.get("title", "Whale Alert")
    email_desc = copy_matrix.get("description", "A huge trade occurred on Polymarket.")
    email_tags = copy_matrix.get("hashtags", "#polyvision #whale")
    email_sound = copy_matrix.get("trending_sound", "Use Sound: Trending Suspense/Phonk via App Explorer")

    print("Generating native Asset Matrix (Audio, Chart, Background)...")

    # Trigger accurate asset builds
    chart_local = generate_chart(tradeData)

    audio_local = generate_voiceover(script=script_text)
    bg_local = generate_background()

    print(f"Uploading assets to Catbox.moe CDN...")
    bg_public = _upload_asset(bg_local)
    print(f"Background successfully CDN hosted: {bg_public}")

    # Final synthesis
    whale_logo_path = os.path.join(os.path.dirname(__file__), "marketing", "assets", "whale_logo.png")
    vid = create_video(
        chart_local,
        audio_local,
        hook_text,
        bg_public,
        whale_logo_path,
        include_music=include_music
    )

    resend_key = os.getenv("RESEND_API_KEY", "")

    if vid and resend_key:
        print(f"Video created -> {vid}. Dispatching Email via Resend natively...")
        
        headers = {
            "Authorization": f"Bearer {resend_key}",
            "Content-Type": "application/json"
        }
        # Automatically Download the MP4 natively to tmp container layer
        desktop_ptr = "/tmp/PolyVision_Trade_Test.mp4"
        import base64
        try:
            vid_data = requests.get(vid, timeout=30).content
            with open(desktop_ptr, "wb") as f:
                f.write(vid_data)
            print(f"✅ Video automatically downloaded directly to your Desktop: {desktop_ptr}")
            
            # Encode for Resend Attachment
            b64_vid = base64.b64encode(vid_data).decode('utf-8')
            attachment_payload = [{
                "filename": f"PolyVision_Trade_{tradeData.get('usd_value', 0)}.mp4",
                "content": b64_vid
            }]
        except Exception as e:
            print(f"Failed to auto-download video or encode attachment: {e}")
            attachment_payload = []

        payload = {
            "from": "PolyVision Delivery <alerts@polyvision.app>",
            "to": "adairclark@gmail.com",
            "subject": f"🚨 PolyVision Video Ready: {email_title}",
            "attachments": attachment_payload,
            "html": f"""
            <div style='font-family:sans-serif; text-align:center;'>
                <h2>Your Marketing Video is Ready</h2>
                <p>The visual matrix for the ${tradeData.get('usd_value', 0):,.0f} trade successfully rendered.</p>
                <a href='{vid}' style='background:#10B981;color:white;padding:12px 24px;text-decoration:none;border-radius:6px;font-weight:bold;display:inline-block;margin-top:20px;margin-bottom:30px;'>Original Cloud MP4 Link</a>
            </div>
            <div style='font-family:sans-serif; background:#f4f4f5; padding:20px; border-radius:8px; text-align:left; max-width: 600px; margin: 0 auto;'>
                <h3 style='margin-top:0;'>📱 Social Media Copy</h3>
                <p><b>Title:</b> {email_title}</p>
                <p><b>Description:</b> {email_desc}</p>
                <p><b>Hashtags:</b> {email_tags}</p>
                <hr style="border: 0; border-top: 1px solid #ccc; margin: 15px 0;">
                <p><b>🎵 Suggested Audio Route:</b> <span style="background: #e2e8f0; padding: 2px 6px; border-radius: 4px;">{email_sound}</span></p>
                <p style='font-size: 11px; color:#555;'><i>(Upload the MP4 silently, then tap "Add Sound" on TikTok/Instagram and search the suggested prompt above to algorithmically ride native trends!)</i></p>
            </div>
            <p style='text-align:center; margin-top:20px; font-size:12px; color:gray'>Sent securely using updated API validation.</p>
            """
        }
        
        try:
            resp = requests.post("https://api.resend.com/emails", headers=headers, json=payload)
            print(f"Resend HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Resend Dispatch Exception: {e}")
            
        with open("/tmp/polyvision_realworld.txt", "w") as f:
            f.write(vid)
    else:
        print("Render failed or Resend Key missing.")

if __name__ == "__main__":
    dispatch_video_alert(include_music=True)
