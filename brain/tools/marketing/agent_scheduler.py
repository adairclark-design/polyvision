#!/usr/bin/env python3
from __future__ import annotations
"""
agent_scheduler.py — VisionEdge Marketing Agent | Layer 2: Cron Orchestrator
Zero-touch autonomous scheduler. Runs as a standalone daemon process.

4-Layer Trigger Funnel (evaluated every 60 seconds):
  Layer 1 — Freshness Gate (Universal):
      Reject any trade older than FRESHNESS_MAX_MINUTES. Kills stale content.
  Layer 2 — Momentum Cluster (Primary):
      3+ trades totaling $50K+ on the same market in 2h → fire immediately.
      Best engagement: "multiple whales piling in" narrative.
  Layer 3 — Single Fresh Large Trade (Secondary):
      Any fresh ($25K+, <90min old) unprocessed trade → fire immediately.
  Layer 4 — 5PM Recap Fallback:
      If 0 videos produced today by RECAP_HOUR, look back 8h for best trade
      and generate a retrospective "earlier today..." video.

Governors (applied across all layers):
  - DAILY_BURST_CAP: max videos per calendar day (default 3)
  - PER_MARKET_COOLDOWN_H: min hours between videos on the same market (default 4)

Run persistently:
  python tools/marketing/agent_scheduler.py
"""
import os
import sys
import time
import json
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo   # stdlib Python 3.9+ — handles EDT/EST automatically

# ── Path ──────────────────────────────────────────────────────────────────────
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

# ── Logging ───────────────────────────────────────────────────────────────────
log_path = os.path.join(THIS_DIR, '..', '..', '.tmp', 'marketing', 'agent.log')
os.makedirs(os.path.dirname(log_path), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_path, mode="a"),
    ],
)
log = logging.getLogger(__name__)

# ── Tunable Constants ─────────────────────────────────────────────────────────
POLL_INTERVAL_S         = 60          # Seconds between each DB poll
DAILY_BURST_CAP         = 3           # Max videos generated per calendar day
PER_MARKET_COOLDOWN_H   = 4           # Min hours before re-covering the same market
FRESHNESS_MAX_MINUTES   = 480         # Layer 1: max trade age (8h — allows held trades to survive until next peak window)
MOMENTUM_WINDOW_M       = 120         # Layer 2: rolling window to detect clusters
MOMENTUM_MIN_TRADES     = 3           # Layer 2: min individual trades to qualify
MOMENTUM_MIN_USD        = 50_000      # Layer 2: min combined USD to qualify
SINGLE_TRADE_MIN_USD    = 25_000      # Layer 3: min trade size (single trade path)
RECAP_HOUR              = 17          # Layer 4: fire recap if no videos by this hour (EST)
RECAP_LOOKBACK_H        = 8           # Layer 4: how far back the recap looks for a trade

# Peak posting windows (EST hours, inclusive). Outside these, fresh trade videos
# are held. Layer 4 recap still fires at RECAP_HOUR regardless.
# 6-9 = morning commute | 19-23 = prime evening engagement window
PEAK_HOURS_EST: set[int] = {6, 7, 8, 9, 19, 20, 21, 22, 23}


def _load_secrets() -> dict:
    secrets_path = os.path.join(THIS_DIR, '..', '..', 'secrets.json')
    try:
        with open(secrets_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load secrets: {e}")
        return {}


def _is_market_on_cooldown(market_key: str, cooldown_map: dict) -> bool:
    """Returns True if this market was covered too recently."""
    last_fired = cooldown_map.get(market_key)
    if not last_fired:
        return False
    elapsed_h = (datetime.now(timezone.utc) - last_fired).total_seconds() / 3600
    return elapsed_h < PER_MARKET_COOLDOWN_H


def _is_peak_hour(now_hour_est: int) -> bool:
    """Returns True if the current EST hour is inside a peak engagement window."""
    return now_hour_est in PEAK_HOURS_EST


def _mark_market_fired(market_key: str, cooldown_map: dict) -> None:
    cooldown_map[market_key] = datetime.now(timezone.utc)


def _today_str() -> str:
    """Returns today's date in UTC as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cluster_to_trade_dict(cluster: dict) -> dict:
    """
    Convert a momentum cluster dict into the trade dict shape that
    run_tiktok_video_for_trade() expects, so we can reuse the same pipeline.
    """
    return {
        "id":           cluster.get("representative_trade_id", ""),
        "market_id":    cluster.get("market_id", ""),
        "market_title": cluster.get("market_title", "Unknown Market"),
        "usd_value":    cluster.get("total_usd", 0),
        "price":        cluster.get("latest_price", 0.5),
        "outcome":      "YES",           # Best guess — LLM will contextualise
        "side":         "BUY",
        "source":       "Polymarket",
        "trader_handle": f"{cluster.get('trade_count', '?')} whales",
        "age_label":    "just now",
        # Signal to LLM that this is a cluster (multiple whales) not a single bet
        "_is_cluster":  True,
        "_trade_count": cluster.get("trade_count", 0),
    }


def main():
    log.info("=" * 60)
    log.info("VisionEdge Autonomous Marketing Agent — 4-LAYER FUNNEL MODE")
    log.info(f"  Burst cap:      {DAILY_BURST_CAP} videos/day")
    log.info(f"  Market cooldown:{PER_MARKET_COOLDOWN_H}h")
    log.info(f"  Freshness gate: {FRESHNESS_MAX_MINUTES}m")
    log.info(f"  Poll interval:  {POLL_INTERVAL_S}s")
    log.info("=" * 60)

    from agent_generator import run_tiktok_video_for_trade
    from agent_db        import init_db
    from agent_reflector import run_reflection
    from whale_data_fetcher import (
        fetch_recent_whale_trades,
        fetch_momentum_clusters,
        pick_best_trade,
    )

    init_db()
    secrets = _load_secrets()

    # ── Runtime state (resets on process restart — acceptable for daily cadence) ─
    video_count_by_day:  dict[str, int]      = {}   # {"2026-04-25": 2}
    market_cooldown:     dict[str, datetime] = {}   # {"market_id": last_fired_dt}
    recap_fired_today:   set[str]            = set()  # dates where recap has fired
    reflection_fired_today: set[str]         = set()  # dates where reflection has fired

    log.info(f"Polling database every {POLL_INTERVAL_S}s...")

    while True:
        try:
            today = _today_str()
            videos_today = video_count_by_day.get(today, 0)
            now_utc      = datetime.now(timezone.utc)
            # Use proper America/New_York zone — handles EDT (UTC-4) vs EST (UTC-5) automatically.
            # The previous hardcoded UTC-5 offset caused peak hours to be miscalculated by 1h
            # during daylight saving time (March–November), silently skipping evening trades.
            _eastern     = ZoneInfo("America/New_York")
            now_hour_est = datetime.now(_eastern).hour   # actual ET hour, DST-correct

            # ── Nightly Reflection (midnight EST, once per day) ──────────────────
            if now_hour_est == 0 and today not in reflection_fired_today:
                log.info("[Reflection] Midnight trigger — running nightly reflection cycle.")
                try:
                    run_reflection()
                except Exception as ref_err:
                    log.error(f"[Reflection] Cycle failed (non-fatal): {ref_err}")
                reflection_fired_today.add(today)

            # ── Daily cap check ───────────────────────────────────────────────
            if videos_today >= DAILY_BURST_CAP:
                log.debug(f"[Cap] {DAILY_BURST_CAP} videos already generated today. Sleeping.")
                time.sleep(POLL_INTERVAL_S)
                continue

            fired = False  # track if this poll cycle produced a video

            # ══════════════════════════════════════════════════════════════════
            # LAYER 2 — Momentum Cluster Detection
            # ══════════════════════════════════════════════════════════════════
            if not _is_peak_hour(now_hour_est):
                log.debug(f"[Scheduler] Off-peak ({now_hour_est}:xx EST) — holding. Peak: 6-9am, 7-11pm EST.")
            else:
                clusters = fetch_momentum_clusters(
                    min_cluster_usd = MOMENTUM_MIN_USD,
                    min_trade_count = MOMENTUM_MIN_TRADES,
                    window_minutes  = MOMENTUM_WINDOW_M,
                    max_age_minutes = FRESHNESS_MAX_MINUTES,
                )

                for cluster in clusters:
                    market_key = cluster.get("market_id") or cluster.get("market_title", "")
                    if _is_market_on_cooldown(market_key, market_cooldown):
                        log.info(f"[Layer 2] Cluster on cooldown: '{cluster['market_title'][:40]}'")
                        continue

                    log.info(
                        f"[Layer 2] MOMENTUM CLUSTER: {cluster['trade_count']} trades, "
                        f"${cluster['total_usd']:,.0f} on '{cluster['market_title'][:50]}'"
                    )
                    trade_dict = _cluster_to_trade_dict(cluster)
                    # Python-level gate: AVG(price) SQL filter can be gamed by mixed clusters.
                    # Check the actual reported price explicitly here.
                    if float(trade_dict.get("price", 0.5)) >= 0.90:
                        log.info(
                            f"[Layer 2] Skipping cluster — Market Probability "
                            f"{float(trade_dict.get('price', 0.5)):.0%} >= 90% (boring sure-thing). "
                            f"Market: '{cluster.get('market_title', '')[:50]}'"
                        )
                        continue
                    success = run_tiktok_video_for_trade(trade_dict, secrets)
                    if success:
                        _mark_market_fired(market_key, market_cooldown)
                        video_count_by_day[today] = videos_today + 1
                        videos_today += 1
                        fired = True
                        log.info(f"[Layer 2] Video generated. Total today: {videos_today}/{DAILY_BURST_CAP}")
                        if videos_today >= DAILY_BURST_CAP:
                            break
                    break  # Only fire one cluster per poll cycle

            # ══════════════════════════════════════════════════════════════════
            # LAYER 3 — Single Fresh Large Trade
            # ══════════════════════════════════════════════════════════════════
            if not fired and videos_today < DAILY_BURST_CAP and _is_peak_hour(now_hour_est):
                fresh_trades = fetch_recent_whale_trades(
                    min_usd         = SINGLE_TRADE_MIN_USD,
                    hours_back      = 8,           # Match FRESHNESS_MAX_MINUTES (480m) so held trades are picked up
                    limit           = 10,
                    max_age_minutes = FRESHNESS_MAX_MINUTES,
                )
                best = pick_best_trade(fresh_trades)

                if best:
                    # Auto-detect if this is a held trade (>90min old) and flag for recap framing
                    # so the LLM uses past-tense language instead of "BREAKING RIGHT NOW".
                    # created_at is an ISO string (see whale_data_fetcher line 160).
                    try:
                        from datetime import datetime as _dt
                        raw_ts   = best.get('created_at', '')
                        trade_dt = _dt.fromisoformat(raw_ts.replace('Z', '+00:00')) if raw_ts else now_utc
                        trade_age_m = (now_utc - trade_dt).total_seconds() / 60
                    except Exception:
                        trade_age_m = 0
                    if trade_age_m > 90:
                        best["_is_recap"] = True
                        log.info(f"[Layer 3] Trade is {trade_age_m:.0f}m old — flagged as RECAP (past-tense framing).")
                    market_key = best.get("market_id") or best.get("market_title", "")
                    if _is_market_on_cooldown(market_key, market_cooldown):
                        log.info(f"[Layer 3] Trade on cooldown: '{best['market_title'][:40]}'")
                    else:
                        log.info(
                            f"🐳 [Layer 3] FRESH WHALE TRADE: ${best['usd_value']:,.0f} "
                            f"on '{best['market_title'][:50]}'"
                        )
                        success = run_tiktok_video_for_trade(best, secrets)
                        if success:
                            _mark_market_fired(market_key, market_cooldown)
                            video_count_by_day[today] = videos_today + 1
                            videos_today += 1
                            fired = True
                            log.info(f"[Layer 3] ✅ Video generated. Total today: {videos_today}/{DAILY_BURST_CAP}")

            # ══════════════════════════════════════════════════════════════════
            # LAYER 4 — 5PM Recap Fallback (fires once/day if 0 videos so far)
            # ══════════════════════════════════════════════════════════════════
            if (
                not fired
                and videos_today == 0
                and now_hour_est >= RECAP_HOUR
                and today not in recap_fired_today
            ):
                log.info(f"[Layer 4] No videos today and it's past {RECAP_HOUR}:00 EST — running recap.")
                recap_trades = fetch_recent_whale_trades(
                    min_usd    = SINGLE_TRADE_MIN_USD,
                    hours_back = RECAP_LOOKBACK_H,
                    limit      = 10,
                    # No freshness gate for recap — we explicitly want older trades
                )
                best_recap = pick_best_trade(recap_trades)

                if best_recap:
                    # Flag the trade so the LLM uses retrospective language
                    best_recap["_is_recap"] = True
                    log.info(
                        f"📰 [Layer 4] RECAP TRADE: ${best_recap['usd_value']:,.0f} "
                        f"on '{best_recap['market_title'][:50]}' ({best_recap.get('age_label', '?')})"
                    )
                    success = run_tiktok_video_for_trade(best_recap, secrets)
                    if success:
                        recap_fired_today.add(today)
                        video_count_by_day[today] = videos_today + 1
                        log.info("[Layer 4] ✅ Recap video generated.")
                else:
                    log.info("[Layer 4] No qualifying trades in the last 8h — no recap today.")
                    recap_fired_today.add(today)  # Don't retry every minute

            elif not fired:
                log.debug("[Scheduler] No trigger fired this cycle.")

        except Exception as e:
            log.error(f"Error during polling cycle: {e}", exc_info=True)

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent daemon stopped.")
