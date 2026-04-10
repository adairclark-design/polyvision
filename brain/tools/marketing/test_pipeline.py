import sys, os, time
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

import sys, types
mock_db = types.ModuleType("agent_db")
mock_db.init_db = lambda: None
mock_db.log_campaign = lambda w, m, t, h, f: None
sys.modules["agent_db"] = mock_db

sys.path.append("/Users/adairclark/Desktop/AntiGravity/PolyVision/brain/tools")
from notifier import deliver_tiktok_package_email

from chart_generator import generate_trade_chart
from tts_generator import generate_tts
from background_generator import generate_background
from video_factory import create_video

log.info("Starting Simulated Pipeline Test...")

tradeData = {"date": "2024-04-12 18:00:00", "size_usd": 150000}
marketData = {"question": "Will AI AGI happen by 2027?", "probability": 0.85, "active_traders": 1500}
handle = "0xMockWhaleWallet42"
hook_text = "Massive $150k injection detected. Is someone betting on AGI?"
script_text = "PolyVision just caught an absolutely massive 150 thousand dollar trade. A hyper-accurate whale is convinced this market is exploding. Here's exactly what they bought."

chart_path = generate_trade_chart(tradeData, marketData, handle)
audio_path = generate_tts(script_text)
bg_url = generate_background()

logo_path = "/Users/adairclark/Desktop/AntiGravity/PolyVision/dashboard/assets/whale_logo.png"

# Execute Render using new logic
video_url = create_video(chart_path, audio_path, hook_text, bg_url, logo_path)

if video_url:
    log.info(f"Video created! Sending delivery email -> {video_url}")
    deliver_tiktok_package_email(
        video_url, handle, "$150,000", marketData["question"]
    )
    log.info("Email Dispatched successfully to Inbox!")
else:
    log.error("Video creation failed.")
