# PolyVision — Deploy Guide

## Quick Start (Local Docker)

```bash
cd /Users/adairclark/Desktop/AntiGravity/PolyVision

# 1. Fill in your API keys
cp .env .env.local   # edit .env with real keys

# 2. Build and start all services
docker compose up --build -d

# 3. Check health
docker compose ps
curl http://localhost:8000/health  # Brain
curl http://localhost:3001/health  # Ear

# 4. Watch live logs
docker compose logs -f ear    # WebSocket events
docker compose logs -f brain  # Pipeline output

# 5. Init DB tables
docker compose exec brain python -c "from tools.whale_profiler import init_db; init_db()"
```

## Architecture

```
Polymarket RTDS (wss://clob.polymarket.com/ws)
        │
        ▼
  ┌─────────────┐    HTTP POST     ┌──────────────────────┐
  │  The Ear    │ ──────────────→  │  The Brain (FastAPI) │
  │  (Node.js)  │                  │  Signal Engine       │
  │  Port 3001  │                  │  Whale Profiler      │
  └─────────────┘                  │  AI Summarizer       │
        │                          │  Notifier            │
        │ Redis Buffer             │  Port 8000           │
        ▼                          └──────────────────────┘
   ┌─────────┐                              │
   │  Redis  │ ←───────────────────────────┘
   │  :6379  │  cache:last100trades          │
   └─────────┘  stream:alerts:live          │ WebSocket
        │                                   ▼
   ┌─────────────┐                   ┌───────────────┐
   │  PostgreSQL │                   │  Dashboard    │
   │  :5432      │                   │  /ws/pulse    │
   └─────────────┘                   └───────────────┘
```

## Railway Deployment

1. Install Railway CLI: `npm i -g @railway/cli`
2. Login: `railway login`
3. Create project: `railway init`
4. Set environment variables in Railway dashboard (copy from `.env`)
5. Deploy: `railway up`
6. Set the `POLYVISION_BRAIN_URL` and `POLYVISION_EAR_URL` secrets in GitHub settings

## GitHub Actions (Automation)

Copy `deploy/cron_jobs.yml` → `.github/workflows/cron_jobs.yml` in your repo.

Required GitHub Secrets:

| Secret | Value |
|---|---|
| `POLYVISION_BRAIN_URL` | Your Railway Brain URL |
| `POLYVISION_EAR_URL` | Your Railway Ear URL |
| `DISCORD_ALERT_WEBHOOK` | Discord webhook for health alerts |

## Trigger Map

| Trigger | Schedule | Tool |
|---|---|---|
| WebSocket (primary) | Real-time | Polymarket RTDS |
| Profile Recalculation | Daily 03:00 UTC | GitHub Actions |
| Dead Man's Switch | Every 5 minutes | GitHub Actions |
| API Schema Check | Daily 03:01 UTC | GitHub Actions |
| UMA Oracle webhook | On market resolution | Hookdeck (optional) |

## Circuit Breaker

The Ear monitors Brain latency. If 5 consecutive calls exceed 500ms, it logs a warning and can be configured to alert via Discord. Set `LATENCY_LIMIT` and `LATENCY_BREACH_THRESHOLD` in `.env` to tune.

## Dead Man's Switch

The GitHub Actions health check job runs every 5 minutes and posts to your Discord alert webhook if either service returns non-200. For additional monitoring, connect `/health` endpoints to BetterStack or Hund.io.
