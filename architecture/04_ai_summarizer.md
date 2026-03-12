# SOP 04 — AI Summarizer

**Layer:** Architecture | **Tool:** `tools/ai_summarizer.py`

## Goal

Transform a `WhaleAlertPayload` into a concise, professional, 280-character-max AI summary using GPT-4o. The output must feel like Bloomberg copy — not hype, not gambling slang.

## Inputs

- `WhaleAlertPayload` JSON

## Prompt Template

```
System: You are a professional financial analyst covering prediction markets.
Write a single, factual, analytical sentence (max 280 characters) summarizing
this whale trade. Use Bloomberg Terminal tone. Do NOT use the words:
gambling, betting, wager, casino, punt, moonshot, ape, degen.
Always end with: "Trade at your own risk."

User:
- Trader: {handle} (Win Rate: {win_rate:.0%}, 30d ROI: {roi_30d:+.1%})
- Market: "{market_title}"
- Position: {outcome} @ ${price:.2f} implied probability
- Size: ${usd_value:,.0f} USD
- Copy Trade Recommended: {copy_trade_recommended}
```

## Output

```json
{
  "ai_summary": "string (max 280 chars, ends with 'Trade at your own risk.')"
}
```

## Forbidden Term Blocklist

Before calling GPT, scan the returned text for these terms (case-insensitive). If found, re-call GPT with an explicit re-prompt ("Do not use the word X, rephrase:"). Max 2 re-tries.

```
["gambl", "bet", "wager", "casino", "punt", "degen", "ape in", "moonshot"]
```

## Model Config

- Model: `gpt-4o`
- `max_tokens`: 150
- `temperature`: 0.3 (low for consistent, analytical tone)

## Error Handling

- **API timeout (>15s):** Return a templated fallback string:
  `"[Handle] entered a ${usd_value:,.0f} position on '{market_title}'. Trade at your own risk."`
- **Rate limit (429):** Back off 10s, retry once. If second attempt fails, use fallback.
- **Forbidden term after 2 retries:** Use fallback string.
- Log all GPT failures to `.tmp/summarizer.log`.
