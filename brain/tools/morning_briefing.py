#!/usr/bin/env python3
"""
morning_briefing.py — PolyVision Layer 3 Tool
Generates and delivers the daily "Morning Alpha" briefing.

Reads the last 24h of whale alerts from Redis, calls GPT-4o to produce a
3-section formatted report, then delivers it via:
  - SendGrid email
  - Discord webhook embed
  - Dashboard GET /briefing/latest endpoint (stores in Redis)

Env vars (all in .env):
    SENDGRID_API_KEY      — SG.xxx...
    BRIEFING_EMAIL_FROM   — verified sender address
    BRIEFING_EMAIL_TO     — comma-separated recipient(s)
    BRIEFING_HOUR_EST     — integer hour to send (default 8 → 08:00 AM EST)
    DISCORD_BOT_TOKEN     — existing config
    DISCORD_CHANNEL_ID    — existing config
    REDIS_URL             — existing config
    OPENAI_API_KEY        — existing config

Usage:
    python tools/morning_briefing.py --test     # generate, print, don't send
    python tools/morning_briefing.py --send     # generate and deliver now
    python tools/morning_briefing.py --preview  # return JSON for API endpoint
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timezone
from collections import defaultdict

import requests
import redis as redis_lib
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
REDIS_URL           = os.getenv("REDIS_URL", "redis://localhost:6379/0")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
SENDGRID_API_KEY    = os.getenv("SENDGRID_API_KEY", "")
BRIEFING_EMAIL_FROM = os.getenv("BRIEFING_EMAIL_FROM", "")
BRIEFING_EMAIL_TO   = os.getenv("BRIEFING_EMAIL_TO", "")
DISCORD_BOT_TOKEN   = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID  = os.getenv("DISCORD_CHANNEL_ID", "")

CACHE_KEY           = "cache:last100trades"
BRIEFING_CACHE_KEY  = "briefing:latest"
BRIEFING_TTL        = 86400 * 2   # keep last briefing for 48h
WINDOW_HOURS        = 24

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [briefing] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


# ── Redis helpers ─────────────────────────────────────────────────────────────
def _redis():
    return redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)


# ── Data Collection ───────────────────────────────────────────────────────────
def fetch_24h_trades() -> list[dict]:
    """Return all whale alerts from the last 24 hours."""
    r = _redis()
    window_start = time.time() - (WINDOW_HOURS * 3600)
    raw = r.zrangebyscore(CACHE_KEY, window_start, "+inf")
    trades = []
    for item in raw:
        try:
            trades.append(json.loads(item))
        except json.JSONDecodeError:
            continue
    log.info(f"Fetched {len(trades)} trades from last {WINDOW_HOURS}h")
    return trades


def analyze_trades(trades: list[dict]) -> dict:
    """Derive structured stats from raw trades for the GPT prompt."""
    if not trades:
        return {}

    total_volume   = sum(t.get("usd_value", 0) for t in trades)
    whale_trades   = [t for t in trades if t.get("alert_tier") == "WHALE"]
    cluster_trades = [t for t in trades if t.get("alert_tier") == "CLUSTER"]

    # Market volume map
    market_volume: dict[str, float] = defaultdict(float)
    market_direction: dict[str, dict]  = defaultdict(lambda: {"YES": 0.0, "NO": 0.0})
    wallet_trades:  dict[str, list]   = defaultdict(list)

    for t in trades:
        mkt = t.get("market_title", "Unknown")[:60]
        val = float(t.get("usd_value", 0))
        out = t.get("outcome", "YES").upper()
        wallet = t.get("wallet_address", "")
        handle = t.get("trader_handle", wallet[:10])

        market_volume[mkt]           += val
        market_direction[mkt][out]   += val
        if wallet:
            wallet_trades[wallet].append({
                "handle": handle,
                "market": mkt,
                "outcome": out,
                "usd_value": val,
                "win_rate": t.get("wallet_win_rate"),
            })

    # Top 5 markets by volume
    top_markets = sorted(market_volume.items(), key=lambda x: x[1], reverse=True)[:5]

    # Top 3 wallets by total traded volume
    wallet_vols = {
        w: sum(t["usd_value"] for t in ts)
        for w, ts in wallet_trades.items()
    }
    top_wallet_addrs = sorted(wallet_vols, key=lambda x: wallet_vols[x], reverse=True)[:3]
    top_wallets = []
    for addr in top_wallet_addrs:
        ts = wallet_trades[addr]
        top_wallets.append({
            "handle":    ts[0]["handle"],
            "volume":    wallet_vols[addr],
            "trades":    len(ts),
            "win_rate":  ts[0].get("win_rate"),
            "markets":   list({t["market"] for t in ts})[:2],
        })

    # Overall directional sentiment (YES vs NO by volume)
    yes_vol = sum(t.get("usd_value", 0) for t in trades if t.get("outcome", "").upper() == "YES")
    no_vol  = sum(t.get("usd_value", 0) for t in trades if t.get("outcome", "").upper() == "NO")

    return {
        "trade_count":     len(trades),
        "whale_count":     len(whale_trades),
        "cluster_count":   len(cluster_trades),
        "total_volume":    total_volume,
        "yes_volume":      yes_vol,
        "no_volume":       no_vol,
        "top_markets":     top_markets,
        "top_wallets":     top_wallets,
        "market_direction": dict(market_direction),
    }


# ── GPT-4o Report Generation ─────────────────────────────────────────────────
def generate_report(stats: dict, date_str: str) -> str:
    """Call GPT-4o to write the Morning Alpha Report."""
    if not OPENAI_API_KEY:
        return _fallback_report(stats, date_str)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        top_mkts_str = "\n".join(
            f"  • \"{m}\" — ${v:,.0f}" for m, v in stats["top_markets"]
        )
        top_wallets_str = "\n".join(
            f"  • {w['handle']} — ${w['volume']:,.0f} across {w['trades']} trades"
            + (f" (Win Rate: {w['win_rate']:.0%})" if w.get("win_rate") else "")
            for w in stats["top_wallets"]
        )
        sentiment = "bullish (YES-heavy)" if stats["yes_volume"] > stats["no_volume"] else "bearish (NO-heavy)"

        prompt = f"""You are PolyVision's AI analyst. Write the Morning Alpha Report for {date_str}.

DATA FROM THE LAST 24 HOURS:
- Total whale alerts: {stats['trade_count']} trades | ${stats['total_volume']:,.0f} total volume
- WHALE-tier: {stats['whale_count']} | CLUSTER events: {stats['cluster_count']}
- Overall smart-money sentiment: {sentiment}
- YES volume: ${stats['yes_volume']:,.0f} | NO volume: ${stats['no_volume']:,.0f}

TOP MARKETS BY VOLUME:
{top_mkts_str}

TOP WALLETS TO WATCH:
{top_wallets_str}

Write EXACTLY 3 paragraphs, each clearly labeled. Use Bloomberg Terminal tone — authoritative, analytical, concise. Avoid "gambling" or "betting." Use em-dashes, percentages, and dollar figures liberally.

PARAGRAPH 1 — OVERNIGHT ROTATION (2-3 sentences): Summarize the macro flow. Where did smart money rotate? What is the dominant directional bet?
PARAGRAPH 2 — TOP 3 WALLETS TO WATCH TODAY (name each wallet, what they traded, why they matter): Explain what these wallets did and why subscribers should follow them today.
PARAGRAPH 3 — KEY MARKETS & SIGNAL (2-3 sentences): Which 2-3 markets had the most institutional conviction? What does that signal for the day ahead?

End with this line exactly: "— PolyVision Alpha, {date_str}"
"""
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a professional prediction market analyst writing a morning briefing for sophisticated traders."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=600,
            temperature=0.45,
            timeout=30,
        )
        return resp.choices[0].message.content.strip()

    except Exception as e:
        log.error(f"GPT report generation failed: {e}")
        return _fallback_report(stats, date_str)


def _fallback_report(stats: dict, date_str: str) -> str:
    top = stats.get("top_markets", [])
    top_mkt = top[0][0] if top else "unknown markets"
    top_vol = top[0][1] if top else 0
    wallets = stats.get("top_wallets", [])
    w_names = ", ".join(w["handle"] for w in wallets[:3]) or "multiple wallets"
    sentiment = "predominantly bullish" if stats.get("yes_volume", 0) > stats.get("no_volume", 0) else "predominantly bearish"

    return f"""PARAGRAPH 1 — OVERNIGHT ROTATION
Smart money was {sentiment} over the last 24 hours — ${stats.get('total_volume', 0):,.0f} in total volume across {stats.get('trade_count', 0)} alerts. The heaviest concentration was in "{top_mkt}" with ${top_vol:,.0f} deployed.

PARAGRAPH 2 — TOP 3 WALLETS TO WATCH TODAY
The wallets commanding attention today are {w_names}. These accounts collectively moved the most capital and have historically elevated win rates, making their next moves high-priority signals.

PARAGRAPH 3 — KEY MARKETS & SIGNAL
The markets showing the strongest institutional conviction are the top-volume plays identified above. Elevated cluster activity ({stats.get('cluster_count', 0)} cluster event(s)) suggests coordinated smart-money positioning — watch for continuation today.

— PolyVision Alpha, {date_str}"""


# ── Delivery: Email via SendGrid ──────────────────────────────────────────────
def send_email(report: str, stats: dict, date_str: str) -> bool:
    if not SENDGRID_API_KEY or not BRIEFING_EMAIL_FROM or not BRIEFING_EMAIL_TO:
        log.warning("SendGrid not configured — skipping email delivery.")
        return False

    recipients = [e.strip() for e in BRIEFING_EMAIL_TO.split(",") if e.strip()]
    html_body = _report_to_html(report, stats, date_str)

    payload = {
        "personalizations": [
            {"to": [{"email": e} for e in recipients]}
        ],
        "from": {"email": BRIEFING_EMAIL_FROM, "name": "PolyVision Alpha"},
        "subject": f"📊 Morning Alpha Report — {date_str}",
        "content": [
            {"type": "text/plain", "value": report},
            {"type": "text/html",  "value": html_body},
        ],
    }

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 202):
            log.info(f"✅ Email sent to {recipients}")
            return True
        else:
            log.error(f"SendGrid error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Email delivery error: {e}")
        return False


def _report_to_html(report: str, stats: dict, date_str: str) -> str:
    paragraphs = report.split("\n\n")
    paras_html = "".join(
        f'<p style="margin:0 0 16px;line-height:1.7;">{p.replace(chr(10), "<br>")}</p>'
        for p in paragraphs if p.strip()
    )
    top_mkts_rows = "".join(
        f'<tr><td style="padding:6px 12px;color:#e2e8f0;">{m[:50]}</td>'
        f'<td style="padding:6px 12px;color:#00ffa3;font-weight:700;text-align:right;">${v:,.0f}</td></tr>'
        for m, v in stats.get("top_markets", [])
    )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>PolyVision Morning Alpha — {date_str}</title></head>
<body style="margin:0;padding:0;background:#0d0f14;font-family:'Helvetica Neue',Arial,sans-serif;color:#e2e8f0;">
  <div style="max-width:620px;margin:0 auto;padding:32px 16px;">
    <div style="background:linear-gradient(135deg,#0d2a1e,#1a0d2e);border:1px solid rgba(0,255,163,0.25);border-radius:12px;padding:24px 28px;margin-bottom:24px;">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px;">
        <span style="font-size:26px;">⚡</span>
        <span style="font-size:22px;font-weight:800;color:#00ffa3;letter-spacing:-0.5px;">PolyVision Alpha</span>
      </div>
      <div style="font-size:13px;color:#8899aa;">Morning Alpha Report · {date_str}</div>
    </div>
    <div style="background:#13161d;border:1px solid #1e2433;border-radius:12px;padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:#556677;margin-bottom:16px;text-transform:uppercase;">Today's Intelligence</div>
      {paras_html}
    </div>
    <div style="background:#13161d;border:1px solid #1e2433;border-radius:12px;padding:20px 28px;margin-bottom:20px;">
      <div style="font-size:11px;font-weight:700;letter-spacing:1.5px;color:#556677;margin-bottom:12px;text-transform:uppercase;">Top Markets by Volume</div>
      <table style="width:100%;border-collapse:collapse;">{top_mkts_rows}</table>
    </div>
    <div style="text-align:center;font-size:10px;color:#4a5568;padding:16px 0;">
      PolyVision · Smart money signals · This is not financial advice
    </div>
  </div>
</body>
</html>"""


# ── Delivery: Discord ─────────────────────────────────────────────────────────
def send_discord(report: str, stats: dict, date_str: str) -> bool:
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        log.info("Discord not configured — skipping.")
        return False

    paragraphs = [p.strip() for p in report.split("\n\n") if p.strip()]
    fields = []
    for para in paragraphs[:3]:
        lines = para.split("\n", 1)
        name  = lines[0].replace("PARAGRAPH", "").strip().lstrip("123— ").strip()
        value = lines[1].strip() if len(lines) > 1 else lines[0]
        fields.append({"name": f"▸ {name}", "value": value[:1024], "inline": False})

    embed = {
        "title": f"⚡ Morning Alpha Report — {date_str}",
        "color": 0x00FFA3,
        "fields": fields,
        "footer": {"text": f"PolyVision · {stats.get('trade_count',0)} alerts · ${stats.get('total_volume',0):,.0f} volume past 24h"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = requests.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"embeds": [embed]},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            log.info("✅ Discord briefing sent.")
            return True
        else:
            log.warning(f"Discord error {resp.status_code}: {resp.text[:100]}")
            return False
    except Exception as e:
        log.error(f"Discord delivery error: {e}")
        return False


# ── Redis Store ───────────────────────────────────────────────────────────────
def cache_briefing(report: str, stats: dict, date_str: str):
    """Store the latest briefing in Redis so the dashboard can fetch it."""
    try:
        r = _redis()
        payload = json.dumps({
            "report":     report,
            "stats":      stats,
            "date_str":   date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
        r.setex(BRIEFING_CACHE_KEY, BRIEFING_TTL, payload)
        log.info("Briefing cached in Redis.")
    except Exception as e:
        log.warning(f"Redis cache failed: {e}")


# ── Main Entry ────────────────────────────────────────────────────────────────
def run_briefing(dry_run: bool = False) -> dict:
    """
    Full pipeline: fetch → analyze → generate → deliver.
    Returns the briefing dict (for use as API response).
    """
    date_str = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y")
    log.info(f"🌅 Starting Morning Alpha Briefing for {date_str}")

    trades = fetch_24h_trades()
    if not trades:
        log.warning("No trades in last 24h — sending minimal briefing.")
        stats = {"trade_count": 0, "total_volume": 0, "top_markets": [], "top_wallets": [],
                 "whale_count": 0, "cluster_count": 0, "yes_volume": 0, "no_volume": 0}
    else:
        stats = analyze_trades(trades)

    report = generate_report(stats, date_str)

    result = {
        "report":       report,
        "stats":        stats,
        "date_str":     date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if not dry_run:
        cache_briefing(report, stats, date_str)
        email_ok   = send_email(report, stats, date_str)
        discord_ok  = send_discord(report, stats, date_str)
        result["delivered"] = {"email": email_ok, "discord": discord_ok}
        log.info(f"Briefing delivered — email:{email_ok} discord:{discord_ok}")
    else:
        log.info("Dry run — no delivery.")
        result["delivered"] = {"email": False, "discord": False}

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",    action="store_true", help="Generate and print, no delivery")
    parser.add_argument("--send",    action="store_true", help="Generate and deliver now")
    parser.add_argument("--preview", action="store_true", help="Print JSON result")
    args = parser.parse_args()

    dry = not args.send
    result = run_briefing(dry_run=dry)

    if args.preview:
        print(json.dumps(result, indent=2))
    else:
        print("\n" + "═" * 60)
        print(result["report"])
        print("═" * 60)
        if args.send:
            d = result.get("delivered", {})
            print(f"\n✅ Email: {'sent' if d.get('email') else 'skipped/failed'}")
            print(f"✅ Discord: {'sent' if d.get('discord') else 'skipped/failed'}")
