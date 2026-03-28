from __future__ import annotations

import json
from typing import Any

from google_auth_oauthlib.flow import InstalledAppFlow

from g2nc.models import GoogleAuthConfig


class OAuthConfigError(ValueError):
    pass


def load_client_config(auth: GoogleAuthConfig) -> dict[str, Any]:
    if auth.credentials_json is not None:
        try:
            payload = json.loads(auth.credentials_json)
        except json.JSONDecodeError as exc:
            raise OAuthConfigError(f"invalid credentials_json: {exc}") from exc
    elif auth.credentials_file is not None:
        try:
            payload = json.loads(auth.credentials_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OAuthConfigError(
                f"credentials file does not exist: {auth.credentials_file}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise OAuthConfigError(f"invalid credentials file JSON: {exc}") from exc
    else:
        raise OAuthConfigError("credentials_json or credentials_file is required")

    if not isinstance(payload, dict):
        raise OAuthConfigError("oauth client config must be an object")

    container: dict[str, Any] | None = None
    if "installed" in payload and isinstance(payload["installed"], dict):
        container = payload["installed"]
    elif "web" in payload and isinstance(payload["web"], dict):
        container = payload["web"]

    if container is None:
        raise OAuthConfigError("oauth config must contain installed or web object")

    required_fields = ["client_id", "client_secret", "auth_uri", "token_uri"]
    for field in required_fields:
        value = container.get(field)
        if not isinstance(value, str) or value.strip() == "":
            raise OAuthConfigError(f"oauth config missing field: {field}")

    return payload


def bootstrap_token(auth: GoogleAuthConfig, open_browser: bool) -> None:
    config = load_client_config(auth)
    flow = InstalledAppFlow.from_client_config(config, list(auth.scopes))
    credentials = flow.run_local_server(open_browser=open_browser)
    auth.token_file.parent.mkdir(parents=True, exist_ok=True)
    auth.token_file.write_text(credentials.to_json(), encoding="utf-8")
