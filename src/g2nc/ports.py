from __future__ import annotations

from typing import Protocol

from g2nc.models import CalendarChanges, CalendarEvent, UpsertResult


class SyncTokenInvalidatedError(RuntimeError):
    pass


class GoogleCalendarPort(Protocol):
    def fetch_event_changes(self, calendar_id: str, sync_token: str | None) -> CalendarChanges: ...


class NextcloudCalendarPort(Protocol):
    def upsert_event(
        self,
        calendar_url: str,
        uid: str,
        event: CalendarEvent,
        known_href: str | None,
        known_etag: str | None,
    ) -> UpsertResult: ...

    def delete_event(self, calendar_url: str, href: str, etag: str | None) -> None: ...
