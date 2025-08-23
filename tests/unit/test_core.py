# Ensure src/ is importable without editable install
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Add repo_root/src to sys.path
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import yaml  # type: ignore
from typer.testing import CliRunner

from g2nc.cli import app  # type: ignore
from g2nc.config import AppConfig, load_config  # type: ignore
from g2nc.state import State  # type: ignore
from g2nc.utils.hashing import (  # type: ignore
    hash_ics,
    hash_vcard,
    normalize_ics,
    normalize_vcard,
)


def _write_yaml(p: Path, data: dict[str, Any]) -> None:
    p.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def test_config_precedence_file_env_cli(tmp_path, monkeypatch) -> None:
    cfg_yaml = {
        "nextcloud": {
            "base_url": "https://cloud.example.com",
            "username": "nc_user",
            "app_password": None,
            "addressbook_path": "/remote.php/dav/addressbooks/users/nc_user/Contacts/",
            "calendars": {},
        },
        "google": {
            "token_store": "/data/google_token.json",
            "calendar_ids": {"default": "primary"},
        },
        "sync": {
            "photo_sync": True,
            "overwrite_local": True,
            "time_window_days": 730,
        },
        "logging": {"level": "INFO", "json": True},
        "state": {"db_path": "/data/state.sqlite"},
        "runtime": {"lock_path": "/tmp/g2nc.lock"},
    }
    path = tmp_path / "config.yaml"
    _write_yaml(path, cfg_yaml)

    # ENV overrides logging.level -> DEBUG
    monkeypatch.setenv("G2NC__logging__level", "DEBUG")
    # ENV can also override nested values
    monkeypatch.setenv("G2NC__sync__photo_sync", "false")

    # CLI overrides trump ENV and file
    overrides = {
        "logging": {"level": "ERROR"},
        "sync": {"time_window_days": 365},  # override window
        "google": {"calendar_ids": {"work": "primary", "team": "team@group.calendar.google.com"}},
    }

    cfg: AppConfig = load_config(file_path=str(path), cli_overrides=overrides)
    # file base
    assert cfg.nextcloud.base_url == "https://cloud.example.com"
    assert cfg.nextcloud.username == "nc_user"
    # ENV applied then CLI overrides applied
    assert cfg.logging.level == "ERROR"  # CLI override wins over ENV=DEBUG and file=INFO
    assert cfg.sync.photo_sync is False  # ENV override wins over file=True (no CLI override)
    assert cfg.sync.time_window_days == 365  # CLI override
    # calendar_ids merged from CLI
    assert cfg.google.calendar_ids.get("work") == "primary"
    assert "team" in cfg.google.calendar_ids


def test_hashing_normalization_vcard_and_ics() -> None:
    v1 = "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:people/c1\r\nFN:Alice\r\nREV:2025-01-01T00:00:00Z\r\nEND:VCARD\r\n"
    v2 = "BEGIN:VCARD\nFN: Alice\nUID:people/c1\nVERSION:4.0\nREV:2025-03-01T10:00:00Z\nEND:VCARD\n"
    # REV should be removed, spaces normalized, order sorted deterministically
    n1 = normalize_vcard(v1)
    n2 = normalize_vcard(v2)
    assert "REV:" not in n1 and "REV:" not in n2
    assert hash_vcard(v1) == hash_vcard(v2)

    i1 = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:e1\nSUMMARY:Meet\nDTSTAMP:20250101T000000Z\n"
        "LAST-MODIFIED:20250102T000000Z\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    i2 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Meet\r\nUID:e1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    n_i1 = normalize_ics(i1)
    _ = normalize_ics(i2)
    assert "DTSTAMP:" not in n_i1 and "LAST-MODIFIED:" not in n_i1
    assert hash_ics(i1) == hash_ics(i2)


def test_state_dao_tokens_contacts_events(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    st = State(str(db))

    # tokens
    assert st.get_token("contacts") is None
    st.save_token("contacts", "tok-1")
    assert st.get_token("contacts") == "tok-1"
    st.save_token("contacts", "tok-2")  # upsert
    assert st.get_token("contacts") == "tok-2"
    st.reset_token("contacts")
    assert st.get_token("contacts") is None

    # contacts
    st.upsert_contact("people/c1", "/dav/1.vcf", 'W/"etag1"', "h1", deleted=0)
    rec = st.get_contact("people/c1")
    assert (
        rec
        and rec.nextcloud_href == "/dav/1.vcf"
        and rec.etag == 'W/"etag1"'
        and rec.content_hash == "h1"
    )
    assert st.lookup_contact_href("people/c1") == "/dav/1.vcf"
    # update
    st.upsert_contact("people/c1", "/dav/1.vcf", 'W/"etag2"', "h2", deleted=0)
    rec2 = st.get_contact("people/c1")
    assert rec2 and rec2.etag == 'W/"etag2"' and rec2.content_hash == "h2"
    # remove
    st.remove_contact("people/c1")
    assert st.get_contact("people/c1") is None

    # events
    st.upsert_event("primary", "e1", "/cal/1.ics", 'W/"e1"', "eh1", deleted=0)
    erec = st.get_event("primary", "e1")
    assert (
        erec
        and erec.nextcloud_href == "/cal/1.ics"
        and erec.etag == 'W/"e1"'
        and erec.content_hash == "eh1"
    )
    assert st.lookup_event_href("primary", "e1") == "/cal/1.ics"
    # update
    st.upsert_event("primary", "e1", "/cal/1.ics", 'W/"e2"', "eh2", deleted=0)
    erec2 = st.get_event("primary", "e1")
    assert erec2 and erec2.etag == 'W/"e2"' and erec2.content_hash == "eh2"
    # remove
    st.remove_event("primary", "e1")
    assert st.get_event("primary", "e1") is None


def test_cli_sync_scaffold(tmp_path, monkeypatch) -> None:
    # Prepare minimal config file
    cfg_yaml = {
        "nextcloud": {
            "base_url": "https://cloud.example.com",
            "username": "nc_user",
            "addressbook_path": "/remote.php/dav/addressbooks/users/nc_user/Contacts/",
        },
        "google": {"calendar_ids": {"default": "primary"}},
        "logging": {"json": False, "level": "INFO"},
    }
    path = tmp_path / "config.yaml"
    _write_yaml(path, cfg_yaml)

    runner = CliRunner()
    # Force non-JSON logging for predictable console output in test
    monkeypatch.setenv("G2NC__logging__json", "false")
    # Ensure CLI runs in scaffold mode (no orchestrator side effects)
    monkeypatch.setenv("G2NC_DEV_SCAFFOLD", "1")

    result = runner.invoke(app, ["sync", "--config", str(path), "--dry-run"])
    assert result.exit_code == 0
    assert "g2nc sync scaffold" in result.stdout
    assert "contacts: True" in result.stdout and "calendar: True" in result.stdout
