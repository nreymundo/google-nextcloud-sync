"""Contacts sync engine (Google People API → Nextcloud CardDAV).

Implements PRD §17 contacts pseudocode with:
- Idempotency via UID = Google resourceName and normalized content hashing
- Incremental processing using People API sync tokens
- Dry-run support (no state or remote writes)
- Batching via config.sync.batch_size

Note:
- This engine assumes CardDAV client can find-by-UID and PUT/DELETE; initial scaffold may raise
  NotImplementedError until those methods are completed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

# Gracefully handle missing google client at import time for typing/runtime robustness
try:  # pragma: no cover
    from googleapiclient.errors import HttpError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    class HttpError(Exception):  # type: ignore[no-redef]
        pass

from ..config import AppConfig
from ..google.contacts import PeopleClient
from ..mapping.contacts import person_to_vcard
from ..nextcloud.carddav import CardDAVClient
from ..state import State
from ..utils.hashing import hash_vcard

log = logging.getLogger(__name__)


def _is_expired_sync_token_error(exc: Exception) -> bool:
    """Detect People API EXPIRED_SYNC_TOKEN errors.

    The Google API client raises HttpError with content including:
      reason: 'EXPIRED_SYNC_TOKEN'
      or message: 'Sync token is expired. Clear local cache and retry call without the sync token.'
    """
    try:
        if isinstance(exc, HttpError):
            # Prefer structured content, fall back to string matching
            msg = ""
            try:
                raw = getattr(exc, "content", b"")
                if isinstance(raw, bytes):
                    msg = raw.decode("utf-8", errors="ignore")
                else:
                    msg = str(raw)
            except Exception:
                msg = str(exc)
            lm = msg.lower()
            return ("expired_sync_token" in lm) or ("sync token is expired" in lm)
    except Exception:
        return False
    return False


@dataclass(frozen=True)
class ContactsSyncResult:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0


class ContactsSync:
    def __init__(
        self,
        cfg: AppConfig,
        state: State,
        people: PeopleClient,
        carddav: CardDAVClient,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.people = people
        self.carddav = carddav

    def run(self, *, dry_run: bool = False, reset_token: bool = False) -> ContactsSyncResult:
        """Run a single incremental contacts sync pass."""
        scope = "contacts"
        token: str | None = None if reset_token else self.state.get_token(scope)

        # Handle EXPIRED_SYNC_TOKEN by clearing local token and retrying once (bounded/full resync for People API)
        next_token: str | None = None
        attempt = 0
        while True:
            try:
                iter_changes, next_token = self.people.iterate_changes(
                    sync_token=token,
                    page_size=self.cfg.sync.batch_size,
                    contact_group_ids=self.cfg.google.contact_groups,
                )
                break
            except HttpError as exc:
                if _is_expired_sync_token_error(exc) and attempt == 0:
                    log.warning("people-sync-token-expired-resetting; clearing local token and retrying without sync_token")
                    token = None
                    attempt += 1
                    if not dry_run:
                        try:
                            self.state.reset_token(scope)
                        except Exception:
                            # Non-fatal; continue with retry
                            pass
                    continue
                # Any other API error (or repeated failure) is fatal to this pass
                raise

        # Convert to mutable dataclass mimic
        fetched = created = updated = deleted = skipped = errors = 0

        for change in iter_changes:
            fetched += 1
            try:
                if change.deleted:
                    del_href = self.state.lookup_contact_href(change.google_id)
                    if del_href and not dry_run:
                        try:
                            self.carddav.delete(del_href, etag=None)
                        except NotImplementedError:
                            # Scaffold: skip until implemented
                            pass
                    if not dry_run:
                        self.state.remove_contact(change.google_id)
                    deleted += 1
                    continue

                # Map to vCard text
                vcard_text = person_to_vcard(
                    change.person,
                    version="4.0",  # PRD default; config switch can be added later
                    categories_from_groups=True,
                    include_photo_uri=self.cfg.sync.photo_sync,
                )
                content_hash = hash_vcard(vcard_text)

                rec = self.state.get_contact(change.google_id)
                if rec and rec.content_hash == content_hash and not bool(rec.deleted):
                    skipped += 1
                    continue

                # Determine target href/etag (existing or by UID search)
                href: str | None = rec.nextcloud_href if rec else None
                etag: str | None = rec.etag if rec else None

                if href is None:
                    try:
                        found = self.carddav.find_by_uid(change.google_id)
                        if found:
                            href, etag = found.href, found.etag
                    except NotImplementedError:
                        pass

                if not dry_run:
                    try:
                        new_href, new_etag = self.carddav.put_vcard(
                            vcard_text, href=href, etag=etag
                        )
                    except NotImplementedError:
                        new_href, new_etag = href or "", etag
                    self.state.upsert_contact(
                        change.google_id, new_href, new_etag, content_hash, deleted=0
                    )

                if href:
                    updated += 1
                else:
                    created += 1

            except Exception:  # pragma: no cover - error path
                errors += 1
                log.exception("contacts-sync-error", extra={"google_id": change.google_id})

        # Persist token only on non-dry runs
        if not dry_run and next_token:
            self.state.save_token(scope, next_token)

        return ContactsSyncResult(
            fetched=fetched,
            created=created,
            updated=updated,
            deleted=deleted,
            skipped=skipped,
            errors=errors,
        )
