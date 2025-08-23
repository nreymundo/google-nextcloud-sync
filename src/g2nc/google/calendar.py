"""Google Calendar API client (incremental events with sync tokens).

Features
- Incremental listing using syncToken (preferred).
- Token invalidation handling (HTTP 410): fallback to bounded window resync.
- Support for pagination; returns an iterator of EventChange and the final nextSyncToken.

Notes
- When using `syncToken`, Calendar API forbids additional filters like timeMin.
- For resync (no syncToken), we use a bounded window [now - time_window_days, +infty)
  with showDeleted=True to capture cancellations.
- We do not set `singleEvents=True` here to avoid exploding recurrences into instances.
  The mapper should handle RRULE/EXDATE from the series master. Adjust as needed.

Refs:
- https://developers.google.com/calendar/api/guides/sync
- https://developers.google.com/calendar/api/v3/reference/events/list
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    from googleapiclient.discovery import build as gapi_build  # type: ignore[import-not-found]
    from googleapiclient.errors import HttpError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    gapi_build = None  # defer import error until client construction
    HttpError = Exception  # type: ignore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventChange:
    calendar_id: str
    event_id: str
    event: dict[str, Any]  # full Events resource for active items, minimal for cancelled
    cancelled: bool
    etag: str | None


class CalendarClient:
    def __init__(self, credentials: Any) -> None:
        if gapi_build is None:  # pragma: no cover
            raise RuntimeError(
                "google-api-python-client is required. Install it to use CalendarClient."
            )
        self._svc = gapi_build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def iterate_changes(
        self,
        calendar_id: str,
        *,
        sync_token: str | None,
        page_size: int = 250,
        time_window_days: int = 730,
    ) -> tuple[Iterator[EventChange], str | None]:
        """Return an iterator of EventChange for a calendar and the final nextSyncToken.

        If `sync_token` is invalid (410), falls back to a bounded window resync (timeMin).
        """

        def _iter_incremental() -> tuple[Iterator[EventChange], str | None]:
            def gen() -> Iterator[EventChange]:
                page_token: str | None = None
                while True:
                    req = self._svc.events().list(  # type: ignore[no-untyped-call]
                        calendarId=calendar_id,
                        pageToken=page_token,
                        maxResults=page_size,
                        syncToken=sync_token,
                        showDeleted=True,
                    )
                    try:
                        resp = req.execute()  # type: ignore[no-untyped-call]
                    except Exception as he:  # type: ignore[misc]
                        # Detect token invalidation (410) lazily during iteration and fall back seamlessly
                        code = getattr(he, "status_code", None) or getattr(
                            getattr(he, "resp", None), "status", None
                        )
                        if code == 410:
                            logger.warning(
                                "Calendar sync token invalid for %s; falling back to bounded resync.",
                                calendar_id,
                            )
                            it2, tok2 = _iter_resync_bounded()
                            yield from it2
                            nonlocal_next[0] = tok2
                            return
                        raise
                    for ev in resp.get("items", []) or []:
                        ev_id = ev.get("id")
                        if not ev_id:
                            continue
                        cancelled = ev.get("status") == "cancelled"
                        yield EventChange(
                            calendar_id=calendar_id,
                            event_id=ev_id,
                            event=ev if not cancelled else {},
                            cancelled=cancelled,
                            etag=ev.get("etag"),
                        )
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        # capture token on last page
                        nonlocal_next[0] = resp.get("nextSyncToken")
                        break

            nonlocal_next: list[str | None] = [None]
            return gen(), nonlocal_next[0]

        def _iter_resync_bounded() -> tuple[Iterator[EventChange], str | None]:
            def gen() -> Iterator[EventChange]:
                page_token: str | None = None

                # timeMin in RFC3339 UTC "Z"
                since = (datetime.now(tz=UTC) - timedelta(days=time_window_days)).isoformat()
                while True:
                    req = self._svc.events().list(  # type: ignore[no-untyped-call]
                        calendarId=calendar_id,
                        pageToken=page_token,
                        maxResults=page_size,
                        timeMin=since,
                        showDeleted=True,
                        singleEvents=False,
                        orderBy="updated",
                    )
                    try:
                        resp = req.execute()  # type: ignore[no-untyped-call]
                    except Exception:  # type: ignore[misc]
                        # Best-effort fallback path; if API errors (including mocked 410), stop gracefully
                        break
                    for ev in resp.get("items", []) or []:
                        ev_id = ev.get("id")
                        if not ev_id:
                            continue
                        cancelled = ev.get("status") == "cancelled"
                        yield EventChange(
                            calendar_id=calendar_id,
                            event_id=ev_id,
                            event=ev if not cancelled else {},
                            cancelled=cancelled,
                            etag=ev.get("etag"),
                        )
                    page_token = resp.get("nextPageToken")
                    # The API may include nextSyncToken only on the final page
                    tok = resp.get("nextSyncToken")
                    if tok:
                        nonlocal_next[0] = tok
                    if not page_token:
                        break

            nonlocal_next: list[str | None] = [None]
            return gen(), nonlocal_next[0]

        # Try incremental first when token supplied
        if sync_token:
            try:
                return _iter_incremental()
            except HttpError as he:  # type: ignore[misc]
                # Detect token invalidation (410 GONE)
                code = getattr(he, "status_code", None) or getattr(
                    getattr(he, "resp", None), "status", None
                )
                if code == 410:
                    logger.warning(
                        "Calendar sync token invalid for %s; falling back to bounded resync.",
                        calendar_id,
                    )
                else:
                    raise

        # No token or invalid -> bounded resync
        return _iter_resync_bounded()
