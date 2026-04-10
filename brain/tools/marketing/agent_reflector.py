#!/usr/bin/env python3
"""
agent_reflector.py — VisionEdge Marketing Agent | Layer 2: Self-Annealing
Runs nightly. Fetches telemetry from DB, passes to Claude Sonnet for deep
analysis, then APPENDS new learned rules to the brain directive.

This is the self-improvement loop: the agent gets smarter every night.

Uses Claude Sonnet (Anthropic) for the reflection step — superior reasoning
quality over Hermes for synthesizing performance patterns into actionable rules.
"""
import os
import sys
import json
import logging
import requests
from datetime import datetime, timezone

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

from agent_db import get_recent_campaign_data
from metrics_fetcher import run_telemetry as _harvest_telemetry

log = logging.getLogger(__name__)

SECRETS_PATH = os.path.join(THIS_DIR, '..', '..', 'secrets.json')
BRAIN_PATH   = os.path.join(THIS_DIR, '..', '..', 'directives', 'visionedge_marketing_brain.md')
TELEMETRY_FETCH_HOURS = 24   # pull telemetry for posts older than this


def _load_secrets() -> dict:
    try:
        with open(SECRETS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Cannot load secrets.json: {e}")
        return {}


def _fetch_telemetry_from_apis(campaigns: list[dict], secrets: dict) -> list[dict]:
    """
    For each campaign, attempt to pull live engagement stats from Ayrshare.
    Falls back to stored DB values if unavailable.

    Ayrshare analytics endpoint provides impressions, likes, shares for posted content.
    """
    ayrshare_key = secrets.get("AYRSHARE_API_KEY", os.getenv("AYRSHARE_API_KEY", ""))
    enriched = []

    for c in campaigns:
        # If DB already has real telemetry, use it
        if c.get("impression_count", 0) > 0:
            enriched.append(c)
            continue

        # Otherwise, try Ayrshare analytics (if post ID was stored — future iteration)
        # For now, mark as pending and skip
        log.info(f"Campaign {c.get('id')} pending telemetry — will try Ayrshare next cycle.")
        enriched.append(c)

    return enriched


def _call_claude_reflection(campaigns: list[dict], secrets: dict) -> str | None:
    """
    Pass campaign data to Claude Sonnet for analysis.
    Returns raw markdown bullet points of new rules to append.
    """
    api_key = secrets.get("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set — cannot run reflection.")
        return None

    # Format campaign data as a readable table
    campaign_str = ""
    for c in campaigns:
        campaign_str += (
            f"\n---\n"
            f"Platform:    {c.get('platform')}\n"
            f"Ticker:      {c.get('ticker')}\n"
            f"Strategy:    {c.get('strategy_attempt')}\n"
            f"Content:     {str(c.get('content', ''))[:200]}\n"
            f"Impressions: {c.get('impression_count', 0)}\n"
            f"Engagement:  {c.get('engagement_score', 0.0):.2f}\n"
            f"Posted At:   {c.get('posted_at')}\n"
        )

    system = (
        "You are the Reflection Layer of the VisionEdge AI Autonomous Marketing Agent. "
        "Your job is to analyze recent marketing campaign performance data and extract actionable insights. "
        "Output ONLY markdown bullet points in this exact format:\n"
        "- [Rule N]: [Clear, specific, actionable rule based on the data]\n\n"
        "Rules must be:\n"
        "1. Specific (reference real patterns from the data)\n"
        "2. Actionable (describe what TO DO or NOT DO)\n"
        "3. Platform-labeled (start with the platform: X, TikTok, or Reddit)\n"
        "Output 3-7 rules maximum. No preamble, no explanation — just the bullet points."
    )

    user = (
        f"Analyze the following recent campaign performance data and extract new rules:\n\n"
        f"{campaign_str}\n\n"
        f"What patterns do you see? What should the agent do MORE of? Less of? "
        f"Generate 3-7 bullet-point rules to improve future performance."
    )

    try:
        import anthropic
        client    = anthropic.Anthropic(api_key=api_key)
        response  = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[
                {"role": "user", "content": f"System context:\n{system}\n\nTask:\n{user}"}
            ],
        )
        findings = response.content[0].text.strip()
        log.info(f"Claude reflection complete — {len(findings)} chars")
        return findings
    except Exception as e:
        log.error(f"Claude reflection failed: {e}")
        return None


def _append_to_brain(findings: str):
    """Append new rules below the divider in the brain directive."""
    try:
        ts_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        section = f"\n\n### Reflection Session — {ts_str}\n{findings}\n"

        with open(BRAIN_PATH, 'a') as f:
            f.write(section)

        log.info(f"Brain updated with {len(findings.splitlines())} new rules.")
    except Exception as e:
        log.error(f"Failed to update brain directive: {e}")


def run_reflection():
    """Full reflection cycle: harvest telemetry → fetch data → analyze → update brain."""
    log.info("=== Reflection Cycle Started ===")
    secrets = _load_secrets()

    # ── Step 1: Harvest real impression data from Twitter API ────────────────
    # This MUST run before get_recent_campaign_data() so the DB has real numbers.
    log.info("[Reflect] Step 1/3: Harvesting X/Twitter impression metrics...")
    try:
        _harvest_telemetry()
        log.info("[Reflect] Telemetry harvest complete.")
    except Exception as e:
        log.warning(f"[Reflect] Telemetry harvest failed (non-fatal): {e}")

    # ── Step 2: Pull enriched campaign data from DB ────────────────────────
    log.info("[Reflect] Step 2/3: Pulling campaign data for analysis...")
    campaigns = get_recent_campaign_data(limit=10)
    if not campaigns:
        log.info("[Reflect] No telemetry-marked campaigns yet — skipping reflection.")
        return

    log.info(f"[Reflect] Step 2/3: Analyzing {len(campaigns)} campaigns...")
    enriched = _fetch_telemetry_from_apis(campaigns, secrets)

    # ── Step 3: Claude Sonnet reflection ────────────────────────────────
    log.info("[Reflect] Step 3/3: Running Claude Sonnet analysis...")
    findings = _call_claude_reflection(enriched, secrets)
    if not findings:
        log.warning("No findings returned — brain not updated.")
        return

    _append_to_brain(findings)
    log.info("=== Reflection Cycle Complete ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [reflector] %(levelname)s: %(message)s",
    )
    run_reflection()
