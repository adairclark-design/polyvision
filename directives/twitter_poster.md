# Twitter Auto-Poster

## Goal
Auto-post PolyVision whale trade alerts to @PolyVisionApp on X (Twitter) every time a qualifying trade is detected. Free organic marketing using live data.

## Tools
- `tools/twitter_poster.py` — formats tweets, posts via tweepy API v2, Redis deduplication

## Required Env Vars (Railway)
| Variable | Description |
|---|---|
| `TWITTER_API_KEY` | App Consumer Key from developer.twitter.com |
| `TWITTER_API_KEY_SECRET` | App Consumer Secret |
| `TWITTER_ACCESS_TOKEN` | Account Access Token (for @PolyVisionApp) |
| `TWITTER_ACCESS_TOKEN_SECRET` | Account Access Token Secret |
| `TWITTER_MIN_SIZE` | Min USD for Polymarket trades (default: 50000) |
| `TWITTER_KALSHI_MIN_SIZE` | Min USD for Kalshi trades (default: 5000) |

## Thresholds
- **Polymarket**: `$50,000+` (configurable via `TWITTER_MIN_SIZE`)
- **Kalshi**: `$5,000+` (configurable via `TWITTER_KALSHI_MIN_SIZE`)

## Tweet Format
```
🐋 $127,000 YES on "Will the Fed cut rates in June?"
📊 72% probability · Polymarket

Live whale tracking → polyvision.app

#PredictionMarkets #Polymarket
```

## Rate Limits
- Twitter API v2 Free: 1,500 tweets/month ($0)
- Redis deduplication: same market won't tweet again within 15 minutes

## Testing
```bash
# Dry-run (format only, no post)
python tools/twitter_poster.py --test

# Test with real payload
echo '{"usd_value": 75000, "outcome": "YES", ...}' | python tools/twitter_poster.py --dry-run
```

## Common Errors
| Error | Fix |
|---|---|
| `401 Unauthorized` | Check app permissions are set to "Read and Write" in developer.twitter.com |
| `403 Forbidden` | App may be in read-only mode — regenerate Access Token after changing permissions |
| `tweepy not found` | Run `pip install tweepy>=4.14.0,<5.0.0` or redeploy Railway |
| `tweepy.errors.TweepyException: duplicate content` | Same tweet sent twice — Redis dedup should prevent this |

## Self-Annealing Log
- 2026-03-26: Initial implementation. Uses tweepy.Client (API v2) with OAuth 1.0a. Thresholds: $50K Polymarket, $5K Kalshi. Redis deduplication TTL: 15 minutes. Graceful skip if credentials not set.
