#!/usr/bin/env python3
from __future__ import annotations
"""
video_factory.py — VisionEdge Marketing Agent | Layer 3: Video Renderer

Renders a TikTok-ready 1080×1920 MP4 using LOCAL FFmpeg.
Replaces the previous Creatomate API ($54/month) with zero-cost on-device rendering.

Layout (1080×1920, 9:16 vertical):
  Layer 1 (bottom): Background image, scaled to fill full frame
  Layer 2:          Chart/odds graphic PNG, 1080px wide, centered
  Layer 3:          Hook caption text, top 8%, white bold + shadow + dark box
  Layer 4 (top):    Logo watermark PNG, bottom-right corner, 160px wide
  Audio:            TTS MP3, -shortest trims video to audio duration

PIPELINE:
  background URL → download to /tmp
  chart PNG (local) + audio MP3 (local) + logo PNG (local)
    → FFmpeg filter_complex composite
    → output.mp4 (local, ~5-20 MB)
    → upload to Cloudflare R2
    → return public R2 URL
"""
import os
import sys
import json
import time
import logging
import requests
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

SECRETS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'secrets.json')


def _load_secrets() -> dict:
    try:
        with open(SECRETS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"secrets.json not found (expected in Railway — using env vars): {e}")
        return {}


SECRETS = _load_secrets()

FAL_KEY = SECRETS.get("FAL_KEY", os.getenv("FAL_KEY", ""))
if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY

CATBOX_API = "https://catbox.moe/user/api.php"

# ── Cloudflare R2 Config ──────────────────────────────────────────────────────
R2_ACCOUNT_ID        = SECRETS.get("R2_ACCOUNT_ID",        os.getenv("R2_ACCOUNT_ID", ""))
R2_ACCESS_KEY_ID     = SECRETS.get("R2_ACCESS_KEY_ID",     os.getenv("R2_ACCESS_KEY_ID", ""))
R2_SECRET_ACCESS_KEY = SECRETS.get("R2_SECRET_ACCESS_KEY", os.getenv("R2_SECRET_ACCESS_KEY", ""))
R2_BUCKET_NAME       = SECRETS.get("R2_BUCKET_NAME",       os.getenv("R2_BUCKET_NAME", "polyvision-assets"))
R2_PUBLIC_URL        = SECRETS.get("R2_PUBLIC_URL",        os.getenv("R2_PUBLIC_URL", ""))
_R2_ENABLED          = bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_PUBLIC_URL)


def _guess_content_type(filename: str) -> str:
    """Lightweight content-type mapper for R2 uploads."""
    ext = Path(filename).suffix.lower().lstrip(".")
    return {
        "png":  "image/png",
        "jpg":  "image/jpeg",
        "jpeg": "image/jpeg",
        "mp3":  "audio/mpeg",
        "mp4":  "video/mp4",
        "gif":  "image/gif",
    }.get(ext, "application/octet-stream")


# ── Asset Hosting: R2-first, catbox fallback, transfer.sh final tier ─────────
def _upload_asset(file_path: str, retries: int = 2) -> str | None:
    """
    Upload a local file to the best available CDN and return a public URL.

    Priority:
      1. Cloudflare R2 (if R2_* env vars configured) — 99.99% SLA, zero egress.
      2. catbox.moe (anonymous fallback) — often blocked on datacenter IPs.
      3. transfer.sh (server-friendly, no auth, 7-day retention).
    """
    filename = Path(file_path).name

    # ── 1. Cloudflare R2 ─────────────────────────────────────────────────────
    if _R2_ENABLED:
        for attempt in range(1 + retries):
            try:
                import boto3
                from botocore.config import Config as BotoConfig
                endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
                s3 = boto3.client(
                    "s3",
                    endpoint_url=endpoint,
                    aws_access_key_id=R2_ACCESS_KEY_ID,
                    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                    config=BotoConfig(signature_version="s3v4"),
                    region_name="auto",
                )
                s3.upload_file(
                    file_path,
                    R2_BUCKET_NAME,
                    filename,
                    ExtraArgs={"ContentType": _guess_content_type(filename)},
                )
                public_url = f"{R2_PUBLIC_URL.rstrip('/')}/{filename}"
                log.info(f"[R2] Uploaded {filename} → {public_url}")
                return public_url
            except Exception as e:
                log.warning(f"[R2] Upload attempt {attempt + 1} failed: {e}")
                time.sleep(2 ** attempt)
        log.error(f"[R2] All {1 + retries} attempts failed for {filename} — falling back to catbox.")

    # ── 2. catbox.moe ─────────────────────────────────────────────────────────
    log.warning(f"[CDN] Using catbox.moe fallback for {filename} (configure R2 keys for production).")
    for attempt in range(1 + retries):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    CATBOX_API,
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": (filename, f)},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=60,
                )
            resp.raise_for_status()
            url = resp.text.strip()
            if url.startswith("https://"):
                log.info(f"[catbox] Uploaded {filename} → {url}")
                return url
            log.error(f"[catbox] Unexpected response: {url[:100]}")
        except Exception as e:
            log.warning(f"[catbox] Upload attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)

    log.warning(f"[CDN] catbox exhausted for {filename} — trying transfer.sh (server-friendly CDN).")

    # ── 3. transfer.sh (CDN #3 — no credentials, works from datacenter IPs) ──
    try:
        with open(file_path, "rb") as f:
            resp = requests.put(
                f"https://transfer.sh/{filename}",
                data=f,
                headers={"Max-Days": "7"},
                timeout=60,
            )
        resp.raise_for_status()
        url = resp.text.strip()
        if url.startswith("https://"):
            log.info(f"[transfer.sh] Uploaded {filename} → {url}")
            return url
        log.error(f"[transfer.sh] Unexpected response: {url[:100]}")
    except Exception as e:
        log.error(f"[transfer.sh] Upload failed: {e}")

    log.error(f"[CDN] All 3 CDN tiers exhausted for {filename}. Returning None.")
    return None


# Legacy alias — existing call sites in agent_generator.py keep working.
_upload_to_catbox = _upload_asset


# ── FFmpeg Font Resolution ────────────────────────────────────────────────────
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",   # Debian/Ubuntu (fonts-liberation pkg)
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",  # Ubuntu 22.04+
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",            # RHEL/CentOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",           # Universal fallback
]


def _find_font() -> str:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    log.warning("[FFmpeg] No system font found — drawtext may fail. Is fonts-liberation installed?")
    return _FONT_CANDIDATES[0]


def _escape_drawtext(text: str) -> str:
    """Escape caption text for FFmpeg drawtext filter."""
    text = text.replace("\\", "\\\\")   # must be first
    text = text.replace("'",  "\\'")
    text = text.replace(":",  "\\:")
    text = text.replace("%",  "\\%")
    return text


def _download_bg(url: str) -> str | None:
    """Download background image URL to a local temp file. Returns path or None."""
    try:
        resp = requests.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        suffix = ".mp4" if ("mp4" in ct or url.endswith(".mp4")) else \
                 ".png" if ("png" in ct or url.endswith(".png")) else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
            return f.name
    except Exception as e:
        log.error(f"[FFmpeg] Background download failed ({url[:60]}): {e}")
        return None


def _cleanup(*paths) -> None:
    """Best-effort file deletion — never raises."""
    for p in paths:
        if p and os.path.exists(str(p)):
            try:
                os.unlink(str(p))
            except Exception:
                pass



def _get_random_loop_url() -> str | None:
    """
    List MP4 video loops in R2 bucket under backgrounds/loops/ prefix and
    return a public URL for a randomly chosen one.
    Returns None if R2 is not configured or the folder is empty.
    """
    if not _R2_ENABLED:
        return None
    try:
        import boto3
        import random
        from botocore.config import Config as BotoConfig
        endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )
        resp = s3.list_objects_v2(Bucket=R2_BUCKET_NAME, Prefix="backgrounds/loops/")
        keys = [
            obj["Key"] for obj in resp.get("Contents", [])
            if obj["Key"].lower().endswith(".mp4")
        ]
        if not keys:
            log.warning("[R2] No MP4 loops found in backgrounds/loops/ — falling back to static image.")
            return None
        chosen = random.choice(keys)
        url = f"{R2_PUBLIC_URL.rstrip('/')}/{chosen}"
        log.info(f"[R2] Selected background loop: {chosen}")
        return url
    except Exception as e:
        log.warning(f"[R2] Could not list background loops ({e}) — falling back to static image.")
        return None


# ── Public Interface ──────────────────────────────────────────────────────────
def create_video(
    chart_image_path: str,
    audio_path: str | None,
    caption: str,
    bg_image_url: str,
    logo_path: str | None = None,
    include_music: bool = False,   # API-compat param — unused in FFmpeg path
    amount_str: str | None = None, # Dollar amount for animated slam intro (e.g. '$126,606')
) -> str | None:
    """
    Render a 1080×1920 TikTok-ready MP4 using local FFmpeg (zero API cost).

    Features:
      - Ken Burns slow horizontal pan on background (cinematic motion)
      - Chart overlay + caption text during main content
      - 3-second branded outro: centered logo + polyvision.app text
      - Audio padded with 3s silence for the outro segment

    Args:
        chart_image_path: Local path to odds/chart PNG (from chart_generator).
        audio_path:       Local path to TTS MP3 (from tts_generator). None = 30s silent.
        caption:          Hook text to burn into top of frame (≤60 chars).
        bg_image_url:     Public URL of background image (from R2 / Unsplash fallback).
        logo_path:        Local path to watermark/outro PNG (from outro_generator).

    Returns:
        Public R2 URL of the rendered MP4, or None on failure.
    """
    import subprocess
    from datetime import datetime as _dt

    OUTRO_DURATION = 3.0   # seconds of branded outro after audio ends

    # ── 1. Background: prefer a real video loop from R2 (more engaging, less shadowban risk)
    #       Fall back to the DALL-E/static image with Ken Burns if no loops available.
    log.info("[FFmpeg] Selecting background...")
    loop_url = _get_random_loop_url()
    is_video_bg = False
    bg_local: str | None = None

    if loop_url:
        bg_local = _download_bg(loop_url)
        is_video_bg = bool(bg_local and bg_local.endswith(".mp4"))
        if is_video_bg:
            log.info("[FFmpeg] Using real video loop background.")

    if not is_video_bg:
        log.info("[FFmpeg] Falling back to static background image (Ken Burns effect).")
        bg_local = _download_bg(bg_image_url)
        if not bg_local:
            log.error("[FFmpeg] Cannot render — no background available.")
            return None

    # ── 2. Output path ────────────────────────────────────────────────────────
    tmp_dir = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'marketing')
    os.makedirs(tmp_dir, exist_ok=True)
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(tmp_dir, f"video_{ts}.mp4")

    # ── 3. Resolve assets ─────────────────────────────────────────────────────
    font_path  = _find_font()
    safe_cap   = _escape_drawtext(caption)
    safe_font  = font_path.replace("\\", "/")

    has_chart       = bool(chart_image_path and os.path.exists(str(chart_image_path)))
    chart_is_animated = has_chart and os.path.isdir(str(chart_image_path))  # directory = animated frames
    has_audio       = bool(audio_path and os.path.exists(str(audio_path)))
    has_logo        = bool(logo_path and os.path.exists(str(logo_path)))

    if not has_chart:
        log.error(f"[FFmpeg] Chart missing: {chart_image_path}")
        _cleanup(bg_local)
        return None

    # ── 4. Get audio duration for outro timing ───────────────────────────────
    audio_dur = 15.0   # safe default
    if has_audio:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                capture_output=True, text=True, timeout=10,
            )
            audio_dur = float(probe.stdout.strip())
        except Exception as e:
            log.warning(f"[FFmpeg] ffprobe duration failed ({e}) — assuming {audio_dur}s")

    total_dur = audio_dur + OUTRO_DURATION
    od = audio_dur   # outro start time (shorthand)

    # ── 5. FFmpeg inputs ──────────────────────────────────────────────────────
    # Index 0: background (video loop or static image)
    # Index 1: chart (image sequence directory or single PNG)
    # Index 2: logo PNG (if present)
    # Index 2 or 3: audio MP3 (if present)
    ANIM_FRAMES      = 30  # Must match chart_generator.ANIM_FRAMES — no cross-import to avoid path issues
    chart_last_frame = ANIM_FRAMES - 1       # for loop= freeze filter

    if chart_is_animated:
        frames_pattern = os.path.join(str(chart_image_path), "frame_%03d.png")
        chart_input = ["-framerate", "30", "-i", frames_pattern]
        log.info(f"[FFmpeg] Animated chart: {ANIM_FRAMES} frames from {chart_image_path}")
    else:
        chart_input = ["-i", str(chart_image_path)]

    if is_video_bg:
        ff_inputs: list[str] = ["-stream_loop", "-1", "-i", bg_local] + chart_input
    else:
        ff_inputs: list[str] = ["-loop", "1", "-i", bg_local] + chart_input

    logo_idx = None
    if has_logo:
        logo_idx = 2
        ff_inputs += ["-i", logo_path]

    audio_idx = None
    if has_audio:
        audio_idx = (logo_idx + 1) if logo_idx is not None else 2
        ff_inputs += ["-i", audio_path]

    # ── 6. filter_complex ─────────────────────────────────────────────────────
    if is_video_bg:
        # A) Real video loop: scale to fill 1080×1920 and crop center.
        #    No Ken Burns needed — the video already has motion.
        fp = [
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1[bg]"
        ]
    else:
        # A) Static image fallback: scale to 1.4× and apply slow horizontal crop-pan
        #    (Ken Burns effect — 12px/s, capped at 216px = half the 432px headroom)
        fp = [
            "[0:v]scale=1512:2688,setsar=1[bg_big]",
            "[bg_big]crop=w=1080:h=1920:x='min(t*12\\,216)':y='384',setsar=1[bg]",
        ]

    # B) Scale chart + freeze on last frame if animated (loop=-1 loops indefinitely from start=last)
    if chart_is_animated:
        fp.append(f"[1:v]scale=1080:-1,loop=loop=-1:size=1:start={chart_last_frame}[chart_s]")
    else:
        fp.append("[1:v]scale=1080:-1[chart_s]")

    # C) Chart overlay — visible during main content only (0 to audio_dur)
    main_enable = f":enable='between(t,0,{od:.2f})'" if has_audio else ""
    fp.append(f"[bg][chart_s]overlay=x=(W-w)/2:y=(H-h)/2{main_enable}[v1]")
    cur = "v1"

    # D) Logo layers: small watermark (main) + large centered (outro)
    if has_logo:
        # Small corner watermark during main content
        fp.append(f"[{logo_idx}:v]scale=140:-1[logo_small]")
        fp.append(f"[{cur}][logo_small]overlay=x=W-w-20:y=H-h-30{main_enable}[v2]")
        cur = "v2"

        if has_audio:
            # Large centered logo during 3-second outro
            fp.append(f"[{logo_idx}:v]scale=360:-1[logo_big]")
            fp.append(
                f"[{cur}][logo_big]overlay="
                f"x=(W-w)/2:y=(H-h)/2-140:"
                f"enable='between(t,{od:.2f},{total_dur:.2f})'[v3]"
            )
            cur = "v3"

    # E) Caption drawtext — below the WHALE ALERT badge (~13.5% from top), visible during main content only
    fp.append(
        f"[{cur}]drawtext="
        f"text='{safe_cap}':"
        f"x=(w-text_w)/2:"
        f"y=h*0.135:"
        f"fontsize=40:"
        f"fontcolor=white:"
        f"fontfile='{safe_font}':"
        f"shadowcolor=black@0.85:"
        f"shadowx=3:shadowy=3:"
        f"fix_bounds=1:"
        f"box=1:boxcolor=black@0.45:boxborderw=16"
        f"{main_enable}"
        f"[v_cap]"
    )
    cur = "v_cap"

    # F) "polyvision.app" brand text — visible during outro only (brand green)
    if has_audio and has_logo:
        outro_enable = f"enable='between(t,{od:.2f},{total_dur:.2f})'"
        fp.append(
            f"[{cur}]drawtext="
            f"text='polyvision.app':"
            f"x=(w-text_w)/2:"
            f"y=h*0.72:"
            f"fontsize=64:"
            f"fontcolor=#10B981:"
            f"fontfile='{safe_font}':"
            f"shadowcolor=black@0.9:"
            f"shadowx=2:shadowy=2:"
            f"{outro_enable}"
            f"[main_content]"
        )
    else:
        fp.append(f"[{cur}]copy[main_content]")

    # G) Dollar slam intro — 0.6s black card with amount slammed in white text
    #    Prepended via concat so it plays BEFORE the main content (no audio yet).
    SLAM_DURATION = 0.6
    has_slam = bool(amount_str)
    if has_slam:
        safe_amount = _escape_drawtext(amount_str)
        slam_idx = len(ff_inputs) // 2 + (1 if is_video_bg else 0)  # will be appended last
        fp.append(
            f"color=black:size=1080x1920:rate=30:d={SLAM_DURATION}[slam_bg]"
        )
        fp.append(
            f"[slam_bg]drawtext="
            f"text='{safe_amount}':"
            f"x=(w-text_w)/2:"
            f"y=(h-text_h)/2:"
            f"fontsize=148:"
            f"fontcolor=white:"
            f"fontfile='{safe_font}':"
            f"shadowcolor=black@0.6:"
            f"shadowx=5:shadowy=5"
            f"[slam_card]"
        )
        fp.append("[slam_card][main_content]concat=n=2:v=1:a=0[final]")
    else:
        fp.append("[main_content]copy[final]")

    filter_complex = ";".join(fp)

    # ── 7. Full FFmpeg command ────────────────────────────────────────────────
    cmd = ["ffmpeg", "-y", *ff_inputs,
           "-filter_complex", filter_complex,
           "-map", "[final]"]

    if has_audio:
        slam_ms = int(SLAM_DURATION * 1000) if has_slam else 0
        slam_total = total_dur + (SLAM_DURATION if has_slam else 0)
        af_chain = f"adelay={slam_ms}|{slam_ms},apad=pad_dur={OUTRO_DURATION}" if has_slam else f"apad=pad_dur={OUTRO_DURATION}"
        cmd += ["-map", f"{audio_idx}:a",
                "-af", af_chain,
                "-t", f"{slam_total:.2f}"]
    else:
        silent_dur = 30 + (SLAM_DURATION if has_slam else 0)
        cmd += ["-t", f"{silent_dur:.2f}"]   # silent fallback

    cmd += [
        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",    # Required for TikTok/Instagram/Shorts
        "-r", "30",
        "-movflags", "+faststart",  # Moov atom at front — enables streaming
        output_path,
    ]

    # ── 8. Render ─────────────────────────────────────────────────────────────
    slam_label = f" + {SLAM_DURATION}s slam intro" if has_slam else ""
    log.info(f"[FFmpeg] Rendering {total_dur:.1f}s video (main={audio_dur:.1f}s + outro={OUTRO_DURATION:.0f}s{slam_label})...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=360)
    except subprocess.TimeoutExpired:
        log.error("[FFmpeg] Render timed out after 360s.")
        _cleanup(bg_local, output_path)
        return None
    except FileNotFoundError:
        log.error("[FFmpeg] ffmpeg not found — is ffmpeg in the Dockerfile apt-get install?")
        _cleanup(bg_local)
        return None
    finally:
        _cleanup(bg_local)

    if result.returncode != 0:
        log.error(f"[FFmpeg] Render failed (exit {result.returncode}).")
        log.error(f"[FFmpeg] stderr:\n{result.stderr[-2000:]}")
        _cleanup(output_path)
        return None

    size_mb = os.path.getsize(output_path) / 1_000_000
    log.info(f"[FFmpeg] ✅ Render complete — {size_mb:.1f} MB")

    # ── 9. Upload to R2 ───────────────────────────────────────────────────────
    log.info("[FFmpeg] Uploading rendered video to R2...")
    video_url = _upload_asset(output_path)
    _cleanup(output_path)

    if not video_url:
        log.error("[FFmpeg] Video CDN upload failed.")
        return None

    log.info(f"[FFmpeg] ✅ Video live at: {video_url}")
    return video_url



# ── Standalone Test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [video_factory] %(levelname)s: %(message)s",
    )

    tmp_dir = os.path.join(os.path.dirname(__file__), '..', '..', '.tmp', 'marketing')
    charts  = sorted(Path(tmp_dir).glob("graphic_*.png"), reverse=True)
    audios  = sorted(Path(tmp_dir).glob("voiceover_*.mp3"), reverse=True)

    chart_path   = str(charts[0]) if charts else None
    audio_path   = str(audios[0]) if audios else None
    test_bg_url  = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1080&q=90"
    test_caption = "$340K Smart Money Signal Detected"

    if not chart_path:
        print("No chart PNG in .tmp/marketing/. Run chart_generator.py first.")
        sys.exit(1)

    log.info(f"Chart:   {chart_path}")
    log.info(f"Audio:   {audio_path or 'None (silent)'}")
    log.info(f"Caption: {test_caption}")

    url = create_video(
        chart_image_path=chart_path,
        audio_path=audio_path,
        caption=test_caption,
        bg_image_url=test_bg_url,
    )
    if url:
        print(f"\n✅ VIDEO URL: {url}")
    else:
        print("\n❌ Video render failed.")
        sys.exit(1)
