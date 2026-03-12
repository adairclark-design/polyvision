# SOP 03 — Whale Profiler

**Layer:** Architecture | **Tool:** `tools/whale_profiler.py`

## Goal

For every trade that passes through the Signal Engine, build and maintain a persistent `WhaleProfile` in PostgreSQL. This profile powers the win-rate filter and the "Trader Persona" identity system.

## Inputs

- `TradeEvent` JSON (wallet address + trade details)

## Database Table: `wallets`

```sql
CREATE TABLE IF NOT EXISTS wallets (
    wallet_address  TEXT PRIMARY KEY,
    handle          TEXT,                      -- e.g. "The Oracle of Oregon"
    total_trades    INTEGER DEFAULT 0,
    total_volume_usd FLOAT DEFAULT 0.0,
    winning_trades  INTEGER DEFAULT 0,
    win_rate        FLOAT DEFAULT 0.0,
    roi_30d         FLOAT DEFAULT 0.0,
    roi_all_time    FLOAT DEFAULT 0.0,
    dominant_category TEXT,
    first_seen      TIMESTAMPTZ DEFAULT NOW(),
    last_seen       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trades (
    id              TEXT PRIMARY KEY,
    wallet_address  TEXT REFERENCES wallets(wallet_address),
    market_id       TEXT,
    market_title    TEXT,
    outcome         TEXT,
    price           FLOAT,
    size            FLOAT,
    usd_value       FLOAT,
    side            TEXT,
    resolved        BOOLEAN DEFAULT FALSE,
    won             BOOLEAN,
    timestamp       TIMESTAMPTZ
);
```

## Logic Flow

```
TradeEvent arrives
    │
    ▼
UPSERT wallets row
    ├── New wallet: create row, generate handle via persona_generator()
    └── Existing:  update last_seen, total_trades +1, total_volume_usd += usd_value
    │
    ▼
INSERT into trades (resolved=False, won=NULL)
    │
    ▼
Schedule resolution check (24h after market close)
    └── When market resolves → UPDATE trades SET resolved=True, won=<bool>
        → Recalculate win_rate = winning_trades / total_trades
        → Return updated WhaleProfile
```

## Persona Generator Rules

- Assigned once on first trade. Never changed.
- Format: `"The [Adjective] of [US City/Region]"` — generated deterministically from wallet address hash.
- Examples: "The Strategist," "The Oracle of Oregon," "The Chicago Bull."
- Do not use real names or PII.

## Error Handling

- **DB connection failure:** Log to `.tmp/profiler.log`, return `null` profile. Signal Engine degrades gracefully.
- **Duplicate trade ID:** Use `INSERT ... ON CONFLICT DO NOTHING`.
