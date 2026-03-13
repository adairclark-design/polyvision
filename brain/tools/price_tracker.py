"""
price_tracker.py — PolyVision Price Movement Correlation

Tracks how market prices move after a whale trade is placed.
For each whale trade, we record the entry price at trade time.
This job runs 24h later, fetches current price, and computes delta.
Aggregate "avg price impact" per wallet is stored in wallets table
and shown in the whale X-Ray profile: "📊 Avg price impact: +4.2%"

CLI:
  python price_tracker.py --check     # compute 24h deltas for trades from yesterday
  python price_tracker.py --stats     # show top wallets by avg price impact
"""

import os
import sys
import logging
import argparse
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [price_tracker] %(levelname)s: %(message)s')

DATABASE_URL = os.getenv('DATABASE_URL', '')
GAMMA_API    = 'https://gamma-api.polymarket.com'


def _connect():
    return psycopg2.connect(DATABASE_URL, connect_timeout=5)


# ── DB Schema ─────────────────────────────────────────────────────────────────
def init_db():
    """Add price impact columns and table (idempotent)."""
    if not DATABASE_URL:
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # Per-trade price impact table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_impacts (
                    id          SERIAL PRIMARY KEY,
                    trade_id    TEXT UNIQUE,
                    market_id   TEXT,
                    outcome     TEXT,
                    entry_price FLOAT,
                    price_24h   FLOAT,
                    delta_pct   FLOAT,
                    checked_at  TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_pi_market  ON price_impacts(market_id);
                CREATE INDEX IF NOT EXISTS idx_pi_checked ON price_impacts(checked_at);
            """)
            # Rolling average on wallets table
            cur.execute("""
                ALTER TABLE wallets
                ADD COLUMN IF NOT EXISTS avg_price_impact FLOAT DEFAULT NULL;
            """)
        conn.commit()
        log.info('price_impacts table and wallets.avg_price_impact ready.')
    except Exception as e:
        log.warning(f'init_db error (may already exist): {e}')
        conn.rollback()
    finally:
        conn.close()


# ── Polymarket Price Fetch ────────────────────────────────────────────────────
def fetch_current_price(market_id: str, outcome: str) -> float | None:
    """Return current last trade price for a given market + outcome."""
    try:
        resp = requests.get(
            f'{GAMMA_API}/markets',
            params={'conditionId': market_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        markets = data if isinstance(data, list) else data.get('markets', [])
        if not markets:
            return None
        m = markets[0]
        # outcomePrices is array [yes_price, no_price]
        prices = m.get('outcomePrices') or []
        if outcome.upper() == 'YES' and len(prices) >= 1:
            return float(prices[0])
        elif outcome.upper() == 'NO' and len(prices) >= 2:
            return float(prices[1])
        return float(m.get('lastTradePrice', 0))
    except Exception as e:
        log.warning(f'fetch_current_price({market_id}): {e}')
        return None


# ── Core Check Pass ───────────────────────────────────────────────────────────
def check_price_impact():
    """
    For each trade placed ~24h ago (not yet checked), fetch the current
    market price and compute the % change since entry.
    """
    if not DATABASE_URL:
        log.warning('No DATABASE_URL — skipping price impact check.')
        return

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch trades from 22–26h ago that haven't been checked yet
            cur.execute("""
                SELECT t.id AS trade_id, t.market_id, t.outcome, t.price AS entry_price,
                       t.wallet_address
                FROM trades t
                LEFT JOIN price_impacts pi ON pi.trade_id = t.id::TEXT
                WHERE t.created_at BETWEEN NOW() - INTERVAL '26 hours'
                                       AND NOW() - INTERVAL '22 hours'
                  AND pi.trade_id IS NULL
                  AND t.market_id IS NOT NULL
                LIMIT 200
            """)
            trades = cur.fetchall()

        log.info(f'Checking price impact for {len(trades)} trades...')
        if not trades:
            return

        inserts = []
        wallet_deltas: dict[str, list[float]] = {}

        for t in trades:
            current = fetch_current_price(t['market_id'], t['outcome'])
            if current is None or t['entry_price'] is None or t['entry_price'] == 0:
                continue

            delta_pct = (current - t['entry_price']) / t['entry_price']
            inserts.append((
                str(t['trade_id']), t['market_id'], t['outcome'],
                float(t['entry_price']), float(current), delta_pct,
            ))
            wallet = t['wallet_address']
            wallet_deltas.setdefault(wallet, []).append(delta_pct)

        if inserts:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO price_impacts (trade_id, market_id, outcome, entry_price, price_24h, delta_pct)
                    VALUES %s
                    ON CONFLICT (trade_id) DO NOTHING
                """, inserts)
            conn.commit()
            log.info(f'Stored {len(inserts)} price impact records.')

        # Update avg_price_impact per wallet
        update_wallet_avg_impact(conn, wallet_deltas)

    except Exception as e:
        log.error(f'check_price_impact error: {e}')
        conn.rollback()
    finally:
        conn.close()


def update_wallet_avg_impact(conn, wallet_deltas: dict[str, list[float]]):
    """Recompute rolling avg price impact per affected wallet."""
    try:
        with conn.cursor() as cur:
            for wallet, deltas in wallet_deltas.items():
                avg = sum(deltas) / len(deltas)
                cur.execute("""
                    UPDATE wallets SET avg_price_impact = %s
                    WHERE address = %s
                """, (avg, wallet))
        conn.commit()
        log.info(f'Updated avg_price_impact for {len(wallet_deltas)} wallets.')
    except Exception as e:
        log.error(f'update_wallet_avg_impact: {e}')
        conn.rollback()


def get_top_impact_wallets(limit: int = 10) -> list[dict]:
    """Return wallets ranked by avg_price_impact descending."""
    if not DATABASE_URL:
        return []
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT address, handle, avg_price_impact, total_trades
                FROM wallets
                WHERE avg_price_impact IS NOT NULL
                ORDER BY avg_price_impact DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────
def run_price_tracker_pass():
    """Entry point for the APScheduler cron."""
    log.info('--- Price Impact Check Pass ---')
    init_db()
    check_price_impact()
    log.info('--- Price Impact Check Complete ---')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check',  action='store_true', help='Run 24h price check')
    parser.add_argument('--stats',  action='store_true', help='Show top wallets by price impact')
    parser.add_argument('--init-db',action='store_true', help='Initialize DB schema')
    args = parser.parse_args()

    if args.init_db:
        init_db()
    elif args.check:
        run_price_tracker_pass()
    elif args.stats:
        rows = get_top_impact_wallets(20)
        for r in rows:
            impact = r['avg_price_impact']
            print(f"{r['handle'] or r['address'][:10]}: {'+' if impact >= 0 else ''}{impact*100:.1f}% avg 24h impact ({r['total_trades']} trades)")
    else:
        parser.print_help()
