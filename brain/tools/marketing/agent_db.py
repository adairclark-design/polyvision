#!/usr/bin/env python3
from __future__ import annotations
"""
agent_db.py — VisionEdge Marketing Agent | Layer 3: Database
Initializes and manages the agent_marketing_campaigns table.
Logs every generated campaign with metadata so the reflector
can analyze performance telemetry.
"""
import os
import json
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone

log = logging.getLogger(__name__)

def _load_secrets():
    """Load secrets from secrets.json (project standard)."""
    secrets_path = os.path.join(os.path.dirname(__file__), '..', '..', 'secrets.json')
    try:
        with open(secrets_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Could not load secrets.json: {e}")
        return {}

SECRETS = _load_secrets()
DATABASE_URL = SECRETS.get("DATABASE_URL", os.getenv("DATABASE_URL", ""))


def get_conn():
    """Return a new psycopg2 connection."""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create the agent_marketing_campaigns table if it doesn't exist."""
    if not DATABASE_URL:
        log.warning("agent_db: No DATABASE_URL configured — skipping DB init.")
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_marketing_campaigns (
                        id                  SERIAL PRIMARY KEY,
                        platform            TEXT        NOT NULL,
                        ticker              TEXT        DEFAULT '',
                        strategy_attempt    TEXT        DEFAULT '',
                        content             TEXT        NOT NULL,
                        video_url           TEXT        DEFAULT '',
                        chart_image_url     TEXT        DEFAULT '',
                        impression_count    INTEGER     DEFAULT 0,
                        engagement_score    FLOAT       DEFAULT 0.0,
                        telemetry_pulled    BOOLEAN     DEFAULT FALSE,
                        posted_at           TIMESTAMPTZ DEFAULT NOW(),
                        created_at          TIMESTAMPTZ DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_amc_platform
                        ON agent_marketing_campaigns(platform);
                    CREATE INDEX IF NOT EXISTS idx_amc_telemetry
                        ON agent_marketing_campaigns(telemetry_pulled, posted_at);
                """)
            conn.commit()
        log.info("agent_marketing_campaigns table ready.")

        # ── video_history: ensure RL-critical columns exist ──────────────────
        # Open a FRESH connection — the outer `with get_conn()` block closed its
        # connection on __exit__. Re-using `conn` there would silently fail.
        with get_conn() as conn2:
            with conn2.cursor() as cur2:
                cur2.execute("""
                    DO $$
                    BEGIN
                        -- post_id: stores the platform post ID after auto-publishing
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='video_history' AND column_name='post_id'
                        ) THEN
                            ALTER TABLE video_history ADD COLUMN post_id TEXT DEFAULT NULL;
                        END IF;

                        -- impressions: populated nightly by metrics_fetcher
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='video_history' AND column_name='impressions'
                        ) THEN
                            ALTER TABLE video_history ADD COLUMN impressions INTEGER DEFAULT 0;
                        END IF;

                        -- upvotes: likes — used by Epsilon-Greedy as quality signal
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='video_history' AND column_name='upvotes'
                        ) THEN
                            ALTER TABLE video_history ADD COLUMN upvotes INTEGER DEFAULT 0;
                        END IF;

                        -- mode: fresh|cluster|recap — the story type the RL system controls
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='video_history' AND column_name='mode'
                        ) THEN
                            ALTER TABLE video_history ADD COLUMN mode TEXT DEFAULT 'fresh';
                        END IF;

                        -- market_category: crypto|political|sports|default
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name='video_history' AND column_name='market_category'
                        ) THEN
                            ALTER TABLE video_history ADD COLUMN market_category TEXT DEFAULT 'default';
                        END IF;
                    END $$;
                """)
            conn2.commit()
        log.info("video_history RL columns verified (post_id, impressions, upvotes).")
    except Exception as e:
        log.warning(f"agent_db init failed: {e}")



def log_campaign(
    platform: str,
    ticker: str,
    strategy: str,
    content: str,
    video_url: str = "",
    chart_image_url: str = ""
):
    """Insert a new campaign row and return its ID."""
    if not DATABASE_URL:
        log.info(f"[DRY-RUN] Campaign logged — {platform} | {ticker} | {content[:80]}")
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO agent_marketing_campaigns
                        (platform, ticker, strategy_attempt, content, video_url, chart_image_url)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id;
                """, (platform, ticker, strategy, content, video_url, chart_image_url))
                row = cur.fetchone()
            conn.commit()
        campaign_id = row[0] if row else None
        log.info(f"Campaign #{campaign_id} logged — {platform}/{ticker}")
        return campaign_id
    except Exception as e:
        log.error(f"log_campaign failed: {e}")
        return None


def get_pending_telemetry(hours: int = 24) -> list[dict]:
    """Return campaigns older than {hours}h that haven't had telemetry pulled."""
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("""
                    SELECT id, platform, ticker, content, video_url
                    FROM agent_marketing_campaigns
                    WHERE telemetry_pulled = FALSE
                      AND posted_at < NOW() - INTERVAL '%s hours'
                    ORDER BY posted_at DESC
                    LIMIT 20;
                """, (hours,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.error(f"get_pending_telemetry failed: {e}")
        return []


def update_telemetry(campaign_id: int, impressions: int, engagement: float):
    """Write fetched telemetry back to the campaign row."""
    if not DATABASE_URL:
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE agent_marketing_campaigns
                    SET impression_count = %s,
                        engagement_score = %s,
                        telemetry_pulled = TRUE
                    WHERE id = %s;
                """, (impressions, engagement, campaign_id))
            conn.commit()
        log.info(f"Telemetry updated for campaign #{campaign_id}: {impressions} impressions")
    except Exception as e:
        log.error(f"update_telemetry failed: {e}")


def get_recent_campaign_data(limit: int = 10) -> list[dict]:
    """Return the last {limit} completed (telemetry pulled) campaigns for reflection."""
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("""
                    SELECT platform, ticker, strategy_attempt, content,
                           impression_count, engagement_score, posted_at
                    FROM agent_marketing_campaigns
                    WHERE telemetry_pulled = TRUE
                    ORDER BY posted_at DESC
                    LIMIT %s;
                """, (limit,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.error(f"get_recent_campaign_data failed: {e}")
        return []
