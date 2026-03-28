from __future__ import annotations

import json
from pathlib import Path

import pytest

from g2nc.config import ConfigError, load_config


def _write_config(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_config_from_json_and_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = _write_config(
        tmp_path / "settings.json",
        {
            "sqlite_path": "data/state.sqlite",
            "lock_file": "data/sync.lock",
            "logging": {"level": "info", "json": True},
            "google": {
                "credentials_file": "config/client_secret.json",
                "token_file": "config/token.json",
                "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
            },
            "nextcloud": {"username": "alice", "app_password": "from-config"},
            "mappings": [
                {
                    "name": "work",
                    "google_calendar_id": "primary",
                    "nextcloud_calendar_url": "https://cloud.example/remote.php/dav/calendars/alice/work/",
                }
            ],
        },
    )

    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "from-env")

    config = load_config(config_file)

    assert config.nextcloud.app_password == "from-env"
    assert config.logging.level == "INFO"
    assert config.mappings[0].mapping_key == (
        "primary|https://cloud.example/remote.php/dav/calendars/alice/work/"
    )
    assert config.google.token_file == (tmp_path / "config/token.json").resolve()


def test_load_config_rejects_duplicate_mappings(tmp_path: Path) -> None:
    config_file = _write_config(
        tmp_path / "settings.json",
        {
            "google": {"credentials_json": "{}", "token_file": "token.json"},
            "nextcloud": {"username": "alice", "app_password": "x"},
            "mappings": [
                {
                    "name": "one",
                    "google_calendar_id": "primary",
                    "nextcloud_calendar_url": "https://cloud.example/cal/1/",
                },
                {
                    "name": "two",
                    "google_calendar_id": "primary",
                    "nextcloud_calendar_url": "https://cloud.example/cal/1/",
                },
            ],
        },
    )

    with pytest.raises(ConfigError, match="duplicate mapping key"):
        load_config(config_file)
