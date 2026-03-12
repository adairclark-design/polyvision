# SOP 05 — Notification Delivery

**Layer:** Architecture | **Tool:** `tools/notifier.py`

## Goal

Route a final `WhaleAlertPayload` (with AI summary) to all configured delivery channels: push notification (Firebase/OneSignal), Discord embed, and Telegram message.

## Inputs

- `WhaleAlertPayload` JSON (fully enriched, with `ai_summary`)

## Delivery Channels

### Channel 1: Push Notification (Firebase / OneSignal)

**Template:**

```
Title: 🐋 WHALE ALERT  (or 🔵 SIGNAL for STANDARD tier)
Body:  "{handle}" just took a ${usd_value:,.0f} position on "{market_title}".
       {ai_summary}
```

- Send via OneSignal REST API `POST /notifications` or Firebase `POST /fcm/send`.
- Target: All subscribed users (topic `whale_alerts`).

### Channel 2: Discord Embed

**Embed fields:**

| Field | Value |
|---|---|
| Title | 🐋 {handle} — {market_title} |
| Color | `0x00C851` (green) if outcome=YES, `0xFF4444` (red) if NO |
| Position | {outcome} @ ${price:.2f} |
| Size | ${usd_value:,.0f} USD |
| Win Rate | {win_rate:.0%} |
| Copy Trade | ✅ Recommended / ⛔ Not Recommended |
| Summary | {ai_summary} |
| Footer | ⚠️ Whales can hedge. Trade at your own risk. |

### Channel 3: Telegram Message

```
{tier_emoji} *{handle}* — {alert_tier} ALERT
📊 Market: {market_title}
🎯 Position: {outcome} @ ${price:.2f}
💰 Size: ${usd_value:,.0f}
📈 Win Rate: {win_rate:.0%}
🤖 {ai_summary}
⚠️ _Trade at your own risk._
```

## Rate Limiting

- Max 1 WHALE alert per market per 5 minutes (Redis TTL key: `alert:sent:{market_id}`).
- Max 10 STANDARD alerts per hour globally (Redis counter: `alerts:standard:count`, TTL: 3600s).

## Retry Logic

- On any HTTP error: retry once after 3s.
- On second failure: log to `.tmp/notifier.log`, emit to stderr. Do not block pipeline.

## Dry-Run Mode

- Invoked with `--dry-run` flag.
- Prints formatted payload to stdout. Makes zero external HTTP calls.
