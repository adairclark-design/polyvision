import os
import logging
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import APIRouter, HTTPException, Depends

router = APIRouter(prefix="/portfolio", tags=["Paper Trading"])
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")

def init_db():
    if not DATABASE_URL:
        log.warning("No DATABASE_URL found. Skipping paper trading DB init.")
        return
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Table for overall portfolio stats (the leaderboard root)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS paper_portfolios (
                        clerk_user_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        total_pnl NUMERIC DEFAULT 0,
                        trades_taken INTEGER DEFAULT 0,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    );
                """)
                # Table for individual positions
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS paper_positions (
                        id SERIAL PRIMARY KEY,
                        clerk_user_id TEXT REFERENCES paper_portfolios(clerk_user_id) ON DELETE CASCADE,
                        market_title TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        entry_price NUMERIC NOT NULL,
                        size_usd NUMERIC NOT NULL,
                        status TEXT DEFAULT 'OPEN',
                        pnl_usd NUMERIC DEFAULT 0,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    );
                """)
            conn.commit()
    except Exception as e:
        log.error(f"Failed to init paper trading DB: {e}")

class PaperTradeRequest(BaseModel):
    clerk_user_id: str
    display_name: str
    market_title: str
    outcome: str
    entry_price: float
    size_usd: float

@router.post("/trade")
def save_paper_trade(req: PaperTradeRequest):
    """Saves a new paper trade for a user and updates their portfolio."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Upsert the portfolio
                cur.execute("""
                    INSERT INTO paper_portfolios (clerk_user_id, display_name, trades_taken)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (clerk_user_id) DO UPDATE 
                    SET display_name = EXCLUDED.display_name,
                        trades_taken = paper_portfolios.trades_taken + 1,
                        updated_at = NOW();
                """, (req.clerk_user_id, req.display_name))
                
                # Insert the position
                cur.execute("""
                    INSERT INTO paper_positions (clerk_user_id, market_title, outcome, entry_price, size_usd)
                    VALUES (%s, %s, %s, %s, %s)
                """, (req.clerk_user_id, req.market_title, req.outcome, req.entry_price, req.size_usd))
            conn.commit()
        return {"success": True, "message": "Paper trade recorded."}
    except Exception as e:
        log.error(f"Failed to save paper trade: {e}")
        raise HTTPException(status_code=500, detail="Failed to save paper trade")
@router.delete("/trade/{trade_id}")
def delete_paper_trade(trade_id: int):
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM paper_positions WHERE id = %s RETURNING id", (trade_id,))
                deleted = cur.fetchone()
            conn.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Trade not found")
        return {"success": True, "message": "Trade removed"}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to delete paper trade: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete paper trade")

@router.get("/leaderboard")
def get_leaderboard():
    """Returns the top 50 users ranked by total_pnl."""
    if not DATABASE_URL:
        return []
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT display_name, total_pnl, trades_taken, updated_at
                    FROM paper_portfolios
                    ORDER BY total_pnl DESC
                    LIMIT 50;
                """)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        log.error(f"Failed to fetch leaderboard: {e}")
        return []

# Helper route to fetch a user's open positions in the format app.js expects
@router.get("/portfolio")
def get_user_portfolio_aggregate(clerk_user_id: str = ""):
    if not DATABASE_URL or not clerk_user_id:
        return {"trades": [], "total_pnl": 0, "roi_pct": 0, "win_rate": 0, "total_invested": 0, "priced_trades": 0}
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get the user's aggregate stats
                cur.execute("SELECT total_pnl, trades_taken FROM paper_portfolios WHERE clerk_user_id = %s", (clerk_user_id,))
                portfolio = cur.fetchone()
                total_pnl = portfolio['total_pnl'] if portfolio else 0
                trades_taken = portfolio['trades_taken'] if portfolio else 0

                # Get their positions
                cur.execute("""
                    SELECT id as trade_id, market_title, outcome, entry_price, size_usd as paper_size, status, pnl_usd as pnl, created_at as followed_at
                    FROM paper_positions
                    WHERE clerk_user_id = %s
                    ORDER BY created_at DESC;
                """, (clerk_user_id,))
                trades = [dict(row) for row in cur.fetchall()]
                
                total_invested = sum(t['paper_size'] for t in trades)
                roi_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
                win_rate = sum(1 for t in trades if t['pnl'] > 0) / len(trades) if trades else 0

                return {
                    "trades": trades,
                    "total_trades": len(trades),
                    "total_invested": total_invested,
                    "total_pnl": float(total_pnl),
                    "roi_pct": float(roi_pct),
                    "win_rate": win_rate,
                    "priced_trades": len(trades),
                    "last_updated": datetime.now().isoformat()
                }
    except Exception as e:
        log.error(f"Failed to fetch aggregate portfolio: {e}")
        return {"trades": [], "total_pnl": 0, "roi_pct": 0, "win_rate": 0, "total_invested": 0, "priced_trades": 0}
