from __future__ import annotations

from pathlib import Path

import pytest

from g2nc.cli import main
from g2nc.config import ConfigError
from g2nc.models import AppConfig, CalendarMapping, GoogleAuthConfig, LoggingConfig, NextcloudConfig


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        sqlite_path=tmp_path / "state.sqlite",
        lock_file=tmp_path / "g2nc.lock",
        logging=LoggingConfig(level="INFO", json=True),
        google=GoogleAuthConfig(
            credentials_file=None,
            credentials_json="{}",
            token_file=tmp_path / "token.json",
            scopes=("https://www.googleapis.com/auth/calendar.readonly",),
        ),
        nextcloud=NextcloudConfig(username="alice", app_password="secret", timeout_seconds=30),
        mappings=(
            CalendarMapping(
                name="work",
                google_calendar_id="primary",
                nextcloud_calendar_url="https://cloud.example/work/",
            ),
        ),
    )


def test_main_returns_error_for_bad_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["g2nc", "--config", "missing.json", "validate-config"])
    monkeypatch.setattr(
        "g2nc.cli.load_config", lambda path: (_ for _ in ()).throw(ConfigError("bad"))
    )

    assert main() == 2
    assert "Configuration error: bad" in capsys.readouterr().out


def test_validate_config_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr("sys.argv", ["g2nc", "--config", "settings.json", "validate-config"])
    monkeypatch.setattr("g2nc.cli.load_config", lambda path: config)
    monkeypatch.setattr("g2nc.cli.configure_logging", lambda level, use_json: None)

    assert main() == 0


def test_auth_bootstrap_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    called: dict[str, bool] = {"bootstrap": False}
    monkeypatch.setattr(
        "sys.argv", ["g2nc", "--config", "settings.json", "auth", "bootstrap", "--no-browser"]
    )
    monkeypatch.setattr("g2nc.cli.load_config", lambda path: config)
    monkeypatch.setattr("g2nc.cli.configure_logging", lambda level, use_json: None)
    monkeypatch.setattr(
        "g2nc.cli.bootstrap_token",
        lambda auth, open_browser: called.__setitem__("bootstrap", not open_browser),
    )

    assert main() == 0
    assert called["bootstrap"] is True


def test_sync_command_runs_all_mappings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    sync_calls: list[str] = []

    class _StateStub:
        def initialize(self) -> None:
            return None

    class _ServiceStub:
        def sync_mapping(self, mapping: CalendarMapping) -> None:
            sync_calls.append(mapping.name)

    class _LockStub:
        def __init__(self, path: Path) -> None:
            self.path = path

        def __enter__(self) -> _LockStub:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    monkeypatch.setattr("sys.argv", ["g2nc", "--config", "settings.json", "sync"])
    monkeypatch.setattr("g2nc.cli.load_config", lambda path: config)
    monkeypatch.setattr("g2nc.cli.configure_logging", lambda level, use_json: None)
    monkeypatch.setattr("g2nc.cli.SqliteStateRepository", lambda path: _StateStub())
    monkeypatch.setattr("g2nc.cli.GoogleCalendarClient", lambda auth: object())
    monkeypatch.setattr("g2nc.cli.NextcloudCalendarClient", lambda nextcloud: object())
    monkeypatch.setattr("g2nc.cli.SyncService", lambda google, nextcloud, state: _ServiceStub())
    monkeypatch.setattr("g2nc.cli.FileLock", _LockStub)

    assert main() == 0
    assert sync_calls == ["work"]
