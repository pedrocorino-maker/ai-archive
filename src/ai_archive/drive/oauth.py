"""AI Archive — Google Drive OAuth Desktop App flow."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..logging_config import get_logger

logger = get_logger("drive.oauth")

SCOPES = ["https://www.googleapis.com/auth/drive"]


def is_token_valid(token_json: Path) -> bool:
    """Return True if a saved token file exists and is not expired."""
    if not token_json.exists():
        return False
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(token_json), SCOPES)
        if not creds:
            return False
        if creds.valid:
            return True
        # Check if it can be refreshed
        if creds.expired and creds.refresh_token:
            return True  # Will be refreshed by get_credentials
        return False
    except Exception as exc:
        logger.debug("Token validity check failed: %s", exc)
        return False


def get_credentials(
    credentials_json: Path,
    token_json: Path,
) -> object:
    """Load, refresh, or obtain new Google OAuth credentials.

    Uses InstalledAppFlow for the initial OAuth dance.
    Saves the token to token_json for subsequent runs.

    Returns google.oauth2.credentials.Credentials.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None

    # Load existing token
    if token_json.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_json), SCOPES)
        except Exception as exc:
            logger.warning("Failed to load token from %s: %s", token_json, exc)
            creds = None

    # Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            logger.info("Refreshed Google OAuth token.")
        except Exception as exc:
            logger.warning("Token refresh failed: %s — re-authorizing.", exc)
            creds = None

    # Run OAuth flow if no valid creds
    if not creds or not creds.valid:
        if not credentials_json.exists():
            raise FileNotFoundError(
                f"Google Drive credentials file not found: {credentials_json}. "
                "Download it from Google Cloud Console (OAuth Desktop App)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_json), SCOPES)
        creds = flow.run_local_server(port=0)
        logger.info("New Google OAuth token obtained via browser flow.")

    # Persist token
    token_json.parent.mkdir(parents=True, exist_ok=True)
    token_json.write_text(creds.to_json(), encoding="utf-8")
    logger.debug("Token saved to %s", token_json)

    return creds
