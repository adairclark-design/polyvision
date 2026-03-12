#!/usr/bin/env python3
"""
test_connections.py — PolyVision Phase 2: Link
Tests every external API handshake. Prints ✅/❌ per service.
Exits with code 0 if all pass, code 1 if any fail.
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

RESULTS = []

def check(label: str, fn):
    try:
        fn()
        print(f"  ✅  {label}")
        RESULTS.append(True)
    except Exception as e:
        print(f"  ❌  {label} — {e}")
        RESULTS.append(False)


# ── Polymarket Public APIs ──────────────────────────────────────────────────

def test_gamma_api():
    r = requests.get("https://gamma-api.polymarket.com/markets?limit=1", timeout=10)
    r.raise_for_status()
    data = r.json()
    assert len(data) > 0, "Empty response"

def test_clob_api():
    r = requests.get("https://clob.polymarket.com/prices?token_id=0&side=BUY", timeout=10)
    # A 400 here is fine — it means the API is reachable but token_id is invalid
    assert r.status_code in (200, 400), f"Unexpected status: {r.status_code}"

# ── OpenAI ──────────────────────────────────────────────────────────────────

def test_openai():
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY not set in .env")
    from openai import OpenAI
    client = OpenAI(api_key=key)
    models = client.models.list()
    assert len(list(models)) > 0, "No models returned"

# ── OneSignal ────────────────────────────────────────────────────────────────

def test_onesignal():
    app_id = os.getenv("ONESIGNAL_APP_ID", "")
    api_key = os.getenv("ONESIGNAL_API_KEY", "")
    if not app_id or not api_key:
        raise ValueError("ONESIGNAL_APP_ID or ONESIGNAL_API_KEY not set in .env")
    r = requests.get(
        f"https://onesignal.com/api/v1/apps/{app_id}",
        headers={"Authorization": f"Basic {api_key}"},
        timeout=10,
    )
    r.raise_for_status()

# ── Databases (Dockerized) ───────────────────────────────────────────────────


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🔍 PolyVision — Connection Tests\n")

    print("\n── Polymarket (Public) ──")
    check("Gamma API", test_gamma_api)
    check("CLOB API", test_clob_api)

    print("\n── OpenAI ──")
    check("OpenAI GPT-4o", test_openai)

    print("\n── Push Notifications ──")
    check("OneSignal", test_onesignal)

    print("\n── Push Notifications ──")
    check("OneSignal", test_onesignal)

    passed = sum(RESULTS)
    total = len(RESULTS)
    print(f"\n{'─'*40}")
    print(f"Results: {passed}/{total} passed\n")

    if not all(RESULTS):
        print("⚠️  Fix failing connections before proceeding to Phase 3.")
        sys.exit(1)
    else:
        print("🚀 All systems go. Proceed to Phase 3: Architect.")
        sys.exit(0)
