from __future__ import annotations

import json
from typing import Any, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from g2nc.models import CalendarChanges, CalendarEvent, GoogleAuthConfig
from g2nc.ports import SyncTokenInvalidatedError


class GoogleAuthError(RuntimeError):
    pass


class GoogleCalendarClient:
    def __init__(self, auth: GoogleAuthConfig) -> None:
        self._auth = auth

    def fetch_event_changes(self, calendar_id: str, sync_token: str | None) -> CalendarChanges:
        service = self._build_service()
        params: dict[str, Any] = {
            "calendarId": calendar_id,
            "showDeleted": True,
            "maxResults": 2500,
        }
        if sync_token:
            params["syncToken"] = sync_token

        events: list[CalendarEvent] = []
        next_page_token: str | None = None
        next_sync_token: str | None = None

        while True:
            if next_page_token:
                params["pageToken"] = next_page_token
            else:
                params.pop("pageToken", None)

            events_api = service.events()
            request = events_api.list(**params)
            try:
                response = request.execute()
            except HttpError as exc:
                if exc.resp.status == 410:
                    raise SyncTokenInvalidatedError("google sync token invalidated") from exc
                raise

            items = response.get("items", [])
            if not isinstance(items, list):
                raise GoogleAuthError("unexpected Google API response: items is not a list")

            for raw in items:
                event = self._map_event(raw)
                if event is not None:
                    events.append(event)

            next_page_token = cast(str | None, response.get("nextPageToken"))
            if next_page_token is None:
                next_sync_token = cast(str | None, response.get("nextSyncToken"))
                break

        if next_sync_token is None:
            raise GoogleAuthError("Google API response missing nextSyncToken")

        return CalendarChanges(events=tuple(events), next_sync_token=next_sync_token)

    def _build_service(self) -> Any:
        if not self._auth.token_file.exists():
            raise GoogleAuthError(
                f"Google token file not found: {self._auth.token_file}. Run auth bootstrap first."
            )

        token_payload = self._auth.token_file.read_text(encoding="utf-8")
        token_data = json.loads(token_payload)
        if not isinstance(token_data, dict):
            raise GoogleAuthError("token file JSON must be an object")

        credentials = Credentials.from_authorized_user_info(token_data, self._auth.scopes)
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                self._auth.token_file.write_text(credentials.to_json(), encoding="utf-8")
            else:
                raise GoogleAuthError(
                    f"Google token is invalid and cannot be refreshed: {self._auth.token_file}. "
                    "Run auth bootstrap again."
                )

        return build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _map_event(self, raw: Any) -> CalendarEvent | None:
        if not isinstance(raw, dict):
            return None

        event_id_raw = raw.get("id")
        if not isinstance(event_id_raw, str) or event_id_raw.strip() == "":
            return None

        deleted = raw.get("status") == "cancelled"
        if deleted:
            return CalendarEvent(
                google_event_id=event_id_raw,
                deleted=True,
                title="",
                description=None,
                location=None,
                start_raw="",
                end_raw="",
                all_day=False,
                recurrence=(),
            )

        start = raw.get("start")
        end = raw.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            return None

        all_day = isinstance(start.get("date"), str) and isinstance(end.get("date"), str)
        if all_day:
            start_raw = cast(str, start["date"])
            end_raw = cast(str, end["date"])
        else:
            start_dt = start.get("dateTime")
            end_dt = end.get("dateTime")
            if not isinstance(start_dt, str) or not isinstance(end_dt, str):
                return None
            start_raw = start_dt
            end_raw = end_dt

        recurrence_raw = raw.get("recurrence", [])
        recurrence: tuple[str, ...]
        if isinstance(recurrence_raw, list) and all(
            isinstance(item, str) for item in recurrence_raw
        ):
            recurrence = tuple(recurrence_raw)
        else:
            recurrence = ()

        title_raw = raw.get("summary")
        description_raw = raw.get("description")
        location_raw = raw.get("location")

        return CalendarEvent(
            google_event_id=event_id_raw,
            deleted=False,
            title=title_raw if isinstance(title_raw, str) else "",
            description=description_raw if isinstance(description_raw, str) else None,
            location=location_raw if isinstance(location_raw, str) else None,
            start_raw=start_raw,
            end_raw=end_raw,
            all_day=all_day,
            recurrence=recurrence,
        )
