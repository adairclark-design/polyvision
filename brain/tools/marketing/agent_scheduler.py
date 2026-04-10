#!/usr/bin/env python3
"""
agent_scheduler.py — VisionEdge Marketing Agent | Layer 2: Cron Orchestrator
Zero-touch autonomous scheduler. Runs as a standalone process (or alongside
the Next.js app via a companion process).

Schedule:
  - 09:00 EST: Morning generation cycle (X post + TikTok video + Reddit draft)
  - 18:00 EST: Evening generation cycle (X post only — no video spam)
  - 00:05 EST: Nightly reflection cycle (analyze telemetry → update brain)

Run this process persistently:
  python tools/marketing/agent_scheduler.py

Or add it to your existing server startup script.
"""
import os
import sys
import time
import random
import logging
from datetime import datetime

# ── Path ──────────────────────────────────────────────────────────────────────
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

# ── Logging ───────────────────────────────────────────────────────────────────
log_path = os.path.join(THIS_DIR, '..', '..', '.tmp', 'marketing', 'agent.log')
os.makedirs(os.path.dirname(log_path), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_path, mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ── APScheduler ───────────────────────────────────────────────────────────────
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron      import CronTrigger

from agent_generator import run_generation_cycle, run_tiktok_video_for_trade
from agent_reflector import run_reflection
from agent_db        import init_db

def main():
    log.info("=" * 60)
    log.info("VisionEdge Autonomous Marketing Agent — DAEMON MODE")
    log.info("=" * 60)
    init_db()

    # Load secrets once
    import json
    secrets_path = os.path.join(THIS_DIR, '..', '..', 'secrets.json')
    try:
        with open(secrets_path, 'r') as f:
            secrets = json.load(f)
    except Exception as e:
        log.error(f"Failed to load secrets: {e}")
        secrets = {}

    from whale_data_fetcher import fetch_recent_whale_trades, pick_best_trade

    log.info("Polling database every 60 seconds for new Whale Trades...")
    
    while True:
        try:
            trades = fetch_recent_whale_trades(limit=5)
            best_trade = pick_best_trade(trades)
            
            if best_trade:
                log.info(f"🚨 ACTIVE WHALE DETECTED: ${best_trade['usd_value']:,.0f} on {best_trade['market_title']}")
                log.info("Engaging the Native Generation Pipeline...")
                
                # Execute the video generation pipeline specifically locking onto this trade
                run_tiktok_video_for_trade(best_trade, secrets)
                
            else:
                pass # Silent ping, no new unprocessed trades found.

        except Exception as e:
            log.error(f"Error during polling cycle: {e}")

        # Sleep for 60 seconds before checking the database again
        time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent daemon stopped.")
