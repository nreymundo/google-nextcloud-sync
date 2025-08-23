"""Google People API client (incremental connections list with sync tokens).

This client provides a minimal wrapper needed by the sync engine:

- iterate_changes(sync_token: str | None, page_size: int = 200) -> tuple[Iterator[ContactChange], str | None]
  Yields ContactChange items (deleted or active) and returns the nextSyncToken (if present).

Notes
- The People API uses `people.connections.list` with `requestSyncToken=true` to produce
  incremental updates. Pass a `syncToken` obtained from previous runs to receive only changes.
- Deleted contacts are surfaced (per PRD) via `person["metadata"]["deleted"] == True`.
- You must specify `personFields` explicitly; keep it configurable to avoid API under-fetching.

References:
- https://developers.google.com/people/api/rest/v1/people.connections/list
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

try:
    from googleapiclient.discovery import build as gapi_build  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    gapi_build = None  # defer import error until client construction

logger = logging.getLogger(__name__)


DEFAULT_PERSON_FIELDS = ",".join(
    [
        "names",
        "nicknames",
        "emailAddresses",
        "phoneNumbers",
        "urls",
        "organizations",
        "biographies",
        "birthdays",
        "addresses",
        "photos",
        "memberships",  # for groups/categories
        "metadata",
    ]
)


@dataclass(frozen=True)
class ContactChange:
    google_id: str  # people/cNNNN
    person: dict[str, Any]  # full People API person payload (when not deleted)
    deleted: bool
    etag: str | None


class PeopleClient:
    def __init__(self, credentials: Any, *, person_fields: str = DEFAULT_PERSON_FIELDS) -> None:
        if gapi_build is None:  # pragma: no cover
            raise RuntimeError(
                "google-api-python-client is required. Install it to use PeopleClient."
            )
        # Using cache_discovery=False avoids cached discovery docs (safer in distributed envs)
        self._svc = gapi_build("people", "v1", credentials=credentials, cache_discovery=False)
        self._person_fields = person_fields

    def iterate_changes(
        self,
        *,
        sync_token: str | None,
        page_size: int = 200,
        contact_group_ids: list[str] | None = None,
    ) -> tuple[Iterator[ContactChange], str | None]:
        """Return an iterator of ContactChange and the nextSyncToken.

        Eagerly fetches all pages to compute nextSyncToken up-front (simplifies callers/tests).
        """
        items_acc: list[ContactChange] = []
        next_tok: str | None = None
        page_token: str | None = None

        while True:
            req = (
                self._svc.people()  # type: ignore[no-untyped-call]
                .connections()
                .list(
                    resourceName="people/me",
                    pageToken=page_token,
                    pageSize=page_size,
                    personFields=self._person_fields,
                    requestSyncToken=True,
                    syncToken=sync_token,
                    sortOrder="LAST_MODIFIED_ASCENDING",
                    **({"sources": ["READ_SOURCE_TYPE_CONTACT"]} if True else {}),
                )
            )
            resp = req.execute()  # type: ignore[no-untyped-call]
            items = resp.get("connections", []) or []

            for person in items:
                md = person.get("metadata", {}) or {}
                deleted = bool(md.get("deleted", False))
                google_id = person.get("resourceName")
                if not google_id:
                    continue
                if contact_group_ids and not _in_any_group(person, contact_group_ids):
                    continue
                etag = md.get("sources", [{}])[0].get("etag") if md.get("sources") else None
                if deleted:
                    items_acc.append(
                        ContactChange(google_id=google_id, person={}, deleted=True, etag=etag)
                    )
                else:
                    items_acc.append(
                        ContactChange(google_id=google_id, person=person, deleted=False, etag=etag)
                    )

            page_token = resp.get("nextPageToken")
            if not page_token:
                next_tok = resp.get("nextSyncToken")
                break

        # Always perform a lightweight follow-up to capture a stable nextSyncToken
        # (the API commonly returns it only on the final/empty page).
        try:
            req2 = (
                self._svc.people()  # type: ignore[no-untyped-call]
                .connections()
                .list(
                    resourceName="people/me",
                    pageSize=1,
                    personFields=self._person_fields,
                    requestSyncToken=True,
                    syncToken=sync_token,
                    sortOrder="LAST_MODIFIED_ASCENDING",
                )
            )
            resp2 = req2.execute()  # type: ignore[no-untyped-call]
            tok2 = resp2.get("nextSyncToken")
            if tok2:
                next_tok = tok2
        except Exception:  # pragma: no cover
            pass

        return iter(items_acc), next_tok


def _in_any_group(person: dict[str, Any], group_ids: list[str]) -> bool:
    mships = person.get("memberships") or []
    ids = {g for g in group_ids}
    for m in mships:
        g = (m.get("contactGroupMembership") or {}).get("contactGroupResourceName")
        if g and g in ids:
            return True
    return False
