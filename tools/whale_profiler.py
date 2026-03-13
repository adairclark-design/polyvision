#!/usr/bin/env python3
"""
whale_profiler.py — PolyVision Layer 3 Tool
Reads a TradeEvent, upserts a WhaleProfile in PostgreSQL, and
returns the enriched profile for use by the Signal Engine.

Architecture SOP: architecture/03_whale_profiler.md
Usage:
    python tools/whale_profiler.py --init-db    # create tables
    python tools/whale_profiler.py --test        # run with fixture
    python tools/whale_profiler.py < trade.json  # pipe a TradeEvent
"""

import os
import sys
import json
import hashlib
import argparse
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/polyvision")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [profiler] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(".tmp/profiler.log", mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ── Persona Generator ─────────────────────────────────────────────────────────
ADJECTIVES = [
    "Strategist", "Oracle", "Tactician", "Visionary", "Analyst",
    "Architect", "Sentinel", "Navigator", "Pioneer", "Scholar",
    "Sage", "Prodigy", "Virtuoso", "Maestro", "Commander",
]
REGIONS = [
    "Oregon", "the Midwest", "Texas", "New York", "California",
    "Chicago", "Seattle", "Miami", "Boston", "Denver",
    "Atlanta", "Phoenix", "the Pacific Northwest", "New England",
]

def generate_handle(wallet_address: str) -> str:
    """Deterministically generate a Trader Persona from a wallet address hash."""
    h = int(hashlib.sha256(wallet_address.encode()).hexdigest(), 16)
    adj    = ADJECTIVES[h % len(ADJECTIVES)]
    region = REGIONS[(h >> 8) % len(REGIONS)]
    return f"The {adj} of {region}"


# ── Database ──────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=5)


def init_db():
    """Create tables if they don't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS wallets (
        wallet_address   TEXT PRIMARY KEY,
        handle           TEXT NOT NULL,
        total_trades     INTEGER DEFAULT 0,
        total_volume_usd FLOAT DEFAULT 0.0,
        winning_trades   INTEGER DEFAULT 0,
        win_rate         FLOAT DEFAULT 0.0,
        roi_30d          FLOAT DEFAULT 0.0,
        roi_all_time     FLOAT DEFAULT 0.0,
        dominant_category TEXT,
        first_seen       TIMESTAMPTZ DEFAULT NOW(),
        last_seen        TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS trades (
        id               TEXT PRIMARY KEY,
        wallet_address   TEXT REFERENCES wallets(wallet_address),
        market_id        TEXT,
        market_title     TEXT,
        outcome          TEXT,
        price            FLOAT,
        size             FLOAT,
        usd_value        FLOAT,
        side             TEXT,
        resolved         BOOLEAN DEFAULT FALSE,
        won              BOOLEAN,
        created_at       TIMESTAMPTZ DEFAULT NOW()
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    log.info("Database tables initialized.")


# ── Profile Logic ─────────────────────────────────────────────────────────────
def upsert_wallet(conn, trade: dict) -> dict:
    """Upsert a wallet row and return the updated WhaleProfile."""
    addr     = trade["maker_address"]
    handle   = generate_handle(addr)
    usd      = float(trade.get("usd_value", 0))
    now      = datetime.now(timezone.utc).isoformat()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # 1. Determine if this wallet already exists
        cur.execute("SELECT wallet_address FROM wallets WHERE wallet_address = %s;", (addr,))
        is_new_wallet = cur.fetchone() is None

        # 2. Upsert wallet
        cur.execute("""
            INSERT INTO wallets (wallet_address, handle, total_trades, total_volume_usd, last_seen)
            VALUES (%s, %s, 1, %s, NOW())
            ON CONFLICT (wallet_address) DO UPDATE
                SET total_trades     = wallets.total_trades + 1,
                    total_volume_usd = wallets.total_volume_usd + EXCLUDED.total_volume_usd,
                    last_seen        = NOW()
            RETURNING *;
        """, (addr, handle, usd))
        wallet_row = dict(cur.fetchone())

        # 3. If brand new, fetch real historical data from Polymarket API to prepopulate
        if is_new_wallet:
            try:
                # Local import to prevent circular dependencies if tools import each other
                import brain.tools.wallet_xray as xray
                log.info(f"New wallet detected {addr[:8]}... Triggering live historical backfill.")
                profile_stats = xray.get_xray(addr, force_refresh=True)

                if profile_stats and profile_stats.get('win_rate'):
                    real_win_rate = profile_stats['win_rate']
                    real_roi = profile_stats.get('all_time_pnl', 0)
                    real_vol = max((usd, profile_stats.get('all_time_vol', usd)))

                    cur.execute("""
                        UPDATE wallets 
                        SET win_rate = %s, roi_all_time = %s, total_volume_usd = %s
                        WHERE wallet_address = %s
                        RETURNING *;
                    """, (real_win_rate, real_roi, real_vol, addr))
                    wallet_row = dict(cur.fetchone())
                    log.info(f"Backfill complete: Win Rate {real_win_rate * 100}%, PNL {real_roi}")
            except Exception as e:
                log.warning(f"Failed to backfill historical data for {addr}: {e}")

        # 4. Insert trade (deduplication via ON CONFLICT DO NOTHING)
        cur.execute("""
            INSERT INTO trades (id, wallet_address, market_id, market_title, outcome,
                                price, size, usd_value, side)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING;
        """, (
            trade.get("id", ""),
            addr,
            trade.get("market_id", ""),
            trade.get("market_title", ""),
            trade.get("outcome", ""),
            float(trade.get("price", 0)),
            float(trade.get("size", 0)),
            usd,
            trade.get("side", "BUY"),
        ))
        conn.commit()

    # Build and return WhaleProfile
    return {
        "wallet_address":    wallet_row["wallet_address"],
        "handle":            wallet_row["handle"],
        "total_trades":      wallet_row["total_trades"],
        "total_volume_usd":  wallet_row["total_volume_usd"],
        "win_rate":          wallet_row["win_rate"],
        "roi_30d":           wallet_row["roi_30d"],
        "roi_all_time":      wallet_row["roi_all_time"],
        "dominant_category": wallet_row.get("dominant_category"),
        "first_seen":        str(wallet_row.get("first_seen", "")),
        "last_seen":         str(wallet_row.get("last_seen", "")),
    }


def profile_trade(trade: dict) -> dict | None:
    """Entry point: given a TradeEvent, return enriched WhaleProfile."""
    try:
        os.makedirs(".tmp", exist_ok=True)
        with get_conn() as conn:
            return upsert_wallet(conn, trade)
    except Exception as e:
        log.error(f"Profiler failed for {trade.get('maker_address','?')}: {e}")
        return None


# ── Test Fixture ──────────────────────────────────────────────────────────────
TEST_TRADE = {
    "id":            "test-trade-whale-profiler-001",
    "maker_address": "0xDeAdBeEf1234567890abcdef",
    "market_id":     "0xabc123",
    "market_title":  "Will the Fed cut rates in March 2026?",
    "outcome":       "Yes",
    "price":         0.72,
    "size":          69444.44,
    "usd_value":     50000.00,
    "side":          "BUY",
}


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyVision Whale Profiler")
    parser.add_argument("--init-db", action="store_true", help="Initialize DB tables and exit")
    parser.add_argument("--test",    action="store_true", help="Run with fixture data")
    args = parser.parse_args()

    if args.init_db:
        init_db()
        sys.exit(0)

    if args.test:
        print("🧪 Running whale profiler with test fixture...\n")
        # Verify handle generation determinism
        handle1 = generate_handle(TEST_TRADE["maker_address"])
        handle2 = generate_handle(TEST_TRADE["maker_address"])
        assert handle1 == handle2, "Handle generation is not deterministic!"
        print(f"✅ Handle: {handle1}")
        print("(DB upsert skipped in --test mode to avoid requiring a live DB)")
        sys.exit(0)

    raw = sys.stdin.read().strip()
    trade = json.loads(raw)
    profile = profile_trade(trade)
    if profile:
        print(json.dumps(profile, indent=2))
    else:
        sys.exit(1)
