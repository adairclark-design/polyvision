#!/usr/bin/env python3
"""
market_resolver.py — PolyVision Layer 3 Tool
Daily cron job that:
  1. Finds all unresolved trades in the DB
  2. Checks each unique market_id against Polymarket's Gamma API
  3. Marks trades as won/lost when a market is resolved
  4. Recalculates win_rate for every affected wallet

Architecture: Called daily by APScheduler in main.py lifespan.
Also callable manually:
    python tools/market_resolver.py --run     # run the full resolution pass
    python tools/market_resolver.py --stats   # show current win rate stats
"""

import os
import sys
import json
import logging
import argparse
import time
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [resolver] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com/markets"


# ── Database ───────────────────────────────────────────────────────────────────
def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    """Ensure resolution columns exist (idempotent — safe to re-run)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Add columns to trades if they don't already exist
            cur.execute("""
                ALTER TABLE trades
                    ADD COLUMN IF NOT EXISTS resolved   BOOLEAN DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS won        BOOLEAN;
            """)
            # Add winning_trades to wallets if missing
            cur.execute("""
                ALTER TABLE wallets
                    ADD COLUMN IF NOT EXISTS winning_trades INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS losing_trades  INTEGER DEFAULT 0;
            """)
            # Market resolutions cache table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS market_resolutions (
                    market_id        TEXT PRIMARY KEY,
                    resolved         BOOLEAN DEFAULT FALSE,
                    winning_outcome  TEXT,
                    checked_at       TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_market_res ON market_resolutions(resolved);
            """)
            conn.commit()
    log.info("Resolution schema ready.")


# ── Polymarket API ─────────────────────────────────────────────────────────────
def fetch_market_resolution(market_id: str) -> dict | None:
    """
    Query Gamma API for a market's resolution status.
    Returns dict with keys: resolved (bool), winning_outcome (str|None).
    Returns None on API error.
    """
    try:
        # Gamma API accepts conditionId as a query param
        resp = requests.get(
            GAMMA_API,
            params={"conditionId": market_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # API may return a list or a single object
        markets = data if isinstance(data, list) else [data]
        if not markets:
            return None

        market = markets[0]
        resolved = bool(market.get("resolved") or market.get("closed"))
        # winnerOutcome is typically "Yes" or "No" (capital Y/N)
        winner = market.get("winnerOutcome") or market.get("resolvedOutcome")
        # Normalize to uppercase for comparison with our "YES"/"NO" outcome field
        if winner:
            winner = winner.strip().upper()

        return {
            "resolved":        resolved,
            "winning_outcome": winner if resolved else None,
        }
    except Exception as e:
        log.warning(f"API error for market {market_id[:20]}: {e}")
        return None


# ── Core Resolution Logic ──────────────────────────────────────────────────────
def resolve_pending_trades() -> dict:
    """
    Main resolution pass:
      1. Finds all unresolved trades
      2. Batches unique market_ids
      3. Checks resolution, caches result
      4. Updates trades.resolved + trades.won
      5. Recalculates wallet win rates

    Returns a summary dict with stats.
    """
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set — skipping resolution pass")
        return {"skipped": True}

    summary = {
        "markets_checked": 0,
        "markets_resolved": 0,
        "trades_updated": 0,
        "wallets_updated": 0,
        "errors": 0,
    }

    try:
        conn = get_conn()

        with conn.cursor() as cur:
            # Get all unique market_ids that have unresolved trades
            cur.execute("""
                SELECT DISTINCT t.market_id
                FROM trades t
                WHERE t.resolved = FALSE
                  AND t.market_id IS NOT NULL
                  AND t.market_id != ''
            """)
            pending_markets = [r["market_id"] for r in cur.fetchall()]

        log.info(f"Checking {len(pending_markets)} unresolved markets…")

        for market_id in pending_markets:
            summary["markets_checked"] += 1

            # Small rate limit buffer — Gamma API is public but be polite
            time.sleep(0.2)

            result = fetch_market_resolution(market_id)
            if result is None:
                summary["errors"] += 1
                continue

            # Cache the resolution result
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO market_resolutions (market_id, resolved, winning_outcome, checked_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (market_id) DO UPDATE
                        SET resolved        = EXCLUDED.resolved,
                            winning_outcome = EXCLUDED.winning_outcome,
                            checked_at      = NOW()
                """, (market_id, result["resolved"], result["winning_outcome"]))
                conn.commit()

            if not result["resolved"] or not result["winning_outcome"]:
                continue  # still active

            summary["markets_resolved"] += 1
            winner = result["winning_outcome"]  # "YES" or "NO"

            # Update all trades on this market
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE trades
                    SET resolved = TRUE,
                        won      = (UPPER(outcome) = %s)
                    WHERE market_id = %s
                      AND resolved  = FALSE
                """, (winner, market_id))
                rows_updated = cur.rowcount
                conn.commit()

            summary["trades_updated"] += rows_updated
            log.info(f"✅ Resolved '{market_id[:30]}' — winner: {winner} ({rows_updated} trades updated)")

        # Recalculate win_rate for all wallets that had trades resolved
        wallets_updated = _recalculate_all_win_rates(conn)
        summary["wallets_updated"] = wallets_updated

        conn.close()

    except Exception as e:
        log.error(f"Resolution pass failed: {e}", exc_info=True)
        summary["errors"] += 1

    log.info(f"Resolution pass complete: {summary}")
    return summary


def _recalculate_all_win_rates(conn) -> int:
    """
    Recalculates win_rate, winning_trades, losing_trades for ALL wallets
    based on their resolved trades. Only counts resolved trades.
    Returns the number of wallets updated.
    """
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE wallets w
            SET
                winning_trades = sub.wins,
                losing_trades  = sub.losses,
                win_rate = CASE
                    WHEN (sub.wins + sub.losses) > 0
                    THEN ROUND(sub.wins::NUMERIC / (sub.wins + sub.losses), 4)
                    ELSE 0.0
                END
            FROM (
                SELECT
                    wallet_address,
                    COUNT(*) FILTER (WHERE won = TRUE)  AS wins,
                    COUNT(*) FILTER (WHERE won = FALSE) AS losses
                FROM trades
                WHERE resolved = TRUE
                GROUP BY wallet_address
            ) sub
            WHERE w.wallet_address = sub.wallet_address
        """)
        updated = cur.rowcount
        conn.commit()
    log.info(f"Recalculated win rates for {updated} wallets.")
    return updated


# ── Public Entry Point for main.py ────────────────────────────────────────────
def run_resolution_pass():
    """Called by APScheduler cron in main.py."""
    try:
        init_db()
        return resolve_pending_trades()
    except Exception as e:
        log.error(f"Resolution cron failed: {e}")
        return {"error": str(e)}


# ── CLI Stats View ─────────────────────────────────────────────────────────────
def print_stats():
    """Print current win rate stats for all tracked wallets."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT handle,
                   total_trades,
                   winning_trades,
                   losing_trades,
                   ROUND(win_rate * 100, 1) AS win_pct
            FROM wallets
            ORDER BY win_rate DESC
            LIMIT 20
        """)
        rows = cur.fetchall()
    conn.close()

    print(f"\n{'Handle':<32} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'Win%':>6}")
    print("─" * 65)
    for r in rows:
        total_resolved = (r["winning_trades"] or 0) + (r["losing_trades"] or 0)
        win_str = f"{r['win_pct']}%" if total_resolved > 0 else "TBD"
        print(f"{r['handle']:<32} {r['total_trades']:>7} {r['winning_trades'] or 0:>6} {r['losing_trades'] or 0:>7} {win_str:>6}")


# ── CLI Entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Market Resolver")
    parser.add_argument("--init-db", action="store_true", help="Initialize DB columns and exit")
    parser.add_argument("--run",     action="store_true", help="Run the full resolution pass now")
    parser.add_argument("--stats",   action="store_true", help="Print current win rate stats")
    args = parser.parse_args()

    if args.init_db:
        init_db()
        sys.exit(0)

    if args.stats:
        print_stats()
        sys.exit(0)

    if args.run:
        init_db()
        result = resolve_pending_trades()
        print(json.dumps(result, indent=2))
        sys.exit(0)

    parser.print_help()
