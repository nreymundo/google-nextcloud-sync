from __future__ import annotations

import json
from pathlib import Path

import pytest

from g2nc.google.oauth import OAuthConfigError, load_client_config
from g2nc.models import GoogleAuthConfig


def _auth_config(
    tmp_path: Path, credentials_file: Path | None, credentials_json: str | None
) -> GoogleAuthConfig:
    return GoogleAuthConfig(
        credentials_file=credentials_file,
        credentials_json=credentials_json,
        token_file=tmp_path / "token.json",
        scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    )


def test_load_client_config_from_file(tmp_path: Path) -> None:
    credentials_file = tmp_path / "credentials.json"
    payload = {
        "installed": {
            "client_id": "abc.apps.googleusercontent.com",
            "client_secret": "secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    credentials_file.write_text(json.dumps(payload), encoding="utf-8")

    auth = _auth_config(tmp_path=tmp_path, credentials_file=credentials_file, credentials_json=None)
    resolved = load_client_config(auth)
    assert resolved == payload


def test_load_client_config_rejects_missing_fields(tmp_path: Path) -> None:
    auth = _auth_config(
        tmp_path=tmp_path,
        credentials_file=None,
        credentials_json=json.dumps(
            {
                "installed": {
                    "client_id": "abc.apps.googleusercontent.com",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
    )

    with pytest.raises(OAuthConfigError, match="missing field"):
        load_client_config(auth)
