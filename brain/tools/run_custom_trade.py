import os
import sys
import json
from pathlib import Path
def _load_secrets(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
            for k, v in data.items():
                if isinstance(v, str):
                    os.environ[k] = v
            return data
    except Exception as e:
        print(f"Failed to load secrets: {e}")
        return {}

secrets = _load_secrets("/Users/adairclark/Desktop/AntiGravity/polyvision_deploy/secrets.json")

# Insert path to marketing tools
sys.path.insert(0, "/Users/adairclark/Desktop/AntiGravity/polyvision_deploy/brain/tools/marketing")

from agent_generator import run_tiktok_video_for_trade
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

best_trade = {
    "usd_value": 126000,
    "market_title": "France to win the 2026 FIFA World Cup",
    "outcome": "No",
    "price": 0.18,
    "source": "POLYMARKET",
    "trader_handle": "Dishonest-Bloom",
    "wallet_win_rate": 0.82,
    "age_label": "just now",
    "id": "custom_trade_france_fifa_126k"
}

# Ensure RESEND_API_KEY is properly set if it exists or fallback
secrets["RESEND_API_KEY"] = os.getenv("RESEND_API_KEY", secrets.get("RESEND_API_KEY", ""))

print("Running pipeline for custom trade...")
success = run_tiktok_video_for_trade(best_trade, secrets, dry_run=False)

if success:
    print("\n✅ Successfully triggered the video pipeline and dispatched via email!")
else:
    print("\n❌ Pipeline failed.")
