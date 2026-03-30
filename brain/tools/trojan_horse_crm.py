#!/usr/bin/env python3
"""
trojan_horse_crm.py — PolyVision Layer 3 Tool
Automates the "Trojan Horse" Discord Marketing Outreach Strategy.
Sends an email to the administrator containing a pre-written DM script and target community.
Tracks sent communities in PostgreSQL to avoid duplicates.
"""

import os
import random
import logging
import requests
from datetime import datetime
import psycopg2

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM    = os.getenv("RESEND_FROM", "onboarding@resend.dev")
# Default to sending the CRM reminders to the same email as the briefing
TARGET_EMAIL = os.getenv("BRIEFING_EMAIL_TO", "adair.clark@gmail.com")

def init_crm_db():
    """Create the trojan tracking table if it doesn't exist."""
    if not DATABASE_URL:
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trojan_crm_state (
                        community_name TEXT PRIMARY KEY,
                        category TEXT,
                        sent_at TIMESTAMPTZ DEFAULT NOW()
                    );
                """)
            conn.commit()
        log.info("[TrojanCRM] DB initialized.")
    except Exception as e:
        log.warning(f"[TrojanCRM] DB init failed: {e}")

def get_sent_communities() -> set:
    if not DATABASE_URL:
        return set()
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT community_name FROM trojan_crm_state")
                return {row[0] for row in cur.fetchall()}
    except Exception:
        return set()

def mark_community_sent(name: str, category: str):
    if not DATABASE_URL:
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO trojan_crm_state (community_name, category)
                    VALUES (%s, %s) ON CONFLICT DO NOTHING
                """, (name, category))
            conn.commit()
    except Exception as e:
        log.error(f"[TrojanCRM] Failed to mark {name} as sent: {e}")

TARGET_COMMUNITIES = [
    # Group 1: Crypto & Web3 Trading Servers
    {
        "name": "WallStreetBets Discord",
        "category": "Crypto/DeFi",
        "script": "Hey [Moderator], I build data infrastructure for prediction markets (identifying when massive wallets drop $100k+ on specific outcomes). I'm testing out a new live-feed bot and I'm looking for a few high-quality crypto servers to run it in for free to gather feedback. It basically auto-posts whenever a whale makes a massive move. Happy to drop a webhook into a test channel if you want to see if your community likes the alpha."
    },
    {
        "name": "Crypto Twitter Alpha Groups (Search Twitter for private discords)",
        "category": "Crypto/DeFi",
        "script": "Hey man, I build data infrastructure for prediction markets (identifying when massive wallets drop $100k+ on specific outcomes). I'm testing out a new live-feed bot and I'm looking for a few high-quality crypto servers to run it in for free to gather feedback. It basically auto-posts whenever a whale makes a massive move. Happy to drop a webhook into a test channel if you want to see if your community likes the alpha."
    },
    {
        "name": "DeFi Llama / Major Protocol Discords",
        "category": "Crypto/DeFi",
        "script": "Hey [Moderator], I build data infrastructure for prediction markets (identifying when massive wallets drop $100k+ on Polymarket). I'm testing out a new live-feed bot and I'm looking for a few high-quality servers to run it in for free to gather feedback. It basically auto-posts whenever a whale makes a massive move. Happy to drop a webhook into a test channel if you want to see if your community likes the alpha."
    },
    # Group 2: Sports Betting / DeGen Communities
    {
        "name": "PrizePicks / DFS Discords",
        "category": "Sports/Arbitrage",
        "script": "Hey [Moderator], love the community design. I run a tracker that flags when syndicate wallets place 6-figure bets on Kalshi/Polymarket. Some of our community members cross over with sports betting, so I wanted to see if you'd be interested in a free live feed of these alerts for your server? Gives your guys something crazy to follow during the weekdays when sports cards are light."
    },
    {
        "name": "Makers Discord / EV Betting",
        "category": "Sports/Arbitrage",
        "script": "Hey [Moderator], love the community design. I run a tracker that flags when syndicate wallets place 6-figure bets on Kalshi/Polymarket. Some of our community members cross over with sports betting, so I wanted to see if you'd be interested in a free live feed of these alerts for your server? Gives your guys something crazy to follow during the weekdays when sports cards are light."
    },
    {
        "name": "OddsJam or Promo-Abuse Discords",
        "category": "Sports/Arbitrage",
        "script": "Hey [Moderator], I run a tracker that flags when syndicate wallets place 6-figure bets on Kalshi/Polymarket. Some of our community members cross over with sports betting, so I wanted to see if you'd be interested in a free live feed of these alerts for your server? Gives your guys something crazy to follow during the weekdays when sports cards are light."
    }
]

def send_crm_email(target: dict):
    if not RESEND_API_KEY:
        log.warning("[TrojanCRM] No RESEND_API_KEY found. Skipping CRM email.")
        return

    html_content = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; color: #111827;">
        <h2 style="color: #8b5cf6;">🐎 Trojan Horse CRM Reminder</h2>
        <p>It's time to reach out to a new potential Discord partner to expand PolyVision's organic reach.</p>
        
        <div style="background: #f3f4f6; padding: 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 0; color: #4b5563; font-size: 14px; text-transform: uppercase;">Community Profile</p>
            <p style="font-size: 18px; font-weight: bold; margin: 5px 0;">{target['name']}</p>
            <p style="margin: 0; color: #6b7280; font-size: 14px;">Category: {target['category']}</p>
        </div>

        <p><strong>Your Copy/Paste Pitch:</strong></p>
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 15px; border-radius: 8px; font-size: 15px; line-height: 1.5; color: #334155;">
            {target['script']}
        </div>

        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;" />
        <p style="font-size: 13px; color: #6b7280;">
            <strong>Next Steps:</strong> Find the server, locate an active moderator, and send the DM. If they say yes, ask them to create a Discord Webhook in their server. Then paste their webhook URL into your Railway variables as <code>DISCORD_WEBHOOK_PARTNER_X_URL</code> and they will silently receive PolyVision alerts!
        </p>
    </div>
    """

    data = {
        "from":    RESEND_FROM,
        "to":      [TARGET_EMAIL],
        "subject": f"CRM Action Required: Prospecting {target['name']}",
        "html":    html_content
    }

    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json=data,
            timeout=10
        )
        r.raise_for_status()
        log.info(f"[TrojanCRM] Email dispatched for {target['name']}")
    except Exception as e:
        log.error(f"[TrojanCRM] Failed to send email: {e}")

def run_crm_pass():
    """Triggered by the top-level main.py APScheduler twice a week."""
    log.info("[TrojanCRM] Running marketing CRM pass...")
    init_crm_db()
    
    sent_names = get_sent_communities()
    available_targets = [t for t in TARGET_COMMUNITIES if t['name'] not in sent_names]
    
    if not available_targets:
        log.info("[TrojanCRM] Master list exhausted! Time to draft out more communities.")
        return
        
    target = random.choice(available_targets)
    send_crm_email(target)
    mark_community_sent(target['name'], target['category'])
    log.info(f"[TrojanCRM] CRM pass completed: Recommending {target['name']}")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_crm_pass()
