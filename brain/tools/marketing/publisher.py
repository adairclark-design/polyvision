#!/usr/bin/env python3
"""
publisher.py — PolyVision Marketing Agent | Layer 3: Publishing
Routes finished content to social platforms:

  X (Twitter)       → PolyVision's twitter_poster.py (Railway) OR Tweepy direct
  TikTok            → Upload-Post API  ─┐
  YouTube Shorts    → Upload-Post API   ├─ All three in one API call
  Instagram Reels   → Upload-Post API  ─┘

Upload-Post API Reference:
  Endpoint: POST https://api.upload-post.com/api/upload
  Auth:     Authorization: Apikey <key>   (NOT "Bearer")
  Format:   multipart/form-data
  User ID:  "PolyVision" (the profile name in the Upload-Post dashboard)

Rate limit guard: max 1 video post per 12 hours.
"""
import os
import sys
import json
import time
import logging
import requests

log = logging.getLogger(__name__)

SECRETS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'secrets.json')

# PolyVision brain tools — absolute path (confirmed location)
POLYVISION_TOOLS = "/Users/adairclark/Desktop/AntiGravity/PolyVision/brain/tools"
POLYVISION_ENV   = "/Users/adairclark/Desktop/AntiGravity/PolyVision/.env"

_last_posted: dict[str, float] = {}
PLATFORM_INTERVALS = {
    "twitter": 12 * 3600,
    "video":   12 * 3600,   # shared cooldown for all video platforms
}

# Upload-Post config (confirmed from their API docs)
UPLOAD_POST_URL  = "https://api.upload-post.com/api/upload"
UPLOAD_POST_USER = "PolyVision"   # Profile name in Upload-Post dashboard
VIDEO_PLATFORMS  = ["tiktok", "youtube", "instagram"]


def _load_secrets() -> dict:
    try:
        with open(SECRETS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"secrets.json not found (expected in Railway — using env vars): {e}")
        return {}


def _rate_check(bucket: str) -> bool:
    elapsed = time.time() - _last_posted.get(bucket, 0)
    min_int = PLATFORM_INTERVALS.get(bucket, 3600)
    if elapsed < min_int:
        rem = int(min_int - elapsed)
        log.warning(f"[Rate Guard] {bucket}: {rem//3600}h {(rem%3600)//60}m cooldown remaining.")
        return False
    return True


def _mark_posted(bucket: str):
    _last_posted[bucket] = time.time()


# ── X (Twitter) ────────────────────────────────────────────────────────────────
def post_text(platform: str, text: str, dry_run: bool = False) -> dict:
    """
    Post a tweet to X.
    Path A: PolyVision's existing twitter_poster.py (Railway, already authenticated)
    Path B: Tweepy direct (fallback, uses secrets.json Twitter credentials)
    """
    if platform != "twitter":
        return {"status": "error", "message": f"post_text only supports twitter, got: {platform}"}

    if not _rate_check("twitter"):
        return {"status": "rate_limited", "platform": "twitter"}

    if dry_run:
        log.info(f"[DRY-RUN] X: {text[:140]}...")
        return {"status": "dry_run", "platform": "twitter", "content": text}

    # Path A: PolyVision's twitter_poster — call post_tweet() directly with our
    # pre-written marketing text (NOT maybe_tweet, which ignores our text and
    # applies its own whale-alert template to it).
    polyvision_tools_abs = os.path.abspath(POLYVISION_TOOLS)

    if os.path.isdir(polyvision_tools_abs):
        try:
            # Load PolyVision's .env so Twitter keys are available
            from dotenv import load_dotenv as _lde
            _lde(POLYVISION_ENV, override=False)

            if polyvision_tools_abs not in sys.path:
                sys.path.insert(0, polyvision_tools_abs)

            from twitter_poster import post_tweet, _credentials_set
            if not _credentials_set():
                raise RuntimeError("Twitter credentials not set in PolyVision .env")

            tweet_id = post_tweet(text[:280])   # pass our LLM-written text directly
            log.info(f"[Twitter] Posted via PolyVision post_tweet ✅ — ID: {tweet_id}")
            _mark_posted("twitter")
            return {"status": "success", "platform": "twitter", "tweet_id": tweet_id, "via": "polyvision_poster"}
        except Exception as e:
            log.warning(f"PolyVision twitter_poster unavailable ({e}) — falling back to Tweepy.")

    # Path B: Tweepy direct
    secrets       = _load_secrets()
    api_key       = secrets.get("TWITTER_API_KEY", os.getenv("TWITTER_API_KEY", ""))
    api_secret    = secrets.get("TWITTER_API_SECRET", os.getenv("TWITTER_API_KEY_SECRET", ""))
    access_token  = secrets.get("TWITTER_ACCESS_TOKEN", os.getenv("TWITTER_ACCESS_TOKEN", ""))
    access_secret = secrets.get("TWITTER_ACCESS_SECRET", os.getenv("TWITTER_ACCESS_TOKEN_SECRET", ""))

    if not all([api_key, api_secret, access_token, access_secret]) or "PASTE" in (api_key + api_secret):
        log.error("Twitter credentials not available. Add to secrets.json or Railway env vars.")
        return {"status": "error", "message": "Twitter credentials missing"}

    try:
        pkgs_dir = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'pkgs')
        if pkgs_dir not in sys.path:
            sys.path.insert(0, pkgs_dir)
        import tweepy
        client   = tweepy.Client(
            consumer_key=api_key, consumer_secret=api_secret,
            access_token=access_token, access_token_secret=access_secret,
        )
        resp     = client.create_tweet(text=text[:280])
        tweet_id = resp.data["id"] if resp.data else None
        log.info(f"[Twitter] Posted via Tweepy — ID: {tweet_id}")
        _mark_posted("twitter")
        return {"status": "success", "platform": "twitter", "tweet_id": tweet_id, "via": "tweepy"}
    except Exception as e:
        log.error(f"Tweepy post failed: {e}")
        return {"status": "error", "message": str(e)}


# ── Video: TikTok + YouTube Shorts + Instagram Reels ─────────────────────────
def post_video(platform: str, video_url: str, caption: str, dry_run: bool = False) -> dict:
    """
    Post a video to TikTok, YouTube Shorts, AND Instagram Reels simultaneously
    via a single Upload-Post API call (multipart/form-data).

    API: POST https://api.upload-post.com/api/upload
    Auth: Authorization: Apikey <key>    (NOT Bearer)
    User: "PolyVision" (Upload-Post profile name)

    The `platform` arg is kept for interface compatibility but ignored —
    all three connected platforms are always targeted together.
    """
    if not _rate_check("video"):
        return {"status": "rate_limited", "platforms": VIDEO_PLATFORMS}

    if dry_run:
        log.info(f"[DRY-RUN] VIDEO → {VIDEO_PLATFORMS}: {video_url[:60]} | Caption: {caption[:60]}")
        return {"status": "dry_run", "platforms": VIDEO_PLATFORMS, "video_url": video_url}

    secrets = _load_secrets()
    api_key = secrets.get("UPLOAD_POST_API_KEY", os.getenv("UPLOAD_POST_API_KEY", ""))

    if not api_key or "PASTE" in api_key:
        log.error("UPLOAD_POST_API_KEY not set in secrets.json")
        return {"status": "error", "message": "UPLOAD_POST_API_KEY missing"}

    # YouTube title (max 100 chars) — first sentence of caption
    youtube_title = caption.split(".")[0][:100] if "." in caption else caption[:100]
    youtube_desc  = (
        f"{caption}\n\n"
        f"Track whale trades in real time → polyvision.app\n"
        f"#Polymarket #Kalshi #PredictionMarkets #SmartMoney #WhaleTracking"
    )

    log.info(f"[Upload-Post] Downloading video from {video_url[:30]}... for binary injection")
    
    import tempfile
    local_vid = tempfile.mktemp(suffix=".mp4")
    try:
        with open(local_vid, 'wb') as f:
            v_resp = requests.get(video_url, timeout=60)
            v_resp.raise_for_status()
            f.write(v_resp.content)
            
        data = {
            "user": UPLOAD_POST_USER,
            "platform[]": ["tiktok", "youtube", "instagram"],
            "title": youtube_title,
            "description": youtube_desc,
            "async_upload": "true"
        }
        
        # Explicitly declare the file signature to prevent Image miscategorizations
        files = {
            "video": ("video.mp4", open(local_vid, "rb"), "video/mp4")
        }

        resp = requests.post(
            UPLOAD_POST_URL,
            headers={
                "Authorization": f"Apikey {api_key}",
            },
            data=data,   
            files=files, # Forces strict multipart/form-data boundary
            timeout=180,
        )

        log.info(f"[Upload-Post] HTTP {resp.status_code}")
        resp.raise_for_status()
        
        data = resp.json()
        log.info(f"[Video] Posted to {VIDEO_PLATFORMS} via Upload-Post ✅")
        log.info(f"[Video] Response: {json.dumps(data)[:400]}")
        _mark_posted("video")
        
        if os.path.exists(local_vid):
            os.remove(local_vid)
            
        return {"status": "success", "platforms": VIDEO_PLATFORMS, **data}

    except requests.HTTPError as e:
        try:
            err_body = e.response.text[:600]
        except Exception:
            err_body = str(e)
        log.error(f"Upload-Post API error {e.response.status_code}: {err_body}")
        return {"status": "error", "message": str(e), "body": err_body}
    except Exception as e:
        log.error(f"Upload-Post post failed: {e}")
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [publisher] %(levelname)s: %(message)s",
    )
    # Live test with a real rendered video URL
    result = post_video(
        platform="tiktok",
        video_url="https://f002.backblazeb2.com/file/creatomate-c8xg3hsxdu/0ad2d5fe-079b-4d51-b7d5-1e6043a1cfdf.mp4",
        caption="A whale just bet $2M on Bitcoin by July 🐳 Track every massive move in real time → polyvision.app #Polymarket",
        dry_run=False,
    )
    print(json.dumps(result, indent=2))
