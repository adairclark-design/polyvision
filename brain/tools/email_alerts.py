#!/usr/bin/env python3
"""
email_alerts.py — PolyVision Layer 3 Tool
Stores per-user alert rules in PostgreSQL and fires Resend emails
when an incoming whale trade matches a saved rule.

Rules are stored with the user's Clerk user_id and email address.
The backend checks every trade event against all stored rules.

Usage:
    python tools/email_alerts.py --init-db      # create tables
    python tools/email_alerts.py --test         # send a test email
"""

import os
import sys
import json
import logging
import argparse
import requests
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "PolyVision Alerts <alerts@polyvision.app>")
APP_URL = os.getenv("APP_URL", "https://polyvision.pages.dev")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [email_alerts] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)


# ── Database ───────────────────────────────────────────────────────────────────
def get_conn():
    """Lazy-import psycopg2 so the script works without a local DB install."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise RuntimeError("psycopg2 not installed locally — run: pip3 install psycopg2-binary")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    """Create the alert_rules table if it doesn't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS alert_rules (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    email       TEXT NOT NULL,
                    min_size    FLOAT NOT NULL DEFAULT 10000,
                    side        TEXT NOT NULL DEFAULT 'both',
                    keyword     TEXT DEFAULT '',
                    wallet      TEXT DEFAULT '',
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_alert_rules_user ON alert_rules(user_id);
            """)
            conn.commit()
    log.info("alert_rules table ready.")


# ── Rule CRUD ──────────────────────────────────────────────────────────────────
def get_rules(user_id: str) -> list[dict]:
    """Return all alert rules for a given Clerk user_id."""
    if not DATABASE_URL:
        return []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM alert_rules WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,)
            )
            return [dict(r) for r in cur.fetchall()]


def save_rule(rule: dict) -> dict:
    """
    Insert a new alert rule. rule must contain:
      id, user_id, email, min_size, side, keyword (opt), wallet (opt)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alert_rules (id, user_id, email, min_size, side, keyword, wallet)
                VALUES (%(id)s, %(user_id)s, %(email)s, %(min_size)s, %(side)s, %(keyword)s, %(wallet)s)
                ON CONFLICT (id) DO NOTHING
                RETURNING *
            """, {
                "id":       rule["id"],
                "user_id":  rule["user_id"],
                "email":    rule["email"],
                "min_size": float(rule.get("min_size", 10000)),
                "side":     rule.get("side", "both"),
                "keyword":  (rule.get("keyword") or "").lower().strip(),
                "wallet":   (rule.get("wallet") or "").lower().strip(),
            })
            result = cur.fetchone()
            conn.commit()
    return dict(result) if result else {}


def delete_rule(rule_id: str, user_id: str) -> bool:
    """Delete a rule by ID — only if it belongs to the requesting user."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM alert_rules WHERE id = %s AND user_id = %s",
                (rule_id, user_id)
            )
            conn.commit()
            return cur.rowcount > 0


def get_all_rules() -> list[dict]:
    """Return ALL alert rules across all users (used by the pipeline)."""
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM alert_rules")
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.warning(f"Could not fetch alert rules: {e}")
        return []


# ── Rule Matching ──────────────────────────────────────────────────────────────
def rule_matches(rule: dict, alert: dict) -> bool:
    """
    Returns True if the whale alert matches an alert rule.
    alert keys used: usd_value, outcome, market_title, wallet_address
    """
    usd     = float(alert.get("usd_value", 0))
    outcome = str(alert.get("outcome", "")).upper()
    market  = str(alert.get("market_title", "")).lower()
    wallet  = str(alert.get("wallet_address", "")).lower()

    # Size threshold
    if usd < float(rule.get("min_size", 0)):
        return False

    # Side filter
    side = str(rule.get("side", "both")).upper()
    if side not in ("BOTH", "") and side != outcome:
        return False

    # Keyword filter (must be in market title)
    keyword = str(rule.get("keyword", "")).strip().lower()
    if keyword and keyword not in market:
        return False

    # Specific wallet filter
    rule_wallet = str(rule.get("wallet", "")).strip().lower()
    if rule_wallet and rule_wallet not in wallet:
        return False

    return True


# ── Email Delivery ─────────────────────────────────────────────────────────────
def build_email_html(alert: dict, rule: dict) -> str:
    """Build a clean HTML email for a matched alert."""
    handle  = alert.get("trader_handle", "A whale")
    market  = alert.get("market_title", "an undisclosed market")
    outcome = alert.get("outcome", "")
    usd     = float(alert.get("usd_value", 0))
    price   = float(alert.get("price", 0))
    tier    = alert.get("alert_tier", "STANDARD")
    summary = alert.get("ai_summary", "")
    wallet  = alert.get("wallet_address", "")
    pct     = int(min(99, max(1, price * 100)))

    outcome_color = "#00ffa3" if outcome.upper() == "YES" else "#ff4d6d"
    tier_emoji = "🐋" if tier == "WHALE" else "🔵"

    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PolyVision Alert</title>
</head>
<body style="margin:0;padding:0;background:#0d1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:520px;margin:40px auto;background:#161b22;border-radius:16px;overflow:hidden;border:1px solid #30363d;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#00ffa3 0%,#0057ff 100%);padding:24px 28px;">
      <div style="font-size:22px;font-weight:800;color:#0d1117;">🐋 PolyVision</div>
      <div style="font-size:13px;color:#0d1117;opacity:.8;margin-top:2px;">Custom Alert Triggered</div>
    </div>

    <!-- Body -->
    <div style="padding:24px 28px;">
      <div style="font-size:20px;font-weight:700;color:#e6edf3;margin-bottom:4px;">
        {tier_emoji} {handle}
      </div>
      <div style="font-size:28px;font-weight:800;color:{outcome_color};margin:8px 0;">
        {outcome} on "{market}"
      </div>

      <!-- Stats row -->
      <div style="display:flex;gap:12px;margin:20px 0;flex-wrap:wrap;">
        <div style="background:#0d1117;border-radius:10px;padding:12px 16px;flex:1;min-width:90px;text-align:center;">
          <div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px;">Size</div>
          <div style="font-size:18px;font-weight:700;color:{outcome_color};">${usd:,.0f}</div>
        </div>
        <div style="background:#0d1117;border-radius:10px;padding:12px 16px;flex:1;min-width:90px;text-align:center;">
          <div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px;">Price</div>
          <div style="font-size:18px;font-weight:700;color:#e6edf3;">{pct}¢</div>
        </div>
        <div style="background:#0d1117;border-radius:10px;padding:12px 16px;flex:1;min-width:90px;text-align:center;">
          <div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px;">Tier</div>
          <div style="font-size:18px;font-weight:700;color:#e6edf3;">{tier}</div>
        </div>
      </div>

      <!-- AI Summary -->
      {"" if not summary else f'<div style="background:#0d1117;border-left:3px solid #00ffa3;border-radius:0 8px 8px 0;padding:12px 16px;margin:16px 0;font-size:13px;color:#8b949e;line-height:1.6;">{summary}</div>'}

      <!-- CTA -->
      <a href="{APP_URL}" style="display:block;background:linear-gradient(90deg,#00ffa3,#0057ff);color:#0d1117;text-decoration:none;text-align:center;font-weight:800;font-size:14px;padding:14px;border-radius:10px;margin-top:20px;">
        🐋 View Live Dashboard →
      </a>

      <!-- Wallet link -->
      {"" if not wallet else f'<div style="text-align:center;margin-top:10px;"><a href="https://polymarket.com/profile/{wallet}" style="color:#8b949e;font-size:11px;">View on Polymarket: {wallet[:10]}…{wallet[-4:]}</a></div>'}
    </div>

    <!-- Footer -->
    <div style="padding:16px 28px;border-top:1px solid #30363d;font-size:11px;color:#8b949e;text-align:center;">
      ⚠️ Whales can hedge. This is not financial advice. Trade at your own risk.<br>
      <a href="{APP_URL}/unsubscribe" style="color:#8b949e;">Manage alert preferences</a>
    </div>

  </div>
</body>
</html>
"""


def send_email_alert(to_email: str, alert: dict, rule: dict) -> bool:
    """
    Send a single whale alert email via Resend API.
    Returns True on success, False on failure.
    """
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping email alert")
        return False

    handle  = alert.get("trader_handle", "A whale")
    market  = alert.get("market_title", "")
    usd     = float(alert.get("usd_value", 0))
    outcome = alert.get("outcome", "")

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "from":    RESEND_FROM,
                "to":      [to_email],
                "subject": f"🐋 {handle} just bet ${usd:,.0f} {outcome} on \"{market[:40]}\"",
                "html":    build_email_html(alert, rule),
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"✅ Email sent to {to_email} for rule {rule['id']}")
        return True
    except Exception as e:
        log.error(f"❌ Email send failed to {to_email}: {e}")
        return False


# ── Pipeline Integration ───────────────────────────────────────────────────────
def check_and_fire_email_alerts(alert: dict):
    """
    Called from run_pipeline() for every whale alert.
    Loads all stored rules, finds matches, sends emails.
    Each matched rule fires at most once per alert (deduped by alert_id + rule_id).
    """
    if not DATABASE_URL or not RESEND_API_KEY:
        return  # silent no-op if not configured

    rules = get_all_rules()
    if not rules:
        return

    alert_id = alert.get("alert_id", "")

    for rule in rules:
        if not rule_matches(rule, alert):
            continue

        to_email = rule.get("email", "")
        if not to_email:
            continue

        # Dedupe: don't send the same alert to the same rule twice
        # (handles retried pipeline runs)
        dedup_key = f"email:sent:{alert_id}:{rule['id']}"
        try:
            import redis as redis_lib
            r = redis_lib.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                    decode_responses=True, socket_timeout=3)
            if r.get(dedup_key):
                log.info(f"Deduped email alert: {dedup_key}")
                continue
            r.setex(dedup_key, 86400, "1")  # 24h dedup window
        except Exception:
            pass  # if Redis is unavailable, still send

        send_email_alert(to_email, alert, rule)


# ── CLI Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Email Alerts Tool")
    parser.add_argument("--init-db", action="store_true", help="Initialize DB tables and exit")
    parser.add_argument("--test",    action="store_true", help="Send a test email (requires --to)")
    parser.add_argument("--to",      type=str,            help="Email address for test send")
    args = parser.parse_args()

    if args.init_db:
        init_db()
        sys.exit(0)

    if args.test:
        if not args.to:
            print("ERROR: --to <email> required for --test")
            sys.exit(1)
        test_alert = {
            "alert_id":       "test-001",
            "alert_tier":     "WHALE",
            "trader_handle":  "The Oracle of Oregon",
            "wallet_address": "0xDeAdBeEf1234567890abcdef",
            "market_title":   "Will the Fed cut rates in March 2026?",
            "outcome":        "YES",
            "price":          0.72,
            "usd_value":      50000.0,
            "ai_summary":     "The Oracle of Oregon has deployed $50,000 into the YES side at 72¢.",
        }
        test_rule = {"id": "test-rule", "min_size": 1000, "side": "both", "keyword": "", "wallet": ""}
        result = send_email_alert(args.to, test_alert, test_rule)
        print("✅ Email sent successfully!" if result else "❌ Email send failed.")
        sys.exit(0 if result else 1)

    print("Use --init-db or --test --to user@example.com")
