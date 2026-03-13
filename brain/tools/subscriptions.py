"""
subscriptions.py — PolyVision Stripe subscription management.

Handles:
  - DB schema for subscriptions table (Clerk user ID → Stripe customer/subscription)
  - is_pro(clerk_user_id) → bool  (fast check, used by every API endpoint)
  - upsert_subscription(data)     (called by Stripe webhook handler)
  - get_subscription(clerk_user_id) → dict
"""

import os
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

log = logging.getLogger(__name__)
DATABASE_URL = os.getenv('DATABASE_URL', '')


def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=5)


# ── Schema ────────────────────────────────────────────────────────────────────
def init_db():
    """Create subscriptions table if it doesn't exist (idempotent)."""
    if not DATABASE_URL:
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id                  SERIAL PRIMARY KEY,
                    clerk_user_id       TEXT NOT NULL UNIQUE,
                    stripe_customer_id  TEXT,
                    stripe_sub_id       TEXT,
                    status              TEXT DEFAULT 'inactive',
                    plan                TEXT DEFAULT 'free',
                    current_period_end  TIMESTAMPTZ,
                    created_at          TIMESTAMPTZ DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_subscriptions_clerk
                    ON subscriptions(clerk_user_id);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_customer
                    ON subscriptions(stripe_customer_id);
            """)
        conn.commit()
        log.info('Subscriptions table ready.')
    finally:
        conn.close()


# ── Core Logic ────────────────────────────────────────────────────────────────
def is_pro(clerk_user_id: str) -> bool:
    """Return True if user has an active subscription that hasn't expired."""
    if not DATABASE_URL or not clerk_user_id:
        return False
    try:
        conn = _connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT status, current_period_end
                FROM subscriptions
                WHERE clerk_user_id = %s
                LIMIT 1
            """, (clerk_user_id,))
            row = cur.fetchone()
        conn.close()
        if not row:
            return False
        if row['status'] != 'active':
            return False
        # Check expiry
        exp = row['current_period_end']
        if exp and exp < datetime.now(timezone.utc):
            return False
        return True
    except Exception as e:
        log.warning(f'is_pro check failed: {e}')
        return False


def get_subscription(clerk_user_id: str) -> dict:
    """Return full subscription row for a user, or defaults if not found."""
    defaults = {
        'is_pro': False,
        'plan': 'free',
        'status': 'inactive',
        'current_period_end': None,
    }
    if not DATABASE_URL or not clerk_user_id:
        return defaults
    try:
        conn = _connect()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT status, plan, current_period_end, stripe_customer_id
                FROM subscriptions
                WHERE clerk_user_id = %s
                LIMIT 1
            """, (clerk_user_id,))
            row = cur.fetchone()
        conn.close()
        if not row:
            return defaults
        exp            = row['current_period_end']
        active         = row['status'] == 'active' and (not exp or exp > datetime.now(timezone.utc))
        return {
            'is_pro':               active,
            'plan':                 row['plan'],
            'status':               row['status'],
            'current_period_end':   exp.isoformat() if exp else None,
        }
    except Exception as e:
        log.warning(f'get_subscription failed: {e}')
        return defaults


def upsert_subscription(clerk_user_id: str, stripe_customer_id: str,
                        stripe_sub_id: str, status: str,
                        period_end_ts: int | None) -> None:
    """Create or update a subscription record from a Stripe webhook event."""
    if not DATABASE_URL:
        return
    period_end = (datetime.fromtimestamp(period_end_ts, tz=timezone.utc)
                  if period_end_ts else None)
    plan = 'pro' if status == 'active' else 'free'
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO subscriptions
                    (clerk_user_id, stripe_customer_id, stripe_sub_id, status, plan, current_period_end, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (clerk_user_id) DO UPDATE SET
                    stripe_customer_id  = EXCLUDED.stripe_customer_id,
                    stripe_sub_id       = EXCLUDED.stripe_sub_id,
                    status              = EXCLUDED.status,
                    plan                = EXCLUDED.plan,
                    current_period_end  = EXCLUDED.current_period_end,
                    updated_at          = NOW()
            """, (clerk_user_id, stripe_customer_id, stripe_sub_id,
                  status, plan, period_end))
        conn.commit()
        conn.close()
        log.info(f'Subscription upserted: {clerk_user_id} → {status}')
    except Exception as e:
        log.error(f'upsert_subscription failed: {e}')


def cancel_subscription(stripe_customer_id: str) -> None:
    """Mark a subscription as cancelled by Stripe customer ID."""
    if not DATABASE_URL:
        return
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE subscriptions
                SET status = 'cancelled', plan = 'free', updated_at = NOW()
                WHERE stripe_customer_id = %s
            """, (stripe_customer_id,))
        conn.commit()
        conn.close()
        log.info(f'Subscription cancelled for customer: {stripe_customer_id}')
    except Exception as e:
        log.error(f'cancel_subscription failed: {e}')
