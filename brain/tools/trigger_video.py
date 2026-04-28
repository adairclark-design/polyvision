import os
import json

# Load secrets first!
secrets_path = os.path.join(os.path.dirname(__file__), '..', '..', 'secrets.json')
with open(secrets_path, 'r') as f:
    secrets = json.load(f)

# Set env vars for the script
os.environ["OPENAI_API_KEY"] = secrets.get("OPENAI_API_KEY", "")
os.environ["CREATOMATE_API_KEY"] = secrets.get("CREATOMATE_API_KEY", "")
os.environ["FAL_KEY"] = secrets.get("FAL_KEY", "")
os.environ["RESEND_API_KEY"] = secrets.get("RESEND_API_KEY", "")

# NOW import the pipeline!
from execute_real_world import dispatch_video_alert

payload = {
    "usd_value": 126000,
    "market_title": "France to win the 2026 FIFA World Cup",
    "outcome": "No",
    "price": 0.82,
    "trader_handle": "Dishonest-Bloom",
    "wallet_win_rate": 0.82
}

print("Triggering video generation...")
dispatch_video_alert(payload, include_music=True)
print("Done.")
