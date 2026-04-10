#!/usr/bin/env python3
"""
whale_data_fetcher.py — Layer 3: Execution
Fetches REAL recent whale trades from the PolyVision PostgreSQL database
(the same DB that PolyVision Brain writes to in real time).

Returns the most recent qualifying whale trades so the marketing agent can
write content based on ACCURATE, REAL trade data — not fabricated numbers.

Schema (from PolyVision's whale_profiler.py):
    trades (
        id              TEXT PRIMARY KEY,
        wallet_address  TEXT,
        market_id       TEXT,
        market_title    TEXT,
        outcome         TEXT,
        price           FLOAT,
        size            FLOAT,
        usd_value       FLOAT,
        side            TEXT,
        created_at      TIMESTAMP
        ... (source, trader_handle etc. may also be present)
    )

Usage:
    python whale_data_fetcher.py            # prints recent trades as JSON
    python whale_data_fetcher.py --min 50000  # only trades >= $50k
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# ── Database URL ───────────────────────────────────────────────────────────────
# Try multiple sources in priority order
def _get_db_url() -> str | None:
    # 1. VisionEdge secrets.json
    secrets_path = Path(__file__).parent.parent.parent / "secrets.json"
    try:
        with open(secrets_path) as f:
            url = json.load(f).get("DATABASE_URL", "")
            if url:
                return url
    except Exception:
        pass

    # 2. PolyVision .env (local Railway dev URL)
    polyvision_env = Path("/Users/adairclark/Desktop/AntiGravity/PolyVision/.env")
    if polyvision_env.exists():
        try:
            from dotenv import dotenv_values
            vals = dotenv_values(polyvision_env)
            url = vals.get("DATABASE_URL", "")
            if url:
                return url
        except Exception:
            pass

    # 3. Environment variable
    return os.getenv("DATABASE_URL", "")


def fetch_recent_whale_trades(
    min_usd: float = 25_000,
    hours_back: int = 48,
    limit: int = 10,
) -> list[dict]:
    """
    Fetch recent large whale trades from the PolyVision database.

    Args:
        min_usd:    Minimum USD trade size to include (default $25k)
        hours_back: How many hours back to look (default 48h)
        limit:      Max trades to return (default 10)

    Returns:
        List of trade dicts with keys:
            market_title, outcome, usd_value, price, side,
            source, trader_handle, created_at
        Empty list if DB unavailable (caller must handle gracefully).
    """
    db_url = _get_db_url()
    if not db_url:
        log.warning("whale_data_fetcher: No DATABASE_URL — cannot fetch real trades.")
        return []

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        # Try installing it quietly
        try:
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"], check=True)
            import psycopg2
            import psycopg2.extras
        except Exception as e:
            log.error(f"psycopg2 not available: {e}")
            return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    query = """
        SELECT
            t.id,
            t.market_title,
            t.outcome,
            t.usd_value,
            t.price,
            t.size,
            t.side,
            t.created_at,
            COALESCE(w.handle, LEFT(t.wallet_address, 8)) AS trader_handle,
            COALESCE(w.source, 'Polymarket')               AS source
        FROM trades t
        LEFT JOIN wallets w ON t.wallet_address = w.wallet_address
        WHERE t.usd_value >= %s
          AND t.created_at >= %s
          AND t.id NOT IN (SELECT trade_id FROM video_history)
        ORDER BY t.usd_value DESC, t.created_at DESC
        LIMIT %s
    """

    try:
        # psycopg2 doesn't accept asyncpg-style postgres:// — convert if needed
        if db_url.startswith("postgres://") and not db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        conn   = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(query, (min_usd, cutoff, limit))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        trades = []
        for row in rows:
            t = dict(row)
            # Convert datetime to ISO string for JSON serialisation
            if isinstance(t.get("created_at"), datetime):
                t["created_at"] = t["created_at"].isoformat()
            # Format a friendly timestamp (e.g. "2 hours ago")
            t["age_label"] = _age_label(t.get("created_at", ""))
            trades.append(t)

        log.info(f"whale_data_fetcher: Fetched {len(trades)} real trades (>= ${min_usd:,.0f}, last {hours_back}h)")
        return trades

    except psycopg2.OperationalError as e:
        log.warning(f"whale_data_fetcher: DB connection failed ({e}) — no real trades available.")
        return []
    except Exception as e:
        log.error(f"whale_data_fetcher: Query failed: {e}")
        return []


def pick_best_trade(trades: list[dict]) -> dict | None:
    """
    From the list of real trades, pick the single most 'marketable' one —
    biggest dollar amount with a human-readable market title.
    """
    if not trades:
        return None
    # Sort by USD value descending, skip trades with blank market titles
    valid = [t for t in trades if t.get("market_title", "").strip()]
    if not valid:
        return None
    return sorted(valid, key=lambda t: float(t.get("usd_value", 0)), reverse=True)[0]


def format_trade_for_llm(trade: dict) -> str:
    """
    Format a real whale trade as a concise context block for the LLM prompt.
    The LLM reads this and writes marketing copy based on the REAL numbers.
    """
    usd       = float(trade.get("usd_value", 0))
    market    = trade.get("market_title", "an undisclosed market")
    outcome   = trade.get("outcome", "YES")
    price     = float(trade.get("price", 0.5))
    source    = trade.get("source", "Polymarket").title()
    handle    = trade.get("trader_handle", "")
    age       = trade.get("age_label", "recently")

    usd_str   = f"${usd:,.0f}"
    pct_str   = f"{price:.0%}"
    handle_str = f" (trader: {handle})" if handle else ""

    return (
        f"REAL WHALE TRADE DATA — use these EXACT numbers in your content:\n"
        f"  Platform:    {source}\n"
        f"  Market:      {market}\n"
        f"  Outcome bet: {outcome}\n"
        f"  Amount:      {usd_str}\n"
        f"  Odds/Price:  {pct_str}\n"
        f"  Timing:      {age}{handle_str}\n\n"
        f"RULE: You MUST use the EXACT dollar amount '{usd_str}' and market name in your content. "
        f"Do NOT invent different numbers or markets."
    )


def _age_label(iso_str: str) -> str:
    """Returns a human-friendly relative time label."""
    if not iso_str:
        return "recently"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        minutes = int(diff.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes} minutes ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hours ago"
        return f"{hours // 24} days ago"
    except Exception:
        return "recently"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--min", type=float, default=25000, help="Min USD size")
    parser.add_argument("--hours", type=int, default=48, help="Hours back to look")
    args = parser.parse_args()

    trades = fetch_recent_whale_trades(min_usd=args.min, hours_back=args.hours)
    if trades:
        print(f"\n✅ Found {len(trades)} real whale trades:\n")
        for t in trades:
            print(f"  ${t['usd_value']:>12,.0f}  |  {t['source']:<12}  |  {t['market_title'][:60]}")
        best = pick_best_trade(trades)
        print(f"\n📌 Best for marketing:\n{format_trade_for_llm(best)}")
    else:
        print("⚠️  No real trades found — check DATABASE_URL and trade history.")
