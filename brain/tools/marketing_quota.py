import os
import json
from datetime import datetime, timedelta
import logging

log = logging.getLogger(__name__)

QUOTA_FILE = os.path.join(os.path.dirname(__file__), 'marketing_quota.json')
MAX_DAILY_VIDEOS = 2

def _get_current_period() -> str:
    """
    Calculates the active mathematical 'Day' respecting the 9:00 AM server reset threshold.
    If current time is between 12:00 AM and 8:59 AM, it mathematically ties to yesterday.
    """
    now = datetime.now()
    if now.hour < 9:
        period_start = now - timedelta(days=1)
        return period_start.strftime("%Y-%m-%d 09:00")
    else:
        return now.strftime("%Y-%m-%d 09:00")

def throttle_video_generation() -> bool:
    """
    Validates execution queue limits against the deterministic `marketing_quota.json` file.
    Returns True if execution clears. Returns False if quota strictly reached.
    """
    current_period = _get_current_period()
    state = {"period": current_period, "count": 0}
    
    # Safely load the flat file state array
    if os.path.exists(QUOTA_FILE):
        try:
            with open(QUOTA_FILE, 'r') as f:
                disk_state = json.load(f)
                # Ensure the JSON payload mathematically matches the current 9AM cycle
                if disk_state.get("period") == current_period:
                    state["count"] = int(disk_state.get("count", 0))
        except Exception as e:
            log.warning(f"Quota logic encountered JSON parsing exception: {e}. Rebuilding tracker.")

    # Validate Quota Gate limit (Max 2 videos per day starting at 9 AM)
    if state["count"] >= MAX_DAILY_VIDEOS:
        log.warning(f"[Quota Limit] PolyVision already generated {state['count']} videos since {current_period}. Execution securely blocked.")
        return False
        
    # Execution cleared! Increment the tracking array.
    state["count"] += 1
    
    # Save explicitly back to physical disk
    try:
        with open(QUOTA_FILE, 'w') as f:
            json.dump(state, f, indent=4)
        log.info(f"[Quota Passed] Tracking updated: {state['count']}/{MAX_DAILY_VIDEOS} utilized for the {current_period} cycle.")
        return True
    except Exception as e:
        log.error(f"Failed to record quota state safely tracking failure: {e}")
        # Return True anyway so we do not unexpectedly suppress videos if permissions glitch
        return True

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Current Execution Mathematical Cycle: {_get_current_period()}")
    print(f"Throttle Evaluation Hook Result: {throttle_video_generation()}")
