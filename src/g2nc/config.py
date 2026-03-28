from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from g2nc.models import AppConfig, CalendarMapping, GoogleAuthConfig, LoggingConfig, NextcloudConfig

DEFAULT_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/calendar.readonly",)


class ConfigError(ValueError):
    pass


def _resolve_path(value: str, config_path: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (config_path.parent / candidate).resolve()


def _as_dict(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{field} must be an object")
    return raw


def _as_str(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or raw.strip() == "":
        raise ConfigError(f"{field} must be a non-empty string")
    return raw


def _as_bool(raw: Any, field: str) -> bool:
    if not isinstance(raw, bool):
        raise ConfigError(f"{field} must be a boolean")
    return raw


def _as_int(raw: Any, field: str) -> int:
    if not isinstance(raw, int):
        raise ConfigError(f"{field} must be an integer")
    return raw


def load_config(config_path: Path) -> AppConfig:
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file does not exist: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in config file: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config file root must be an object")

    sqlite_path_str = _as_str(raw.get("sqlite_path", "data/state.sqlite"), "sqlite_path")
    lock_file_str = _as_str(raw.get("lock_file", "data/g2nc.lock"), "lock_file")

    logging_raw = _as_dict(raw.get("logging", {}), "logging")
    logging_level = _as_str(logging_raw.get("level", "INFO"), "logging.level")
    logging_json = _as_bool(logging_raw.get("json", True), "logging.json")

    google_raw = _as_dict(raw.get("google"), "google")
    google_credentials_file = google_raw.get("credentials_file")
    google_credentials_json = google_raw.get("credentials_json")
    google_token_file = google_raw.get("token_file", "config/google_token.json")
    google_scopes = google_raw.get("scopes", list(DEFAULT_SCOPES))

    env_credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE")
    env_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    env_token_file = os.getenv("GOOGLE_TOKEN_FILE")

    if env_credentials_file:
        google_credentials_file = env_credentials_file
    if env_credentials_json:
        google_credentials_json = env_credentials_json
    if env_token_file:
        google_token_file = env_token_file

    if google_credentials_file is None and google_credentials_json is None:
        raise ConfigError(
            "google.credentials_file or google.credentials_json " "(or env equivalent) is required"
        )

    if google_credentials_file is not None and not isinstance(google_credentials_file, str):
        raise ConfigError("google.credentials_file must be a string")
    if google_credentials_json is not None and not isinstance(google_credentials_json, str):
        raise ConfigError("google.credentials_json must be a string")
    if not isinstance(google_token_file, str) or google_token_file.strip() == "":
        raise ConfigError("google.token_file must be a non-empty string")

    if not isinstance(google_scopes, list) or not all(
        isinstance(item, str) for item in google_scopes
    ):
        raise ConfigError("google.scopes must be an array of strings")

    nextcloud_raw = _as_dict(raw.get("nextcloud"), "nextcloud")
    nextcloud_username = nextcloud_raw.get("username")
    nextcloud_password = nextcloud_raw.get("app_password")
    nextcloud_timeout = _as_int(
        nextcloud_raw.get("timeout_seconds", 30), "nextcloud.timeout_seconds"
    )

    env_nextcloud_username = os.getenv("NEXTCLOUD_USERNAME")
    env_nextcloud_password = os.getenv("NEXTCLOUD_APP_PASSWORD")
    if env_nextcloud_username:
        nextcloud_username = env_nextcloud_username
    if env_nextcloud_password:
        nextcloud_password = env_nextcloud_password

    if not isinstance(nextcloud_username, str) or nextcloud_username.strip() == "":
        raise ConfigError("nextcloud.username is required")
    if not isinstance(nextcloud_password, str) or nextcloud_password.strip() == "":
        raise ConfigError("nextcloud.app_password is required")

    mappings_raw = raw.get("mappings")
    if not isinstance(mappings_raw, list) or len(mappings_raw) == 0:
        raise ConfigError("mappings must be a non-empty array")

    mappings: list[CalendarMapping] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(mappings_raw):
        obj = _as_dict(item, f"mappings[{index}]")
        name = _as_str(obj.get("name", f"mapping-{index + 1}"), f"mappings[{index}].name")
        google_calendar_id = _as_str(
            obj.get("google_calendar_id"), f"mappings[{index}].google_calendar_id"
        )
        nextcloud_calendar_url = _as_str(
            obj.get("nextcloud_calendar_url"), f"mappings[{index}].nextcloud_calendar_url"
        )
        mapping = CalendarMapping(
            name=name,
            google_calendar_id=google_calendar_id,
            nextcloud_calendar_url=nextcloud_calendar_url,
        )
        if mapping.mapping_key in seen_keys:
            raise ConfigError(f"duplicate mapping key: {mapping.mapping_key}")
        seen_keys.add(mapping.mapping_key)
        mappings.append(mapping)

    config = AppConfig(
        sqlite_path=_resolve_path(sqlite_path_str, config_path),
        lock_file=_resolve_path(lock_file_str, config_path),
        logging=LoggingConfig(level=logging_level.upper(), json=logging_json),
        google=GoogleAuthConfig(
            credentials_file=(
                _resolve_path(google_credentials_file, config_path)
                if google_credentials_file is not None
                else None
            ),
            credentials_json=google_credentials_json,
            token_file=_resolve_path(google_token_file, config_path),
            scopes=tuple(google_scopes),
        ),
        nextcloud=NextcloudConfig(
            username=nextcloud_username,
            app_password=nextcloud_password,
            timeout_seconds=nextcloud_timeout,
        ),
        mappings=tuple(mappings),
    )

    return config
