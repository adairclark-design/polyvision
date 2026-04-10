#!/usr/bin/env python3
"""
agent_generator.py — PolyVision Marketing Agent | Layer 2: Orchestration
Reads the Brain directive, uses OpenRouter (Hermes) to generate platform-specific
whale-tracking content, then orchestrates the full execution chain:

  X post:    generate whale hook → publish (twitter)
  TikTok:    generate script → chart → TTS → Creatomate render → publish (tiktok)
  Reddit:    generate authentic post → log for human review (staged)

Product: PolyVision.app — Real-time Polymarket/Kalshi whale tracking.
CRITICAL: NO AI claims. NO chart signals. WHALE TRACKING ONLY.
"""
import os
import sys
import json
import random
import logging
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

from agent_db           import init_db, log_campaign
from chart_generator    import generate_chart
from tts_generator      import generate_voiceover
from video_factory      import create_video, _upload_to_catbox
from publisher          import post_text, post_video
from whale_data_fetcher import fetch_recent_whale_trades, pick_best_trade, format_trade_for_llm, _get_db_url
from background_generator import generate_background
from outro_generator    import generate_outro
import psycopg2

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SECRETS_PATH = os.path.join(THIS_DIR, '..', '..', 'secrets.json')
BRAIN_PATH   = os.path.join(THIS_DIR, '..', '..', 'directives', 'visionedge_marketing_brain.md')

# PolyVision logo — dynamically uploaded per cycle
POLYVISION_LOGO_PATH = os.path.join(THIS_DIR, '..', '..', 'assets', 'logo_fallback.png')

# Hot prediction market topics — used as content hooks.
# Chart visuals use mapped stock tickers as vibrant market backdrops.
HOT_TOPICS = [
    "BTC",    # Crypto price markets (massive on Polymarket)
    "ETH",    # Crypto price markets
    "TRUMP",  # Political prediction markets
    "FED",    # Fed rate decision markets
    "SPX",    # S&P 500 level markets (Kalshi)
    "OIL",    # Commodities prediction markets
    "NVDA",   # Earnings prediction markets
]

# Map topics to chart tickers for visual variety
TOPIC_TO_CHART = {
    "BTC": "BTCUSD", "ETH": "ETHUSD", "TRUMP": "SPY",
    "FED": "SPY", "SPX": "SPY", "OIL": "SPY", "NVDA": "NVDA",
}

# Words the LLM must never use (enforced with a filter)
FORBIDDEN_TERMS = [
    "ai detected", "ai spotted", "ai analysis", "ai breakdown",
    "our ai", "machine learning", "algorithm detected",
    "technical analysis", "chart pattern", "bullish signal",
    "bearish signal", "trading signal", "buy signal", "sell signal",
]


def _load_secrets() -> dict:
    try:
        with open(SECRETS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Cannot load secrets.json: {e}")
        return {}


def _read_brain() -> str:
    try:
        with open(BRAIN_PATH, 'r') as f:
            return f.read()
    except Exception as e:
        log.error(f"Cannot read brain directive: {e}")
        return ""


def _is_safe_content(text: str) -> bool:
    """Returns False if the content contains any forbidden AI/signal terms."""
    lower = text.lower()
    for term in FORBIDDEN_TERMS:
        if term in lower:
            log.warning(f"Content safety check FAILED — found: '{term}'")
            return False
    return True


# ── LLM: OpenRouter (Hermes) ──────────────────────────────────────────────────
def _call_openrouter(system: str, user: str, secrets: dict, max_retries: int = 3) -> dict | None:
    """
    Calls OpenRouter with nousresearch/hermes-3-llama-3.1-70b.
    Includes an active exponential backoff loop to catch 429 Rate Limits and 502 Bad Gateways.
    Returns parsed JSON dict, or None on critical failure.
    """
    import requests
    import time
    api_key = secrets.get("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))
    if not api_key:
        log.error("OPENROUTER_API_KEY not set.")
        return None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization":  f"Bearer {api_key}",
                    "Content-Type":   "application/json",
                    "HTTP-Referer":   "https://polyvision.app",
                    "X-Title":        "PolyVision Marketing Agent",
                },
                json={
                    "model":       "nousresearch/hermes-3-llama-3.1-70b",
                    "messages":    [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature":     0.85,
                    "max_tokens":      800,
                },
                timeout=45,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            return json.loads(raw)
            
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                log.warning(f"OpenRouter 429 Too Many Requests. (Attempt {attempt}/{max_retries})")
            else:
                log.warning(f"OpenRouter HTTP Error: {e} (Attempt {attempt}/{max_retries})")
        except Exception as e:
            log.warning(f"OpenRouter System Error: {e} (Attempt {attempt}/{max_retries})")
            
        if attempt < max_retries:
            delay = 15 * attempt
            log.info(f"⏳ Initiating exponential backoff: Waiting {delay} seconds before retry...")
            time.sleep(delay)
    
    log.error("❌ CRITICAL: OpenRouter rate limit sustained. All backup attempts failed.")
    return None


# ── X (Twitter) Post Generation ───────────────────────────────────────────────
def run_x_post(topic: str, secrets: dict, dry_run: bool = False) -> bool:
    """Generate and publish an X post based on a REAL recent whale trade."""
    brain = _read_brain()

    # ── Fetch real trade data from PolyVision DB ───────────────────────────────
    trades    = fetch_recent_whale_trades(min_usd=25_000, hours_back=72, limit=20)
    best      = pick_best_trade(trades)
    trade_ctx = format_trade_for_llm(best) if best else None

    if trade_ctx:
        log.info(f"[X] Using REAL trade: ${best['usd_value']:,.0f} on '{best['market_title'][:50]}'")
        data_block = f"\n\n{trade_ctx}\n"
    else:
        log.warning("[X] No real trades found — LLM will write general whale-tracking content.")
        data_block = f"Write about a compelling recent prediction market trend related to {topic}."

    system = (
        "You are the PolyVision X (Twitter) marketing agent. "
        "PolyVision tracks WHALE TRADES on Polymarket and Kalshi in real time — there is NO AI, no chart analysis. "
        "Follow ALL rules in the brain directive STRICTLY. "
        "NEVER mention AI, machine learning, or trading signals. "
        "ALWAYS focus on: whale bets, dollar amounts, smart money, real-time tracking, FOMO. "
        "If real trade data is provided, you MUST use those EXACT dollar amounts and market names. "
        "NEVER invent or change the numbers. "
        "Output JSON only with keys: 'strategy' (1 sentence) and 'content' (tweet text, max 275 chars)."
    )
    user = (
        f"Brain directive:\n\n{brain}\n\n{data_block}\n"
        f"Generate a punchy X post about this whale bet. "
        f"LEAD with the EXACT dollar amount. Create massive FOMO. "
        f"CTA must be '→ polyvision.app'. "
        f"Include hashtags: #Polymarket and/or #PredictionMarkets. "
        f"Return JSON: {{\"strategy\": \"...\", \"content\": \"...\"}}"
    )

    result = _call_openrouter(system, user, secrets)
    if not result:
        return False

    strategy = result.get("strategy", "")
    content  = result.get("content", "")
    if not content:
        log.error("LLM returned empty content for X post.")
        return False

    # Safety filter
    if not _is_safe_content(content):
        log.error("X post failed safety filter — skipping.")
        return False

    content = content[:280]
    log.info(f"[X] Strategy: {strategy}")
    log.info(f"[X] Content: {content}")

    pub_result = post_text("twitter", content, dry_run=dry_run)
    success    = pub_result.get("status") not in ("error",)

    log_campaign(platform="twitter", ticker=topic, strategy=strategy, content=content)
    return success


# ── TikTok Video Delivery (Hybrid Email) ──────────────────────────────────────
import base64
import requests
import io

def deliver_tiktok_package_email(best: dict, video_url: str, caption: str, script_text: str):
    """Downloads the MP4 and sends it directly to Inbox natively for manual upload."""
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    TARGET_EMAIL   = os.getenv("BRIEFING_EMAIL_TO", "adair.clark@gmail.com")
    RESEND_FROM    = os.getenv("RESEND_FROM", "onboarding@resend.dev")

    if not RESEND_API_KEY:
        log.error("Missing RESEND_API_KEY, cannot dispatch video package.")
        return

    log.info(f"[Email] Downloading final MP4 from {video_url}...")
    try:
        mp4_resp = requests.get(video_url, timeout=60)
        mp4_resp.raise_for_status()
        mp4_b64 = base64.b64encode(mp4_resp.content).decode('utf-8')
    except Exception as e:
        log.error(f"[Email] Failed to download MP4 from Creatomate URL: {e}")
        return

    html_content = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; color: #111827;">
        <h2 style="color: #ea4335;">🎥 Autogenerated TikTok / Reels Package</h2>
        <p>A massive algorithmic trade has hit the tape! Your high-fidelity 1080p video asset is attached as an `.mp4`.</p>
        
        <p><strong>Voiceover Script Context:</strong></p>
        <div style="background: #f3f4f6; padding: 16px; border-radius: 8px; margin-bottom: 24px; font-style: italic;">
            "{script_text}"
        </div>
        
        <p><strong>Post Caption (Copy & Paste):</strong></p>
        <div style="background: #f3f4f6; padding: 16px; border-radius: 8px;">
            {caption}
        </div>

        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;" />
        <p style="font-size: 13px; color: #6b7280;">
            <strong>How to post:</strong> Download the <code>.mp4</code> straight to your iPhone. Open TikTok or Instagram Reels, select the video, tap <em>'Add Sound'</em>, search for a trending native track, and turn it down to 1% volume. Then just paste the caption above.
        </p>
    </div>
    """

    data = {
        "from": RESEND_FROM,
        "to": [TARGET_EMAIL],
        "subject": f"TikTok Package: ${best.get('usd_value', 0):,.0f} moved on {best.get('market_title', 'Unknown Market')[:30]}...",
        "html": html_content,
        "attachments": [
            {
                "content": mp4_b64,
                "filename": "viral_whale_trade.mp4"
            }
        ]
    }

    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json=data,
            timeout=30
        )
        r.raise_for_status()
        log.info("[Email] ✅ Full Video Delivery Package successfully dispatched to inbox.")
    except Exception as e:
        log.error(f"[Email] Failed to email package: {e}")

def run_tiktok_video_for_trade(best: dict, secrets: dict, dry_run: bool = False) -> bool:
    """
    Hybrid Pipeline — generates a FRESH video for a REAL trade:
      1. New whale-tracking script grounded in REAL data (LLM)
      2. New market chart PNG (live Polygon data)
      3. New TTS voiceover (OpenAI)
      4. New Creatomate render with PolyVision logo (NO BACKGROUND MUSIC)
      5. Directly email MP4 payload to User via Resend.
    """
    brain = _read_brain()
    topic = best.get("market_title", "General")
    trade_ctx = format_trade_for_llm(best)
    
    # Phase 2: Telemetry Injection Matrix
    db_url = _get_db_url()
    rl_context = ""
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT theme, upvotes, impressions FROM video_history ORDER BY (upvotes + impressions) DESC LIMIT 3")
        top_history = cur.fetchall()
        if top_history:
            rl_context = "\n\nSYSTEM INTELLIGENCE (REINFORCEMENT LEARNING):\n"
            rl_context += "Here is the exact algorithmic engagement metrics of your past video aesthetic themes:\n"
            for t_theme, t_upvotes, t_impressions in top_history:
                rl_context += f" - Theme '{t_theme}' yielded {t_upvotes} upvotes & {t_impressions} impressions.\n"
            
            # Epsilon-Greedy (80/20) Math Branch
            if random.random() <= 0.80:
                rl_context += "MANDATORY RULE [EXPLOIT MODE]: Analyze what themes scored the highest. You must explicitly emulate the tone and aesthetic logic of the highest performing themes in your current script generation to maximize viral engagement."
            else:
                rl_context += "MANDATORY RULE [EXPLORE MODE]: You are in EXPLORATION MODE. You must actively IGNORE these top-performing themes. Hallucinate a radically experimental and completely contrarian cinematic aesthetic to search for completely new viral engagement avenues."
        conn.close()
    except Exception as e:
         log.warning(f"Telemetry extraction failure: {e}")

    log.info(f"[TikTok] Using REAL trade: ${best['usd_value']:,.0f} on '{best['market_title'][:50]}'")
    data_block = f"\n\n{trade_ctx}\n"

    system = (
        "You are the PolyVision TikTok content agent. "
        "PolyVision tracks whale trades on Polymarket and Kalshi IN REAL TIME. No AI. No chart signals. "
        "NEVER say AI, machine learning, chart pattern, or trading signal. EVER. "
        "Focus exclusively on: shocking dollar amounts, whale tracking, FOMO, copying smart money, transparency. "
        "If real trade data is provided, use those EXACT dollar amounts and market names in the script. "
        "NEVER invent or change the numbers. The viewer may fact-check these — accuracy is critical. "
        "Apply ALL TikTok rules from the brain directive. "
        f"{rl_context}"
        "Return JSON only: 'strategy' (1 sentence), 'voiceover_script' (35-50 words, urgent tone), "
        "'caption' (max 8 words for video overlay text, MUST include the exact dollar amount and market), 'tiktok_post_text' (max 150 chars for description)."
    )
    user = (
        f"Brain directive:\n\n{brain}\n\n{data_block}\n"
        f"Generate a TikTok video script based on this REAL whale trade. "
        f"HOOK (first 2 seconds): use the EXACT dollar amount from the data above. "
        f"CAPTION: Make the 8-word caption explicitly state the dollar amount and what they bet on (e.g. '$129k Bet on Celtics!'). Do NOT use emojis. "
        f"BUILD: explain what the whale bet on and why it's massive. "
        f"CTA: 'Track every whale move free at polyvision.app' "
        f"Keep total script under 50 words for a fast, punchy 15-20 second video. "
        f"Return JSON: 'strategy', 'voiceover_script', 'caption', 'tiktok_post_text'."
    )

    result = _call_openrouter(system, user, secrets)
    if not result:
        return False

    strategy         = result.get("strategy", "")
    voiceover_script = result.get("voiceover_script", "")
    caption          = result.get("caption", "Track the Smart Money 🐳")
    post_text_str    = result.get("tiktok_post_text", f"Whale tracking on {topic} → polyvision.app")

    # Safety filter on the script
    if not _is_safe_content(voiceover_script):
        log.warning("[TikTok] Script failed safety filter — sanitizing.")
        for term in FORBIDDEN_TERMS:
            voiceover_script = voiceover_script.replace(term, "whale tracking")

    if not voiceover_script:
        log.error("LLM returned empty voiceover script.")
        return False

    log.info(f"[TikTok] Strategy: {strategy}")
    log.info(f"[TikTok] Script: {voiceover_script[:100]}...")

    # Step 2: Generate FRESH graphic mapping the real odds of the trade
    log.info(f"[TikTok] Generating visual odds graphic for the trade...")
    chart_path = generate_chart(best)
    if not chart_path:
        log.error("Graphic generation failed.")
        return False

    # Step 3: Generate FRESH voiceover
    log.info("[TikTok] Generating voiceover...")
    audio_path = generate_voiceover(voiceover_script)
    if not audio_path:
        log.error("TTS generation failed.")
        return False

    # Step 4: Generate Graphic Backend Matrix
    bg_path = generate_background()
    bg_url = _upload_to_catbox(bg_path)
    outro_path = generate_outro()

    # Step 5: Render FRESH video with PolyVision tools
    log.info("[TikTok] Rendering High-Def video via Creatomate...")
    video_url = None
    if not dry_run:
        video_url = create_video(
            chart_image_path=chart_path,
            audio_path=audio_path,
            caption=caption,
            bg_image_url=bg_url,
            logo_path=outro_path,
        )
        if not video_url:
            log.error("Video render failed.")
            return False
            
        # SQL Memory Storage
        if best and best.get("id"):
            try:
                # Use actual background type as RL theme signal
                theme_str = "Kling Kinetic" if bg_path and bg_path.endswith(".mp4") else "DALL-E Cinematic"
                conn = psycopg2.connect(db_url)
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO video_history (trade_id, theme) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (best.get('id'), theme_str)
                )
                conn.commit()
                conn.close()
                log.info(f"[Memory] Trade {best.get('id')} logged to video_history with theme '{theme_str}'.")
            except Exception as e:
                log.warning(f"Telemetry logging constraint fail: {e}")
                
    else:
        log.info(f"[DRY-RUN] Skipping Creatomate render. chart={chart_path} audio={audio_path}")
        video_url = "DRY_RUN_VIDEO_URL"

    # Step 6: Dispatch MP4 directly to Inbox! (No API posting)
    if not dry_run:
        deliver_tiktok_package_email(best, video_url, post_text_str, voiceover_script)

    log_campaign(
        platform="tiktok_hybrid", ticker=topic, strategy=strategy,
        content=post_text_str, video_url=video_url if not dry_run else "",
        chart_image_url=chart_path,
    )
    return True


# ── Reddit Post Generation (staged — manual review) ───────────────────────────
def run_reddit_draft(topic: str, secrets: dict) -> bool:
    """
    Generate a Reddit post draft and save to .tmp/marketing/reddit_drafts.jsonl.
    NOT published automatically — requires human review per brain directive.
    """
    brain  = _read_brain()
    system = (
        "You are the PolyVision Reddit content agent. "
        "PolyVision tracks whale trades on Polymarket and Kalshi. No AI, no chart signals. "
        "Be authentic — lead with DATA VALUE. Post as a genuine prediction market enthusiast. "
        "Mention PolyVision naturally as the tool you use to track whale activity. "
        "Output JSON only: 'strategy', 'subreddit', 'title', 'body'."
    )
    user = (
        f"Brain directive:\n\n{brain}\n\n"
        f"Generate a Reddit post about interesting whale activity in the {topic} prediction market. "
        f"Pick an appropriate subreddit from: r/Polymarket, r/PredictionMarkets, r/KalshiMarkets, r/algotrading. "
        f"Title: curious, data-driven — NOT promotional. "
        f"Body: real value + mention polyvision.app as how you spotted the whale activity. "
        f"Return JSON: {{\"strategy\": \"\", \"subreddit\": \"\", \"title\": \"\", \"body\": \"\"}}"
    )

    result = _call_openrouter(system, user, secrets)
    if not result:
        return False

    drafts_path = os.path.join(THIS_DIR, '..', '..', '.tmp', 'marketing', 'reddit_drafts.jsonl')
    Path(drafts_path).parent.mkdir(parents=True, exist_ok=True)

    draft = {
        "ts":        datetime.now(timezone.utc).isoformat(),
        "topic":     topic,
        "subreddit": result.get("subreddit", "r/Polymarket"),
        "strategy":  result.get("strategy", ""),
        "title":     result.get("title", ""),
        "body":      result.get("body", ""),
    }
    with open(drafts_path, "a") as f:
        f.write(json.dumps(draft) + "\n")

    log.info(f"[Reddit] Draft saved → {drafts_path}")
    log.info(f"[Reddit] Subreddit: {draft['subreddit']} | Title: {draft['title'][:60]}")

    log_campaign(
        platform="reddit", ticker=topic, strategy=draft["strategy"],
        content=f"{draft['title']}\n\n{draft['body']}",
    )
    return True


# ── Main Entrypoint ───────────────────────────────────────────────────────────
def run_generation_cycle(dry_run: bool = False):
    """
    Single generation cycle:
      - Picks a random hot prediction market topic
      - Creates a BRAND NEW unique video (fresh chart, fresh script, fresh audio)
      - Publishes across X, TikTok, Reddit (draft)
    Runs at 9AM and 6PM daily via agent_scheduler.py.
    """
    secrets = _load_secrets()
    topic   = random.choice(HOT_TOPICS)
    log.info(f"=== PolyVision Generation Cycle Started | Topic: {topic} ===")

    init_db()

    try:
        log.info("--- Running X Post ---")
        run_x_post(topic, secrets, dry_run=dry_run)
    except Exception as e:
        log.error(f"X post cycle failed: {e}")

    try:
        log.info("--- Running TikTok Video ---")
        run_tiktok_video(topic, secrets, dry_run=dry_run)
    except Exception as e:
        log.error(f"TikTok cycle failed: {e}")

    try:
        log.info("--- Running Reddit Draft ---")
        run_reddit_draft(topic, secrets)
    except Exception as e:
        log.error(f"Reddit draft cycle failed: {e}")

    log.info(f"=== Generation Cycle Complete | Topic: {topic} ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [generator] %(levelname)s: %(message)s",
    )
    dry = "--dry-run" in sys.argv
    run_generation_cycle(dry_run=dry)
