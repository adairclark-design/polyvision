#!/usr/bin/env python3
from __future__ import annotations
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
    max_age_minutes: int | None = None,
) -> list[dict]:
    """
    Fetch recent large whale trades from the PolyVision database.

    Args:
        min_usd:          Minimum USD trade size to include (default $25k)
        hours_back:       How many hours back to look (default 48h)
        limit:            Max trades to return (default 10)
        max_age_minutes:  If set, only return trades younger than this many
                          minutes (freshness gate). None = no freshness filter.

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
        try:
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"], check=True)
            import psycopg2
            import psycopg2.extras
        except Exception as e:
            log.error(f"psycopg2 not available: {e}")
            return []

    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)

    # Build optional freshness clause
    freshness_clause = ""
    freshness_param  = None
    if max_age_minutes is not None:
        freshness_cutoff = now - timedelta(minutes=max_age_minutes)
        freshness_clause = "AND t.created_at >= %s"
        freshness_param  = freshness_cutoff

    query = f"""
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
          {freshness_clause}
          AND t.id NOT IN (SELECT trade_id FROM video_history)
        ORDER BY t.usd_value DESC, t.created_at DESC
        LIMIT %s
    """

    try:
        if db_url.startswith("postgres://") and not db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        conn   = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        params = [min_usd, cutoff]
        if freshness_param:
            params.append(freshness_param)
        params.append(limit)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        trades = []
        for row in rows:
            t = dict(row)
            if isinstance(t.get("created_at"), datetime):
                t["created_at"] = t["created_at"].isoformat()
            t["age_label"] = _age_label(t.get("created_at", ""))
            trades.append(t)

        freshness_tag = f", max_age={max_age_minutes}m" if max_age_minutes else ""
        log.info(f"whale_data_fetcher: Fetched {len(trades)} real trades (>= ${min_usd:,.0f}, last {hours_back}h{freshness_tag})")
        return trades

    except psycopg2.OperationalError as e:
        log.warning(f"whale_data_fetcher: DB connection failed ({e}) — no real trades available.")
        return []
    except Exception as e:
        log.error(f"whale_data_fetcher: Query failed: {e}")
        return []


def fetch_momentum_clusters(
    min_cluster_usd: float = 50_000,
    min_trade_count: int   = 3,
    window_minutes:  int   = 120,
    max_age_minutes: int   = 90,
) -> list[dict]:
    """
    Detect markets with a burst of whale activity in a short rolling window.

    A "momentum cluster" fires when:
      - 3+ individual trades hit the same market_id in the last `window_minutes`
      - Their combined USD value exceeds `min_cluster_usd`
      - At least one trade is younger than `max_age_minutes` (freshness gate)
      - No video has been generated for that market recently
        (the scheduler enforces a per-market cooldown on top of this)

    Returns a list of cluster dicts, each containing:
        market_id, market_title, trade_count, total_usd,
        latest_price, latest_trade_at, representative_trade_id
    Ordered by total_usd DESC so the hottest cluster is first.
    """
    db_url = _get_db_url()
    if not db_url:
        log.warning("fetch_momentum_clusters: No DATABASE_URL.")
        return []

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        log.error("psycopg2 not available for momentum query.")
        return []

    now              = datetime.now(timezone.utc)
    window_cutoff    = now - timedelta(minutes=window_minutes)
    freshness_cutoff = now - timedelta(minutes=max_age_minutes)

    # Find markets where ≥N trades totaling ≥$X happened in the window,
    # and at least one trade is fresh (< max_age_minutes old).
    # Exclude markets that already have a video in video_history.
    query = """
        SELECT
            t.market_id,
            t.market_title,
            COUNT(*)               AS trade_count,
            SUM(t.usd_value)       AS total_usd,
            AVG(t.price)           AS latest_price,
            MAX(t.created_at)      AS latest_trade_at,
            MAX(t.id)              AS representative_trade_id
        FROM trades t
        WHERE t.created_at >= %s
          AND t.usd_value   >= 5000
        GROUP BY t.market_id, t.market_title
        HAVING
            COUNT(*)         >= %s
            AND SUM(t.usd_value) >= %s
            AND MAX(t.created_at) >= %s
        ORDER BY total_usd DESC
        LIMIT 5;
    """

    try:
        if db_url.startswith("postgres://") and not db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)

        conn   = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(query, (window_cutoff, min_trade_count, min_cluster_usd, freshness_cutoff))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        clusters = []
        for row in rows:
            c = dict(row)
            if isinstance(c.get("latest_trade_at"), datetime):
                c["latest_trade_at"] = c["latest_trade_at"].isoformat()
            c["total_usd"]    = float(c.get("total_usd", 0))
            c["latest_price"] = float(c.get("latest_price", 0.5))
            c["trade_count"]  = int(c.get("trade_count", 0))
            clusters.append(c)

        if clusters:
            log.info(
                f"[Momentum] {len(clusters)} cluster(s) detected. "
                f"Hottest: '{clusters[0]['market_title'][:50]}' "
                f"({clusters[0]['trade_count']} trades, ${clusters[0]['total_usd']:,.0f})"
            )
        return clusters

    except psycopg2.OperationalError as e:
        log.warning(f"fetch_momentum_clusters: DB connection failed: {e}")
        return []
    except Exception as e:
        log.error(f"fetch_momentum_clusters: Query failed: {e}")
        return []


def pick_best_trade(trades: list[dict]) -> dict | None:
    """
    From the list of real trades, pick the single most 'marketable' one.

    Selection strategy: Contrarian Score = usd_value × market_uncertainty
      where market_uncertainty = 1 - |price - 0.5| × 2  (peaks at 1.0 for 50/50,
      falls to 0.0 for 100% certainty).

    This prevents 99%+ sure-thing bets (like a $150K bet at 0.99 odds) from
    winning the selection — they score near-zero despite their size.
    Trades with price outside 15%–85% range are excluded entirely as boring.
    """
    if not trades:
        return None

    PROB_MIN = 0.15   # Reject near-certain bets (≤15% implied prob side)
    PROB_MAX = 0.85   # Reject near-certain bets (≥85% implied prob side)

    valid = [
        t for t in trades
        if t.get("market_title", "").strip()
        and PROB_MIN <= float(t.get("price", 0.5)) <= PROB_MAX
    ]

    if not valid:
        # Fallback: relax filter to 10%–90% if nothing qualifies
        log.warning("[Picker] No trades in 15-85% probability band — relaxing to 10-90%.")
        valid = [
            t for t in trades
            if t.get("market_title", "").strip()
            and 0.10 <= float(t.get("price", 0.5)) <= 0.90
        ]

    if not valid:
        # Final fallback: just take largest by USD (old behaviour) to avoid None
        log.warning("[Picker] All trades are near-certain — using raw USD fallback.")
        valid = [t for t in trades if t.get("market_title", "").strip()]

    if not valid:
        return None

    def _contrarian_score(t: dict) -> float:
        usd   = float(t.get("usd_value", 0))
        price = float(t.get("price", 0.5))
        # uncertainty: 1.0 at p=0.50, 0.0 at p=0.00 or p=1.00
        uncertainty = 1.0 - abs(price - 0.5) * 2
        return usd * uncertainty

    best = sorted(valid, key=_contrarian_score, reverse=True)[0]
    score = _contrarian_score(best)
    log.info(
        f"[Picker] Selected trade: ${float(best.get('usd_value', 0)):,.0f} "
        f"@ {float(best.get('price', 0.5)):.0%} odds | "
        f"contrarian_score={score:,.0f} | '{best.get('market_title', '')[:50]}'"
    )
    return best


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
