#!/usr/bin/env python3
from __future__ import annotations
"""
metrics_fetcher.py — Phase 2 RL Telemetry Harvester
Queries Twitter/X API v2 for real impression and like counts on
previously published tweets, then writes those metrics back to
the `video_history` table so the Epsilon-Greedy algorithm in
`agent_generator.py` can make data-driven decisions.

This script is called automatically by `agent_reflector.py` every
midnight BEFORE the Claude reflection cycle runs, so the reflector
always has fresh data to reason about.

Data flow:
  1. Read `video_history` rows WHERE post_id IS NOT NULL AND impressions = 0
  2. For each row, call Twitter API v2: GET /2/tweets/:id?tweet.fields=public_metrics
  3. Write impression_count → video_history.impressions
  4. Write like_count       → video_history.upvotes
  5. Epsilon-Greedy in agent_generator.py reads these on next run
"""
import os
import json
import logging
import psycopg2

log = logging.getLogger(__name__)

SECRETS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'secrets.json')
PKGS_DIR     = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'pkgs')


def _get_secrets() -> dict:
    try:
        with open(SECRETS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def run_telemetry():
    """
    Full telemetry harvest cycle.
    Reads video_history rows with a post_id but no impressions,
    fetches real metrics from the Twitter API, and writes them back.
    """
    secrets = _get_secrets()
    db_url  = secrets.get('DATABASE_URL', os.getenv('DATABASE_URL', ''))

    if not db_url:
        log.error("[Telemetry] No DATABASE_URL configured.")
        return

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # ── Build Tweepy client from secrets.json keys ─────────────────────────────
    twitter_client = None
    try:
        import sys
        if PKGS_DIR not in sys.path:
            sys.path.insert(0, PKGS_DIR)
        import tweepy

        twitter_client = tweepy.Client(
            consumer_key        = secrets.get('TWITTER_API_KEY', os.getenv('TWITTER_API_KEY', '')),
            consumer_secret     = secrets.get('TWITTER_API_SECRET', os.getenv('TWITTER_API_SECRET', os.getenv('TWITTER_API_KEY_SECRET', ''))),
            access_token        = secrets.get('TWITTER_ACCESS_TOKEN', os.getenv('TWITTER_ACCESS_TOKEN', '')),
            access_token_secret = secrets.get('TWITTER_ACCESS_SECRET', os.getenv('TWITTER_ACCESS_SECRET', os.getenv('TWITTER_ACCESS_TOKEN_SECRET', ''))),
            wait_on_rate_limit  = False,
        )
        log.info("[Telemetry] Tweepy client authenticated.")
    except Exception as e:
        log.error(f"[Telemetry] Tweepy init failed: {e} — cannot harvest impressions this cycle.")
        return   # Do NOT fall back to fake data. Abort cleanly.

    # ── Query video_history for rows that need telemetry ──────────────────────
    try:
        conn = psycopg2.connect(db_url)
        cur  = conn.cursor()

        # Only process tweets that are at least 2 hours old (Twitter needs time to index)
        cur.execute("""
            SELECT id, post_id, theme
            FROM video_history
            WHERE post_id IS NOT NULL
              AND (impressions IS NULL OR impressions = 0)
              AND created_at < NOW() - INTERVAL '2 hours'
            ORDER BY created_at DESC
            LIMIT 10;
        """)
        rows = cur.fetchall()

        if not rows:
            log.info("[Telemetry] No pending rows in video_history — all telemetry up to date.")
            cur.close()
            conn.close()
            return

        log.info(f"[Telemetry] Harvesting metrics for {len(rows)} video_history rows.")
        updated = 0

        for (db_id, post_id, theme) in rows:
            try:
                tweet = twitter_client.get_tweet(
                    post_id,
                    tweet_fields=["public_metrics"]
                )
                if not tweet.data or not tweet.data.public_metrics:
                    log.warning(f"[Telemetry] No metrics returned for tweet {post_id}.")
                    continue

                metrics     = tweet.data.public_metrics
                impressions = metrics.get("impression_count", 0)
                likes       = metrics.get("like_count", 0)
                retweets    = metrics.get("retweet_count", 0)

                # engagement_score: combined quality signal (likes + retweets / impressions)
                engagement = round((likes + retweets) / max(impressions, 1), 6)

                cur.execute(
                    """
                    UPDATE video_history
                    SET impressions = %s,
                        upvotes     = %s
                    WHERE id = %s
                    """,
                    (impressions, likes, db_id)
                )
                updated += 1
                log.info(
                    f"[Telemetry] ✅  tweet={post_id} | theme='{theme}' | "
                    f"impressions={impressions:,} | likes={likes} | "
                    f"retweets={retweets} | engagement_score={engagement:.4f}"
                )

            except Exception as e:
                log.warning(f"[Telemetry] Failed for tweet {post_id}: {e}")
                continue

        conn.commit()
        cur.close()
        conn.close()
        log.info(f"[Telemetry] Harvest complete — {updated}/{len(rows)} rows updated.")

    except Exception as e:
        log.error(f"[Telemetry] Database error: {e}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [metrics_fetcher] %(levelname)s: %(message)s",
    )
    run_telemetry()
