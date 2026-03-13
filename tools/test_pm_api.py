import requests
import sys
import json

wallet = sys.argv[1]
print(f"Fetching API stats for {wallet}...")
resp = requests.get(
    "https://data-api.polymarket.com/v1/leaderboard",
    params={"category": "OVERALL", "timePeriod": "ALL", "orderBy": "PNL", "limit": 10, "user": wallet}
)
try:
    print(json.dumps(resp.json(), indent=2))
except Exception as e:
    print("Failed to decode JSON:", e)
    print("Raw response:", resp.text)
