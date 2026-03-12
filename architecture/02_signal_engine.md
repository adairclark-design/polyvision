# SOP 02 — Signal Engine

**Layer:** Architecture | **Tool:** `tools/signal_engine.py`

## Goal

Apply deterministic filtering and scoring logic to a raw `TradeEvent` to produce a `WhaleAlertPayload`. This is the gatekeeper — nothing reaches the notification layer without passing through here.

## Inputs

- `TradeEvent` JSON (from Redis stream `stream:trades:raw`)
- `WhaleProfile` JSON (from `whale_profiler.py`)

## Logic Flow

```
TradeEvent
    │
    ▼
[1] USD Value Filter
    │  usd_value < $10,000 → DISCARD
    │  usd_value ≥ $10,000 → PASS
    ▼
[2] Alert Tier Assignment
    │  $10,000 – $49,999  → tier = "STANDARD"
    │  $50,000+            → tier = "WHALE"
    ▼
[3] Wallet Win-Rate Check (from WhaleProfile)
    │  win_rate ≥ 0.60 → copy_trade_recommended = True
    │  win_rate < 0.60 → copy_trade_recommended = False
    │  win_rate = None (new wallet) → copy_trade_recommended = False
    ▼
[4] Build WhaleAlertPayload
    └─ Emit to Redis stream: stream:alerts:enriched
```

## Output

`WhaleAlertPayload` JSON (see `gemini.md` for full schema).

## Behavioral Rules (Non-Negotiable)

- **$10k Rule:** No alert may be emitted for `usd_value < 10000`. Hard coded, not configurable.
- **Win-Rate Rule:** `copy_trade_recommended` must be `false` if `win_rate < 0.60` or wallet is new.
- **Disclaimer:** The string `"Whales can hedge. Following a trade is at your own risk."` must always be present in the payload.
- **Forbidden terms:** Scanning for forbidden terms happens in `ai_summarizer.py`, not here.

## Error Handling

- If `WhaleProfile` fetch fails: set `copy_trade_recommended = False`, `wallet_win_rate = null`. Do not block the alert — log the profiler failure separately.
