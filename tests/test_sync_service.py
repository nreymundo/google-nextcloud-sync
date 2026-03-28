from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from g2nc.models import CalendarChanges, CalendarEvent, CalendarMapping, UpsertResult
from g2nc.ports import SyncTokenInvalidatedError
from g2nc.state import SqliteStateRepository
from g2nc.sync_service import SyncService


@dataclass
class _GoogleStub:
    sequences: list[CalendarChanges]
    raise_invalid_on_token: bool = False

    def __post_init__(self) -> None:
        self.calls: list[str | None] = []

    def fetch_event_changes(self, calendar_id: str, sync_token: str | None) -> CalendarChanges:
        del calendar_id
        self.calls.append(sync_token)
        if self.raise_invalid_on_token and sync_token is not None:
            self.raise_invalid_on_token = False
            raise SyncTokenInvalidatedError("invalid token")
        return self.sequences.pop(0)


@dataclass
class _NextcloudStub:
    def __post_init__(self) -> None:
        self.upserts: list[tuple[str, str, str | None, str | None]] = []
        self.deletes: list[tuple[str, str, str | None]] = []

    def upsert_event(
        self,
        calendar_url: str,
        uid: str,
        event: CalendarEvent,
        known_href: str | None,
        known_etag: str | None,
    ) -> UpsertResult:
        self.upserts.append((calendar_url, uid, known_href, known_etag))
        return UpsertResult(
            href=(known_href if known_href is not None else f"{event.google_event_id}.ics"),
            etag='"new"',
        )

    def delete_event(self, calendar_url: str, href: str, etag: str | None) -> None:
        self.deletes.append((calendar_url, href, etag))


def _event(event_id: str, title: str = "Title") -> CalendarEvent:
    return CalendarEvent(
        google_event_id=event_id,
        deleted=False,
        title=title,
        description="desc",
        location="loc",
        start_raw="2026-01-01T10:00:00Z",
        end_raw="2026-01-01T11:00:00Z",
        all_day=False,
        recurrence=(),
    )


def _deleted_event(event_id: str) -> CalendarEvent:
    return CalendarEvent(
        google_event_id=event_id,
        deleted=True,
        title="",
        description=None,
        location=None,
        start_raw="",
        end_raw="",
        all_day=False,
        recurrence=(),
    )


def _mapping() -> CalendarMapping:
    return CalendarMapping(
        name="work",
        google_calendar_id="primary",
        nextcloud_calendar_url="https://cloud.example/remote.php/dav/calendars/alice/work/",
    )


def _state(tmp_path: Path) -> SqliteStateRepository:
    repo = SqliteStateRepository(tmp_path / "state.sqlite")
    repo.initialize()
    return repo


def test_sync_is_idempotent_for_same_payload(tmp_path: Path) -> None:
    mapping = _mapping()
    state = _state(tmp_path)

    google = _GoogleStub(
        sequences=[
            CalendarChanges(events=(_event("evt-1"),), next_sync_token="sync-1"),
            CalendarChanges(events=(_event("evt-1"),), next_sync_token="sync-2"),
        ]
    )
    nextcloud = _NextcloudStub()
    service = SyncService(google=google, nextcloud=nextcloud, state=state)

    service.sync_mapping(mapping)
    service.sync_mapping(mapping)

    assert len(nextcloud.upserts) == 1
    assert state.get_sync_token(mapping.mapping_key) == "sync-2"


def test_sync_deletes_cancelled_event(tmp_path: Path) -> None:
    mapping = _mapping()
    state = _state(tmp_path)

    google = _GoogleStub(
        sequences=[
            CalendarChanges(events=(_event("evt-1"),), next_sync_token="sync-1"),
            CalendarChanges(events=(_deleted_event("evt-1"),), next_sync_token="sync-2"),
        ]
    )
    nextcloud = _NextcloudStub()
    service = SyncService(google=google, nextcloud=nextcloud, state=state)

    service.sync_mapping(mapping)
    service.sync_mapping(mapping)

    assert len(nextcloud.deletes) == 1
    assert state.get_event_state(mapping.mapping_key, "evt-1") is None


def test_sync_resets_token_on_google_410(tmp_path: Path) -> None:
    mapping = _mapping()
    state = _state(tmp_path)
    state.set_sync_token(mapping.mapping_key, "old-sync-token")

    google = _GoogleStub(
        sequences=[CalendarChanges(events=(_event("evt-1"),), next_sync_token="fresh-sync-token")],
        raise_invalid_on_token=True,
    )
    nextcloud = _NextcloudStub()
    service = SyncService(google=google, nextcloud=nextcloud, state=state)

    service.sync_mapping(mapping)

    assert google.calls == ["old-sync-token", None]
    assert state.get_sync_token(mapping.mapping_key) == "fresh-sync-token"
