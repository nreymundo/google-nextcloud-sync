"""Configuration loader for g2nc.

This module provides:
- Typed config models (pydantic BaseModel)
- Precedence-aware loader: file (YAML) < ENV (G2NC__*) < CLI overrides
- Minimal coercion for ENV values (bool/int/float/list)

ENV format (nested via delimiter):
  G2NC__nextcloud__base_url=https://cloud.example.com
  G2NC__nextcloud__username=nc_user
  G2NC__nextcloud__app_password=secret
  G2NC__nextcloud__calendars__work=/remote.php/dav/calendars/nc_user/work/
  G2NC__sync__photo_sync=false

CLI overrides can pass a nested dict, e.g.:
  {"sync": {"photo_sync": False}, "logging": {"level": "DEBUG"}}

Example:
  cfg = load_config("/data/config.yaml", cli_overrides={"sync": {"dry_run": True}})
  print(cfg.nextcloud.base_url)
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

try:
    import yaml
except Exception as exc:  # pragma: no cover - hard dependency, but guard import
    raise RuntimeError("PyYAML is required. Install with `pip install PyYAML`.") from exc


# ----------------------------
# Pydantic models (typed config)
# ----------------------------


class GoogleConfig(BaseModel):
    credentials_file: str | None = None  # or GOOGLE_CREDENTIALS_JSON via ENV
    token_store: str = "/data/google_token.json"
    contact_groups: list[str] | None = None
    calendar_ids: dict[str, str] = Field(default_factory=dict)


class NextcloudConfig(BaseModel):
    base_url: str
    username: str
    app_password: str | None = None  # recommended to use ENV
    addressbook_path: str
    calendars: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        # Require explicit scheme to avoid misconfig
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("nextcloud.base_url must start with http:// or https://")
        return v

    @field_validator("addressbook_path")
    @classmethod
    def _validate_addressbook_path(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("nextcloud.addressbook_path must start with '/'")
        return v

    @field_validator("calendars")
    @classmethod
    def _validate_calendars(cls, v: dict[str, str]) -> dict[str, str]:
        for key, path in v.items():
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError(f"nextcloud.calendars['{key}'] must start with '/'")
        return v


class SyncConfig(BaseModel):
    photo_sync: bool = True
    overwrite_local: bool = True  # Google authoritative by default
    # Bounded resync window to limit load on token invalidation (1..1825 days ~= 5y)
    time_window_days: int = Field(730, ge=1, le=1825)
    # Guard batch sizes to avoid server overload (1..1000)
    batch_size: int = Field(200, ge=1, le=1000)
    # Retry cap to avoid cascades (0..10)
    max_retries: int = Field(5, ge=0, le=10)
    # Backoff seed to control retry pacing (>0..60s)
    backoff_initial_sec: float = Field(1.0, gt=0, le=60)
    dry_run: bool = False
    protect_local: bool = False  # convenience flag; if True, may override overwrite behavior


class StateConfig(BaseModel):
    db_path: str = "/data/state.sqlite"


class LoggingConfig(BaseModel):
    # Allow using alias "json" in config/env while avoiding BaseModel.json clash
    model_config = ConfigDict(populate_by_name=True)
    level: str = "INFO"
    as_json: bool = Field(True, alias="json")

    @field_validator("level")
    @classmethod
    def _normalize_level(cls, v: str) -> str:
        lv = (v or "INFO").upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if lv not in allowed:
            raise ValueError(f"logging.level must be one of {sorted(allowed)}")
        return lv


def _default_logging_config() -> LoggingConfig:
    return LoggingConfig(json=True)


class RuntimeConfig(BaseModel):
    lock_path: str = "/tmp/g2nc.lock"


# Additional default factories to satisfy mypy and avoid shared mutable defaults
def _default_google_config() -> GoogleConfig:
    return GoogleConfig()


def _default_sync_config() -> SyncConfig:
    return SyncConfig()


def _default_state_config() -> StateConfig:
    return StateConfig()


def _default_runtime_config() -> RuntimeConfig:
    return RuntimeConfig()


class AppConfig(BaseModel):
    google: GoogleConfig = Field(default_factory=_default_google_config)
    nextcloud: NextcloudConfig
    sync: SyncConfig = Field(default_factory=_default_sync_config)
    state: StateConfig = Field(default_factory=_default_state_config)
    logging: LoggingConfig = Field(default_factory=_default_logging_config)
    runtime: RuntimeConfig = Field(default_factory=_default_runtime_config)


__all__ = [
    "AppConfig",
    "GoogleConfig",
    "LoggingConfig",
    "NextcloudConfig",
    "RuntimeConfig",
    "StateConfig",
    "SyncConfig",
    "load_config",
    "merge_dicts",
    "read_env_config",
]


# ----------------------------
# Utilities
# ----------------------------


_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
_BOOL_FALSE = {"0", "false", "no", "off", "n", "f"}

_LIST_SPLIT_RE = re.compile(r"\s*,\s*")


def _coerce_value(val: str) -> Any:
    """Best-effort coercion for ENV values."""
    s = val.strip()

    # booleans
    ls = s.lower()
    if ls in _BOOL_TRUE:
        return True
    if ls in _BOOL_FALSE:
        return False

    # numbers (int then float)
    if re.fullmatch(r"[+-]?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    if re.fullmatch(r"[+-]?\d+\.\d*", s):
        try:
            return float(s)
        except ValueError:
            pass

    # lists (comma-separated)
    if "," in s:
        parts = [p for p in _LIST_SPLIT_RE.split(s) if p != ""]
        return parts

    return s


def merge_dicts(
    base: MutableMapping[str, Any], override: Mapping[str, Any]
) -> MutableMapping[str, Any]:
    """Deep-merge override into base (mutates base). Lists/atoms are replaced."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, Mapping):
            merge_dicts(base[k], v)
        else:
            base[k] = v
    return base


def read_yaml_config(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path).resolve()

    # Security: Validate path to prevent path traversal attacks
    # Allow common config locations but prevent access to sensitive system files
    allowed_prefixes = [
        Path.home(),  # User home directory
        Path("/data"),  # Docker data volume
        Path("/opt/g2nc"),  # Application directory
        Path("/etc/g2nc"),  # System config directory
        Path.cwd(),  # Current working directory
        Path("/tmp"),  # Temporary directory (for tests)
        Path("/var/tmp"),  # Additional temp directory
    ]

    # Check if path is within allowed locations
    path_allowed = False
    for prefix in allowed_prefixes:
        try:
            p.relative_to(prefix.resolve())
            path_allowed = True
            break
        except ValueError:
            continue

    if not path_allowed:
        raise ValueError(
            f"Configuration file path '{p}' is outside allowed directories. "
            f"Allowed prefixes: {[str(prefix) for prefix in allowed_prefixes]}"
        )

    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Top-level YAML must be a mapping in {p}")
        return data


def read_env_config(prefix: str = "G2NC__", nested_delim: str = "__") -> dict[str, Any]:
    """Build nested dict from environment variables.

    Keys must start with `prefix` (default 'G2NC__').
    Nested keys split by `nested_delim`.

    Example:
      G2NC__nextcloud__base_url=...
      G2NC__sync__time_window_days=730
    """
    if not prefix.endswith(nested_delim):
        # keep behavior stable & explicit
        raise ValueError("prefix must end with the nested_delim (default 'G2NC__' and '__').")

    result: dict[str, Any] = {}
    plen = len(prefix)
    for key, raw in os.environ.items():
        if not key.startswith(prefix):
            continue
        path_parts = key[plen:].split(nested_delim)
        cursor = result
        for part in path_parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[path_parts[-1]] = _coerce_value(raw)
    return result


# ----------------------------
# Loader (precedence: file < env < cli_overrides)
# ----------------------------


def load_config(
    file_path: str | Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    env_prefix: str = "G2NC__",
    env_nested_delim: str = "__",
) -> AppConfig:
    """Load AppConfig with precedence: file < env < CLI overrides.

    Args:
        file_path: YAML path or None
        cli_overrides: nested mapping of overrides (e.g., from CLI args)
        env_prefix: environment variable prefix (must end with env_nested_delim)
        env_nested_delim: nested delimiter for env vars

    Returns:
        AppConfig instance (validated)
    """
    merged: dict[str, Any] = {}

    # 1) file
    file_data = read_yaml_config(Path(file_path) if file_path else None)
    merge_dicts(merged, file_data)

    # 2) env
    env_data = read_env_config(prefix=env_prefix, nested_delim=env_nested_delim)
    merge_dicts(merged, env_data)

    # 3) CLI overrides
    if cli_overrides:
        if not isinstance(cli_overrides, Mapping):
            raise TypeError("cli_overrides must be a mapping (nested dict-like).")
        merge_dicts(merged, cli_overrides)

    try:
        return AppConfig.model_validate(merged)
    except ValidationError as ve:
        # Re-raise with friendly message
        raise ValueError(f"Invalid configuration: {ve}") from ve


# ----------------------------
# Simple smoke self-test (manual)
# ----------------------------

if __name__ == "__main__":  # pragma: no cover
    # Minimal manual run example
    cfg = load_config(
        None,
        cli_overrides={
            "nextcloud": {"base_url": "https://x", "username": "u", "addressbook_path": "/dav/addr"}
        },
    )
    print(cfg.model_dump())
