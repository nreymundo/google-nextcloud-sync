"""Google OAuth helper utilities.

Responsibilities
- Load OAuth client credentials from file or env (GOOGLE_CREDENTIALS_JSON / GOOGLE_CREDENTIALS_FILE)
- Read/write user token to google_cfg.token_store
- Refresh access tokens as needed (headless thereafter)
- Provide ready-to-use google Credentials for API clients

Notes
- Initial interactive flow requires a local browser (run on developer machine once).
  It creates/updates the token_store (mounted under /data in Docker).
- Subsequent runs (local/CI/Docker) refresh headlessly using the refresh token.

Security
- Never log raw tokens; the logging module should redact any token-like strings.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Avoid mypy hard dependency on google packages when not installed locally.
if TYPE_CHECKING:  # pragma: no cover
    from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]


from ..config import GoogleConfig

__all__ = [
    "SCOPES_CALENDAR",
    "SCOPES_CONTACTS",
    "get_credentials",
]

log = logging.getLogger(__name__)

# People API + Calendar API scopes (read/write mirror to Nextcloud)
SCOPES_CONTACTS: list[str] = [
    "https://www.googleapis.com/auth/contacts.readonly",
]
SCOPES_CALENDAR: list[str] = [
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _read_client_config(google_cfg: GoogleConfig) -> dict[str, Any]:
    """Load OAuth client credentials JSON from env or file.

    Priority:
    - GOOGLE_CREDENTIALS_JSON (inline JSON)
    - GOOGLE_CREDENTIALS_FILE (path)
    - google_cfg.credentials_file
    """
    env_inline = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if env_inline:
        try:
            return json.loads(env_inline)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON in GOOGLE_CREDENTIALS_JSON") from exc

    env_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
    file_path = env_file or google_cfg.credentials_file
    if not file_path:
        raise ValueError(
            "Google credentials not provided. Set GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE or google.credentials_file"
        )
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Google credentials file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_saved_credentials(token_store: str, scopes: Sequence[str]) -> Credentials | None:
    """Return Credentials from token store if available and valid for scopes, else None."""
    try:
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "google-auth is required. Install 'google-auth' and 'google-auth-oauthlib'."
        ) from exc

    p = Path(token_store)
    if not p.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
            str(p), scopes=list(scopes)
        )
        return creds
    except Exception:
        return None


def _save_credentials(token_store: str, creds: Credentials) -> None:
    p = Path(token_store)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        fh.write(creds.to_json())  # type: ignore[no-untyped-call]


def _interactive_flow(client_config: dict[str, Any], scopes: Sequence[str]) -> Credentials:
    """Run installed app flow with local server for user consent (interactive)."""
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

    flow = InstalledAppFlow.from_client_config(client_config, scopes=list(scopes))  # type: ignore[no-untyped-call]
    # Use a random free port; restrict to localhost
    return flow.run_local_server(  # type: ignore[no-untyped-call]
        open_browser=True, host="localhost", port=0, authorization_prompt_message=""
    )  # returns Credentials


def _refresh_if_needed(creds: Credentials) -> None:
    """Refresh access token if expired and refresh token is present."""
    from google.auth.transport.requests import Request  # type: ignore[import-not-found]

    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        creds.refresh(Request())  # type: ignore[no-untyped-call]


def get_credentials(
    google_cfg: GoogleConfig,
    scopes: Sequence[str],
    *,
    allow_interactive: bool = True,
) -> Credentials:
    """Return Google OAuth credentials ready for use with google-api-python-client.

    Behavior:
    - Try token_store; refresh if needed.
    - If not present and allow_interactive, run browser consent and store token.
    - If not present and non-interactive, raise.
    """
    # 1) try token store
    creds = _load_saved_credentials(google_cfg.token_store, scopes)
    if creds:
        try:
            _refresh_if_needed(creds)
            if getattr(creds, "valid", False):
                return creds
        finally:
            # Save back any refresh (may have new expiry or tokens)
            try:
                _save_credentials(google_cfg.token_store, creds)
            except Exception:
                pass

    # 2) interactive flow
    if not allow_interactive:
        raise RuntimeError(
            "No valid Google token found and allow_interactive=False. Run locally once to generate the token store."
        )
    client_config = _read_client_config(google_cfg)
    creds = _interactive_flow(client_config, scopes)
    # Save token
    _save_credentials(google_cfg.token_store, creds)
    return creds
