"""Launchpad OAuth OOB desktop flow.

Launchpad treats unregistered consumers as desktop apps and always uses the
OOB (out-of-band) flow regardless of whether oauth_callback is sent. The user
authorises on LP, then our UI prompts them to click Complete — at which point
we exchange the already-blessed request token for an access token (no verifier
needed for OOB).
"""

from __future__ import annotations

import logging
import urllib.parse as urlparse
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_CONSUMER_KEY = "lp-triage"
_TIMEOUT = 15  # seconds


def _web_root(lp_instance: str) -> str:
    from launchpadlib.uris import lookup_web_root
    return lookup_web_root(lp_instance)


def _lp_post(url: str, params: dict, lp_root: str) -> dict:
    """POST url-encoded params to LP, return parsed response as dict."""
    body = urlparse.urlencode(params)
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            url,
            content=body,
            headers={
                "Referer": lp_root,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"LP returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return dict(urlparse.parse_qsl(resp.text))


def get_request_token(lp_instance: str = "production") -> tuple[str, str, str]:
    """Return (auth_url, token_key, token_secret) using OOB desktop flow."""
    lp_root = _web_root(lp_instance)
    data = _lp_post(lp_root + "+request-token", {
        "oauth_consumer_key": _CONSUMER_KEY,
        "oauth_signature_method": "PLAINTEXT",
        "oauth_signature": "&",
        "oauth_callback": "oob",
    }, lp_root)
    token_key = data["oauth_token"]
    token_secret = data["oauth_token_secret"]
    auth_url = f"{lp_root}+authorize-token?oauth_token={urlparse.quote(token_key)}"
    return auth_url, token_key, token_secret


def exchange_token(
    cfg: dict,
    oauth_token: str,
    oauth_token_secret: str,
    oauth_verifier: str,
    lp_instance: str = "production",
) -> bool:
    """Exchange the authorized request token for an access token and save credentials."""
    try:
        lp_root = _web_root(lp_instance)
        params: dict = {
            "oauth_consumer_key": _CONSUMER_KEY,
            "oauth_signature_method": "PLAINTEXT",
            "oauth_token": oauth_token,
            "oauth_signature": f"&{urlparse.quote(oauth_token_secret)}",
        }
        if oauth_verifier:
            params["oauth_verifier"] = oauth_verifier

        data = _lp_post(lp_root + "+access-token", params, lp_root)
        access_token = data["oauth_token"]
        access_secret = data["oauth_token_secret"]

        creds_file = Path(
            cfg.get("auth", {}).get(
                "lp_credentials_file",
                str(Path.home() / ".config" / "lp-triage" / "lp-credentials"),
            )
        )
        creds_file.parent.mkdir(parents=True, exist_ok=True)

        # Write in the INI format that launchpadlib's Credentials.load_from_path() expects
        creds_file.write_text(
            f"[1]\n"
            f"consumer_key = {_CONSUMER_KEY}\n"
            f"consumer_secret = \n"
            f"access_token = {access_token}\n"
            f"access_secret = {access_secret}\n"
        )
        logger.info("LP credentials saved to %s", creds_file)
        return True
    except Exception:
        logger.exception("LP token exchange failed")
        return False
