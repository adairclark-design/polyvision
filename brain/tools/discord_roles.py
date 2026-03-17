"""
discord_roles.py — PolyVision Discord PRO role management.

Grants/revokes the "PRO Member" Discord role when a user subscribes or cancels.

Required env vars (set in Railway Brain service):
    DISCORD_BOT_TOKEN   — bot token (already set)
    DISCORD_GUILD_ID    — right-click server name in Discord → Copy Server ID
    DISCORD_PRO_ROLE_ID — Server Settings → Roles → right-click PRO Member → Copy Role ID
"""

import os
import logging
import requests

log = logging.getLogger(__name__)

DISCORD_BOT_TOKEN   = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID    = os.getenv("DISCORD_GUILD_ID", "")
DISCORD_PRO_ROLE_ID = os.getenv("DISCORD_PRO_ROLE_ID", "")

DISCORD_API = "https://discord.com/api/v10"


def _headers() -> dict:
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
        "X-Audit-Log-Reason": "PolyVision PRO subscription change",
    }


def _enabled() -> bool:
    """Return True only when all required config is present."""
    if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID or not DISCORD_PRO_ROLE_ID:
        log.warning(
            "Discord role management skipped — missing one or more env vars: "
            "DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DISCORD_PRO_ROLE_ID"
        )
        return False
    return True


def grant_pro_role(discord_user_id: str) -> bool:
    """
    Add the PRO Member role to a Discord user in the PolyVision server.
    Returns True on success, False on failure (always safe to call).
    """
    if not discord_user_id or not _enabled():
        return False
    try:
        url = f"{DISCORD_API}/guilds/{DISCORD_GUILD_ID}/members/{discord_user_id}/roles/{DISCORD_PRO_ROLE_ID}"
        r = requests.put(url, headers=_headers(), timeout=10)
        if r.status_code in (200, 201, 204):
            log.info(f"Discord PRO role granted to user {discord_user_id}")
            return True
        log.error(f"Failed to grant Discord role: {r.status_code} {r.text}")
        return False
    except Exception as e:
        log.error(f"grant_pro_role error: {e}")
        return False


def revoke_pro_role(discord_user_id: str) -> bool:
    """
    Remove the PRO Member role from a Discord user.
    Returns True on success, False on failure (always safe to call).
    """
    if not discord_user_id or not _enabled():
        return False
    try:
        url = f"{DISCORD_API}/guilds/{DISCORD_GUILD_ID}/members/{discord_user_id}/roles/{DISCORD_PRO_ROLE_ID}"
        r = requests.delete(url, headers=_headers(), timeout=10)
        if r.status_code in (200, 201, 204):
            log.info(f"Discord PRO role revoked from user {discord_user_id}")
            return True
        log.error(f"Failed to revoke Discord role: {r.status_code} {r.text}")
        return False
    except Exception as e:
        log.error(f"revoke_pro_role error: {e}")
        return False


def exchange_code_for_user_id(code: str, redirect_uri: str) -> tuple[str | None, str | None]:
    """
    Complete Discord OAuth2 flow:
      1. Exchange authorization code for an access token
      2. Call /users/@me to get the user's Discord ID
    Returns (discord_user_id, None) on success or (None, error_detail) on failure.
    """
    client_id     = os.getenv("DISCORD_CLIENT_ID", "")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        msg = "DISCORD_CLIENT_ID or DISCORD_CLIENT_SECRET not set in env."
        log.error(msg)
        return None, msg

    # Step 1: Exchange code → access token
    try:
        token_resp = requests.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if not token_resp.ok:
            detail = f"Token exchange {token_resp.status_code}: {token_resp.text}"
            log.error(f"Discord token exchange failed — {detail}")
            return None, detail
        access_token = token_resp.json()["access_token"]
    except Exception as e:
        detail = f"Token exchange exception: {e}"
        log.error(f"Discord {detail}")
        return None, detail

    # Step 2: Get Discord user ID
    try:
        me_resp = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if not me_resp.ok:
            detail = f"/users/@me {me_resp.status_code}: {me_resp.text}"
            log.error(f"Discord {detail}")
            return None, detail
        discord_user_id = me_resp.json()["id"]
        log.info(f"Discord user ID obtained: {discord_user_id}")
        return discord_user_id, None
    except Exception as e:
        detail = f"/users/@me exception: {e}"
        log.error(f"Discord {detail}")
        return None, detail
