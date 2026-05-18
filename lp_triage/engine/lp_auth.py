"""Launchpad OAuth OOB desktop flow.

Launchpad treats unregistered consumers as desktop apps and always uses the
OOB (out-of-band) flow regardless of whether oauth_callback is sent. The user
authorises on LP, then our UI prompts them to click Complete — at which point
we exchange the already-blessed request token for an access token (no verifier
needed for OOB).
"""

from __future__ import annotations

import logging
from pathlib import Path

from launchpadlib.credentials import Credentials
from launchpadlib.uris import lookup_web_root

logger = logging.getLogger(__name__)

_CONSUMER_KEY = "lp-triage"


def get_request_token(lp_instance: str = "production") -> tuple[str, str, Credentials]:
    """Return (auth_url, token_key, credentials) using OOB desktop flow."""
    web_root = lookup_web_root(lp_instance)
    creds = Credentials(consumer_name=_CONSUMER_KEY)
    auth_url = creds.get_request_token(web_root=web_root)
    token_key = creds._request_token.key
    return auth_url, token_key, creds


def exchange_token(cfg: dict, creds: Credentials, lp_instance: str = "production") -> bool:
    """Exchange the authorized request token for an access token and save credentials."""
    try:
        web_root = lookup_web_root(lp_instance)
        creds.exchange_request_token_for_access_token(web_root=web_root)

        creds_file = Path(
            cfg.get("auth", {}).get(
                "lp_credentials_file",
                str(Path.home() / ".config" / "lp-triage" / "lp-credentials"),
            )
        )
        creds_file.parent.mkdir(parents=True, exist_ok=True)
        creds.save_to_path(str(creds_file))
        logger.info("LP credentials saved to %s", creds_file)
        return True
    except Exception:
        logger.exception("LP token exchange failed")
        return False
