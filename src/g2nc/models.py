from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    json: bool


@dataclass(frozen=True)
class GoogleAuthConfig:
    credentials_file: Path | None
    credentials_json: str | None
    token_file: Path
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class NextcloudConfig:
    username: str
    app_password: str
    timeout_seconds: int


@dataclass(frozen=True)
class CalendarMapping:
    name: str
    google_calendar_id: str
    nextcloud_calendar_url: str

    @property
    def mapping_key(self) -> str:
        return f"{self.google_calendar_id}|{self.nextcloud_calendar_url}"


@dataclass(frozen=True)
class AppConfig:
    sqlite_path: Path
    lock_file: Path
    logging: LoggingConfig
    google: GoogleAuthConfig
    nextcloud: NextcloudConfig
    mappings: tuple[CalendarMapping, ...]


@dataclass(frozen=True)
class CalendarEvent:
    google_event_id: str
    deleted: bool
    title: str
    description: str | None
    location: str | None
    start_raw: str
    end_raw: str
    all_day: bool
    recurrence: tuple[str, ...]


@dataclass(frozen=True)
class CalendarChanges:
    events: tuple[CalendarEvent, ...]
    next_sync_token: str


@dataclass(frozen=True)
class EventState:
    mapping_key: str
    google_event_id: str
    uid: str
    href: str
    etag: str | None
    payload_hash: str


@dataclass(frozen=True)
class UpsertResult:
    href: str
    etag: str | None
