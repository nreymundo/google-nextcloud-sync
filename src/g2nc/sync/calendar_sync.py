"""Calendar sync engine (Google Calendar → Nextcloud CalDAV).

Implements PRD §17 calendar pseudocode with:
- Incremental processing via Calendar API nextSyncToken per calendar
- UID = Google event.id; normalized hashing to avoid unnecessary PUTs
- Dry-run support; batching via config.sync.batch_size

Notes:
- This engine expects CalDAV client to support find-by-UID and PUT/DELETE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import AppConfig
from ..google.calendar import CalendarClient
from ..mapping.events import event_to_ics
from ..nextcloud.caldav import CalDAVClient
from ..state import State
from ..utils.hashing import hash_ics

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalendarSyncResult:
    calendar_id: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0


class CalendarSync:
    def __init__(
        self,
        cfg: AppConfig,
        state: State,
        gcal: CalendarClient,
        caldav: CalDAVClient,
        calendar_id: str,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.gcal = gcal
        self.caldav = caldav
        self.calendar_id = calendar_id

    def run(self, *, dry_run: bool = False, reset_token: bool = False) -> CalendarSyncResult:
        """Run a single incremental calendar sync pass for one calendar."""
        scope = f"calendar:{self.calendar_id}"
        token: str | None = None if reset_token else self.state.get_token(scope)

        iter_changes, next_token = self.gcal.iterate_changes(
            self.calendar_id,
            sync_token=token,
            page_size=self.cfg.sync.batch_size,
            time_window_days=self.cfg.sync.time_window_days,
        )

        fetched = created = updated = deleted = skipped = errors = 0

        for change in iter_changes:
            fetched += 1
            try:
                if change.cancelled:
                    del_href = self.state.lookup_event_href(self.calendar_id, change.event_id)
                    if del_href and not dry_run:
                        try:
                            self.caldav.delete(del_href, etag=None)
                        except NotImplementedError:
                            pass
                    if not dry_run:
                        self.state.remove_event(self.calendar_id, change.event_id)
                    deleted += 1
                    continue

                # Map to ICS text
                ics_text = event_to_ics(
                    change.event,
                    default_tz="UTC",
                    include_valarm=False,  # config option later
                )
                content_hash = hash_ics(ics_text)

                rec = self.state.get_event(self.calendar_id, change.event_id)
                if rec and rec.content_hash == content_hash and not bool(rec.deleted):
                    skipped += 1
                    continue

                # Determine target href/etag (existing or by UID search)
                href: str | None = rec.nextcloud_href if rec else None
                etag: str | None = rec.etag if rec else None

                if href is None:
                    try:
                        found = self.caldav.find_by_uid(change.event_id)
                        if found:
                            href, etag = found.href, found.etag
                    except NotImplementedError:
                        pass

                if not dry_run:
                    try:
                        new_href, new_etag = self.caldav.put_ics(ics_text, href=href, etag=etag)
                    except NotImplementedError:
                        new_href, new_etag = href or "", etag
                    self.state.upsert_event(
                        self.calendar_id,
                        change.event_id,
                        new_href,
                        new_etag,
                        content_hash,
                        deleted=0,
                    )

                if href:
                    updated += 1
                else:
                    created += 1

            except Exception:
                errors += 1
                log.exception(
                    "calendar-sync-error",
                    extra={"calendar_id": self.calendar_id, "event_id": change.event_id},
                )

        if not dry_run and next_token:
            self.state.save_token(scope, next_token)

        return CalendarSyncResult(
            calendar_id=self.calendar_id,
            fetched=fetched,
            created=created,
            updated=updated,
            deleted=deleted,
            skipped=skipped,
            errors=errors,
        )
