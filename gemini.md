# PolyVision — Project Map (gemini.md)

**Source of Truth | Version 2.0 | Last Updated: 2026-03-04**

> This file is the authoritative Project Map. All behavioral rules, data schemas, and architectural decisions are documented here first. If logic changes, update this file before the code.

---

## 🏗️ Project Overview

**North Star:** Provide retail traders with a real-time, high-conviction "Smart Money" signal that identifies exactly which whales are entering a position before the market price fully adjusts.

**Tagline:** *"Bloomberg Terminal for Prediction Markets"*

---

## 🔌 Integrations

| Service | Purpose | Auth Required | Status |
|---|---|---|---|
| Polymarket Gamma API | Market listings, events, tags | None (public) | ⏳ Not tested |
| Polymarket Data API | Holder leaderboards, positions, activity | None (public) | ⏳ Not tested |
| Polymarket CLOB API | Real-time order book, price history, large-wall detection | L1 Private Key + L2 HMAC (for trading endpoints) | ⏳ Keys not set up |
| OpenAI GPT-4o | AI whale intent summaries | API Key | ⏳ Not set up |
| Firebase / OneSignal | Mobile push notifications | Project credentials | ⏳ Not set up |
| Discord Bot API | Alpha Group community alerts | Bot token | ⏳ Not set up |
| Telegram Bot API | Alpha Group community alerts | Bot token | ⏳ Not set up |

### API Base URLs

- **Gamma API:** `https://gamma-api.polymarket.com`
- **Data API:** `https://data-api.polymarket.com`
- **CLOB API:** `https://clob.polymarket.com`

### Python SDK

- **py-clob-client** (official): `pip install py-clob-client` — handles L1/L2 auth, order signing, market data.

---

## 🗃️ Database Architecture

### Redis (Fast Layer — "The Pulse")

- Holds the last 60 minutes of global trades in a sliding-window sorted set.
- Key pattern: `trades:live` → ZSET scored by timestamp.
- TTL: 3600 seconds (auto-evict entries older than 60 min).
- Use case: Whale accumulation detection in real time.

### PostgreSQL (History Layer — "The Vault")

- Stores permanent Whale Profiles on every wallet address seen.
- Tables: `wallets`, `trades`, `whale_alerts`, `markets`.
- Use case: Win-rate calculation, ROI history, Trader Persona generation.

---

## 📐 Data Schema

### Raw Input — Trade Event (from Polymarket CLOB `/trades` or Data API)

```json
{
  "id": "string (trade UUID)",
  "market_id": "string (condition_id)",
  "market_title": "string",
  "outcome": "string (Yes | No)",
  "price": "float (0.0–1.0, represents probability %)",
  "size": "float (number of shares)",
  "usd_value": "float (size * price = total USD spent)",
  "maker_address": "string (0x...)",
  "taker_address": "string (0x...)",
  "side": "string (BUY | SELL)",
  "timestamp": "ISO8601 string"
}
```

### Processed Output — Whale Alert Payload

```json
{
  "alert_id": "string (UUID)",
  "alert_tier": "string (STANDARD | WHALE)",
  "trader_handle": "string (e.g. 'The Oracle of Oregon')",
  "wallet_address": "string (0x...)",
  "market_title": "string",
  "outcome": "string (Yes | No)",
  "usd_value": "float",
  "wallet_win_rate": "float (0.0–1.0)",
  "wallet_roi_30d": "float",
  "ai_summary": "string (GPT-generated, max 280 chars)",
  "copy_trade_recommended": "boolean",
  "disclaimer": "Whales can hedge. Following a trade is at your own risk.",
  "timestamp": "ISO8601 string"
}
```

### Whale Profile (PostgreSQL: `wallets` table)

```json
{
  "wallet_address": "string PRIMARY KEY",
  "handle": "string (e.g. 'The Strategist')",
  "total_trades": "integer",
  "total_volume_usd": "float",
  "win_rate": "float",
  "roi_30d": "float",
  "roi_all_time": "float",
  "dominant_category": "string (e.g. 'US Politics', 'Crypto')",
  "first_seen": "ISO8601",
  "last_seen": "ISO8601"
}
```

---

## ⚖️ Behavioral Rules

### 🚦 Signal Thresholds

| Tier | Min USD Value | Label |
|---|---|---|
| Standard Alert | $10,000 | 🔵 STANDARD |
| Whale Alert | $50,000 | 🐋 WHALE |

### ✅ Copy Trade Eligibility

- `copy_trade_recommended = true` **only if:** `wallet_win_rate >= 0.60`
- Always append disclaimer: *"Whales can hedge. Following a trade is at your own risk."*

### 🔤 Tone & Language Rules

- **Tone:** Professional, Urgent, Analytical. Think Bloomberg Terminal.
- **Forbidden terms:** Gambling, Betting, Wager, Casino, Punt.
- **Preferred terms:** Trading, Position, Contract, Entry, Exposure.
- **No financial advice.** GPT summaries must end with the standard disclaimer.
- **No scraping private data.** Only public Polygon blockchain wallet addresses.

---

## 🗂️ Directory Structure

```
PolyVision/
├── gemini.md              ← This file (Source of Truth)
├── .env                   ← API Keys & secrets (NEVER commit)
├── .tmp/                  ← Ephemeral intermediate files
├── architecture/          ← SOP markdown docs (Layer 1)
│   ├── 01_data_ingestion.md
│   ├── 02_signal_engine.md
│   ├── 03_whale_profiler.md
│   ├── 04_ai_summarizer.md
│   └── 05_notification_delivery.md
├── tools/                 ← Deterministic Python scripts (Layer 3)
│   ├── test_connections.py
│   ├── polymarket_pulse.py
│   ├── whale_profiler.py
│   ├── signal_engine.py
│   ├── ai_summarizer.py
│   └── notifier.py
└── docs/                  ← End-user & API docs
```

---

## 🧠 Context Handoff Log

> **[2026-03-04] INIT:** Project initialized. Blueprint approved. Data schema defined (TradeEvent → WhaleAlertPayload → WhaleProfile). Behavioral rules locked. Next step: Phase 2 Link.

> **[2026-03-04] PHASE 2–3 COMPLETE:** Self-annealing event: Data API `/activity` returns 400 without a user address. Fixed: switched to `CLOB /markets` + `Gamma /trades` in `polymarket_pulse.py`. SOP `01_data_ingestion.md` updated. All 5 execution tools built and tested. All behavioral rule assertions pass.

> **[2026-03-04] PHASE 4 COMPLETE:** Command Center dashboard built (`dashboard/index.html`). Portland Dark-Mode. Three-column layout. Pulse Feed, Whale Cards, Copy-Trade HUD, Conviction Score, Reasoning Chips, Mock Follow, toast alerts. Browser verified — 0 console errors.

> **[2026-03-04] PHASE 5 COMPLETE:** Two-container microservice architecture deployed. The Ear (Node.js) + The Brain (FastAPI). Docker Compose stack with Redis + Postgres. GitHub Actions cron (profile recalc + Dead Man's Switch + schema check). Next step: provision API keys and run `docker compose up --build`.

---

## 🗂️ Directory Structure (v2.0 — Phase 5)

```text
PolyVision/
├── gemini.md              ← This file (Source of Truth, v2.0)
├── docker-compose.yml     ← Production stack orchestration
├── .env                   ← API Keys & secrets (NEVER commit)
├── .tmp/                  ← Ephemeral intermediate files
├── architecture/          ← SOP markdown docs (Layer 1)
│   ├── 01_data_ingestion.md
│   ├── 02_signal_engine.md
│   ├── 03_whale_profiler.md
│   ├── 04_ai_summarizer.md
│   └── 05_notification_delivery.md
├── ear/                   ← The Ear: Node.js WebSocket listener
│   ├── index.js           ← RTDS connection + reconnect + REST catch-up
│   ├── package.json
│   └── Dockerfile
├── brain/                 ← The Brain: Python FastAPI service
│   ├── main.py            ← Pipeline: signal → profile → AI → notify → stream
│   ├── requirements.txt
│   └── Dockerfile
├── tools/                 ← Deterministic Python scripts (Layer 3)
│   ├── test_connections.py
│   ├── polymarket_pulse.py
│   ├── whale_profiler.py
│   ├── signal_engine.py
│   ├── ai_summarizer.py
│   └── notifier.py
├── dashboard/             ← Command Center UI
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── assets/
├── deploy/                ← Deployment infrastructure
│   ├── cron_jobs.yml      ← GitHub Actions (3 automated jobs)
│   └── README.md          ← Deploy guide + trigger map
└── docs/
```

---

## 📋 Maintenance Log — PolyVision Alpha v1.0

**Version:** 1.0  
**Status:** Development — Awaiting API Key Provisioning  
**Last Updated:** 2026-03-04  

### Primary Trigger

- **Type:** WebSocket (Push-over-Pull)
- **Endpoint:** `wss://clob.polymarket.com/ws`
- **Service:** The Ear (`ear/index.js`)
- **Subscription:** `{ "type": "subscribe", "channel": "trade" }`

### Heartbeat

- Health check every **60 seconds** via `GET /health` on both services
- Dead Man's Switch: GitHub Actions pings every **5 minutes**
- Alert channel: Discord webhook on failure

### Circuit Breaker

- **Threshold:** 5 consecutive Brain calls with latency > 500ms
- **Action:** Log warning + Discord alert. Manual inspection required.
- **Backup Node:** Configure `BACKUP_RPC_URL` in `.env` for Quicknode/Infura fallback (Phase 5+)

### Reconnect Logic (The Ear)

- **Exponential backoff:** 1s → 2s → 4s → … → 60s max
- **On reconnect:** REST catch-up via `CLOB /markets` + `Gamma /trades` (last ~1,000 trades across top 10 active markets)
- **Buffer:** Events queued in Redis `buffer:trades` list when Brain is down; drained on next successful call

### Zero-Latency Cache (Redis)

- Key: `cache:last100trades` (ZSET, scored by timestamp)
- Max entries: 100 (auto-trimmed)
- Read latency target: **< 50ms** (served from `/trades/recent` endpoint)
- Key: `stream:alerts:live` (Redis Stream, max 500 entries) → WebSocket broadcast

### Automation Schedule

| Job | Schedule | Tool |
|---|---|---|
| WebSocket listener | Always-on | The Ear (Docker) |
| Profile recalculation | Daily 03:00 UTC | GitHub Actions |
| Dead Man's Switch | Every 5 min | GitHub Actions |
| API schema check | Daily 03:01 UTC | GitHub Actions |
| UMA Oracle webhook | Market resolution | Hookdeck (future) |

### API Schema Version Tracking

The Ear checks expected CLOB fields on startup and daily:

- **Expected:** `condition_id`, `question`, `active`, `minimum_tick_size`
- **Alert trigger:** Any expected field missing → Discord + stderr warning
- **Action:** Log in this Maintenance Log, update SOP `01_data_ingestion.md`

### Known API Changes (Self-Annealing Log)

| Date | Change | Fix Applied |
|---|---|---|
| 2026-03-04 | Data API `/activity` returns 400 without user wallet address | Switched to `CLOB /markets` + `Gamma /trades` |

### Tax & Audit Compliance

- All trades logged to PostgreSQL `trades` table with `usd_value` field
- 1% royalty transfers: log separately to `financial_ledger.csv` in `.tmp/`
- EOY export: `SELECT * FROM trades WHERE EXTRACT(YEAR FROM created_at) = 2026`
