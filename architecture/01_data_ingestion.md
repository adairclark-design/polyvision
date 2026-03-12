# SOP 01 — Data Ingestion

**Layer:** Architecture | **Tool:** `tools/polymarket_pulse.py`

## Goal

Continuously poll Polymarket public APIs to capture every large trade (≥ $10,000 USD) in real time, and push qualifying events into Redis for downstream processing.

## Inputs

| Source | Endpoint | Auth |
|---|---|---|
| Data API | `GET https://data-api.polymarket.com/activity?limit=50&interval=1m` | None |
| CLOB API | `GET https://clob.polymarket.com/trades?market={condition_id}` | None (public) |
| Gamma API | `GET https://gamma-api.polymarket.com/markets?limit=100&active=true` | None |

## Polling Strategy

- **Interval:** Every 30 seconds (configurable via `POLL_INTERVAL_SECONDS` in `.env`).
- **Pagination:** Use cursor-based pagination (`next_cursor`) on Data API `/activity`. Stop when `next_cursor` is null or timestamp of last result is older than 60 seconds.
- **Deduplication:** Every trade has a unique `id`. Before processing, check Redis key `processed:{trade_id}` (TTL: 2h). Skip if exists.

## Output

- Raw `TradeEvent` JSON written to Redis stream key `stream:trades:raw`.
- Consumer: `signal_engine.py`.

## Error Handling & Edge Cases

- **HTTP 429 (Rate limit):** Back off exponentially (2s, 4s, 8s, max 60s). Log to `.tmp/pulse.log`.
- **HTTP 5xx:** Retry 3 times with 5s delay. If all fail, skip the cycle and alert to stderr.
- **Network timeout:** Set `requests` timeout to 10s. Log and continue next cycle.
- **Market not found:** If `market_id` is not in local cache, fetch from Gamma API and cache for 1h in Redis key `market:{condition_id}`.

## Known Constraints

- Data API `/activity` returns most recent global activity first (descending timestamp).
- Large walls in the order book are detected via CLOB API `GET /book?token_id={token_id}` — look for bids/asks with `size > 5000` (in shares).
