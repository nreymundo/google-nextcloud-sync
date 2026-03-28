from __future__ import annotations

import logging

from g2nc.models import CalendarMapping, EventState
from g2nc.ports import GoogleCalendarPort, NextcloudCalendarPort, SyncTokenInvalidatedError
from g2nc.state import SqliteStateRepository
from g2nc.transform import event_payload_hash, event_uid


class SyncService:
    def __init__(
        self,
        google: GoogleCalendarPort,
        nextcloud: NextcloudCalendarPort,
        state: SqliteStateRepository,
    ) -> None:
        self._google = google
        self._nextcloud = nextcloud
        self._state = state
        self._logger = logging.getLogger(__name__)

    def sync_mapping(self, mapping: CalendarMapping) -> None:
        self._logger.info("sync mapping started", extra={"mapping": mapping.name})
        previous_token = self._state.get_sync_token(mapping.mapping_key)

        try:
            changes = self._google.fetch_event_changes(mapping.google_calendar_id, previous_token)
        except SyncTokenInvalidatedError:
            self._logger.warning(
                "google sync token invalidated, resetting token",
                extra={"mapping": mapping.name},
            )
            self._state.clear_sync_token(mapping.mapping_key)
            changes = self._google.fetch_event_changes(mapping.google_calendar_id, None)

        for event in changes.events:
            state_row = self._state.get_event_state(mapping.mapping_key, event.google_event_id)
            if event.deleted:
                if state_row is not None:
                    self._nextcloud.delete_event(
                        calendar_url=mapping.nextcloud_calendar_url,
                        href=state_row.href,
                        etag=state_row.etag,
                    )
                    self._state.delete_event_state(mapping.mapping_key, event.google_event_id)
                continue

            uid = event_uid(mapping.google_calendar_id, event.google_event_id)
            payload_hash = event_payload_hash(event)
            if state_row is not None and state_row.payload_hash == payload_hash:
                continue

            result = self._nextcloud.upsert_event(
                calendar_url=mapping.nextcloud_calendar_url,
                uid=uid,
                event=event,
                known_href=state_row.href if state_row is not None else None,
                known_etag=state_row.etag if state_row is not None else None,
            )
            self._state.upsert_event_state(
                EventState(
                    mapping_key=mapping.mapping_key,
                    google_event_id=event.google_event_id,
                    uid=uid,
                    href=result.href,
                    etag=result.etag,
                    payload_hash=payload_hash,
                )
            )

        self._state.set_sync_token(mapping.mapping_key, changes.next_sync_token)
        self._logger.info("sync mapping completed", extra={"mapping": mapping.name})
