import sys, os, time, json, urllib.request
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Load secrets using absolute PolyVision path (SIP Sandbox safe)
secrets = {}
try:
    with open("/Users/adairclark/Desktop/AntiGravity/VisionEdgeAI/vision-edge-ai/secrets.json", "r") as f:
        secrets = json.load(f)
except Exception as e:
    log.error(f"Secrets load failed: {e}")

resend_key = secrets.get("RESEND_API_KEY", "")
resend_from = secrets.get("RESEND_FROM", "onboarding@resend.dev")

# Inject environment directly so modules don't crash trying to read relative paths
os.environ["ELEVEN_API_KEY"] = secrets.get("ELEVEN_API_KEY", "")
os.environ["FAL_KEY"] = secrets.get("FAL_KEY", "")
os.environ["OPENAI_API_KEY"] = secrets.get("OPENAI_API_KEY", "")
os.environ["CREATOMATE_API_KEY"] = secrets.get("CREATOMATE_API_KEY", "")

sys.path.append("/Users/adairclark/Desktop/AntiGravity/PolyVision/brain/tools")

from chart_generator import generate_chart
from tts_generator import generate_voiceover
from background_generator import generate_background
from video_factory import create_video

log.info("Starting Simulated Pipeline Test...")

tradeData = {"date": "2024-04-12 18:00:00", "size_usd": 150000}
marketData = {"question": "Will AI AGI happen by 2027?", "probability": 0.85, "active_traders": 1500}
handle = "0xMockWhaleWallet42"
hook_text = "Massive $150k injection detected. Is someone betting on AGI?"
script_text = "PolyVision just caught an absolutely massive 150 thousand dollar trade. A hyper-accurate whale is convinced this market is exploding. Here is exactly what they bought."

chart_path = generate_chart(tradeData, marketData, handle)
audio_path = generate_voiceover(
    script=script_text,
)
bg_url = generate_background()

logo_path = "/Users/adairclark/Desktop/AntiGravity/PolyVision/dashboard/assets/whale_logo.png"

# Execute Render
video_url = create_video(chart_path, audio_path, hook_text, bg_url, logo_path)

if video_url and resend_key:
    log.info(f"Video created -> {video_url}. Sending Email Direct bypassing sandbox...")
    html_body = f"""
    <h2>PolyVision TikTok Delivery Matrix</h2>
    <p>A high-value whale trade has triggered an automated media render.</p>
    <ul>
        <li><b>Target Identity:</b> {handle}</li>
        <li><b>Trade Value:</b> $150,000</li>
        <li><b>Prediction Market:</b> {marketData["question"]}</li>
    </ul>
    <h3>📥 <a href="{video_url}">Download 1080p Video (No BGM)</a></h3>
    <p><i>Your seamless cross-faded TikTok loop is ready for publishing.</i></p>
    """
    
    req = urllib.request.Request("https://api.resend.com/emails", method="POST")
    req.add_header("Authorization", f"Bearer {resend_key}")
    req.add_header("Content-Type", "application/json")
    
    payload = json.dumps({
        "from": resend_from,
        "to": "adairclark@gmail.com",
        "subject": f"🐋 TikTok Video Ready: {handle} traded $150,000",
        "html": html_body
    }).encode("utf-8")
    
    try:
        urllib.request.urlopen(req, data=payload)
        log.info("Email Dispatched successfully to Inbox!")
    except Exception as e:
        log.error(f"Resend dispatch failed: {e}")
elif not resend_key:
    log.error("RESEND_API_KEY not found.")
else:
    log.error("Video creation failed.")
