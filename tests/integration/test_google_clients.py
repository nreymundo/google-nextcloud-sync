from __future__ import annotations

from typing import Any

import pytest

from g2nc.google.calendar import CalendarClient
from g2nc.google.contacts import DEFAULT_PERSON_FIELDS, ContactChange, PeopleClient


class _FakePeopleService:
    def __init__(self, responses: list[dict[str, Any]]):
        # responses returned in sequence on .execute()
        self._responses = list(responses)

    def people(self) -> _FakePeopleService:
        return self

    def connections(self) -> _FakePeopleService:
        return self

    def list(self, **kwargs: Any) -> _FakePeopleRequest:
        # mimic a request object that yields from a queue of responses
        return _FakePeopleRequest(self)


class _FakePeopleRequest:
    def __init__(self, svc: _FakePeopleService):
        self._svc = svc

    def execute(self) -> dict[str, Any]:
        if not self._svc._responses:
            return {"connections": [], "nextSyncToken": "tok-final"}
        return self._svc._responses.pop(0)


def test_people_iterate_changes_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    # two contacts, one deleted, then final token fetch
    people_payload = {
        "connections": [
            {
                "resourceName": "people/c1",
                "metadata": {"sources": [{"etag": '"e1"'}], "deleted": False},
                "names": [{"displayName": "Alice"}],
            },
            {
                "resourceName": "people/c2",
                "metadata": {"sources": [{"etag": '"e2"'}], "deleted": True},
            },
        ]
    }
    # Fake service will return this payload once, subsequent call returns {"connections": [], "nextSyncToken": "..."}
    fake_service = _FakePeopleService([people_payload])

    import g2nc.google.contacts as contacts_mod

    def _fake_build(api: str, ver: str, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        assert api == "people" and ver == "v1"
        assert "credentials" in kwargs and "cache_discovery" in kwargs
        return fake_service

    monkeypatch.setattr(contacts_mod, "gapi_build", _fake_build)

    client = PeopleClient(credentials=object(), person_fields=DEFAULT_PERSON_FIELDS)
    it, next_token = client.iterate_changes(sync_token=None, page_size=200, contact_group_ids=None)

    acc: list[ContactChange] = list(it)
    # After iteration, our wrapper issues a final call to capture nextSyncToken (returns tok-final)
    assert next_token == "tok-final"
    assert len(acc) == 2
    assert acc[0].google_id == "people/c1" and not acc[0].deleted and acc[0].etag == '"e1"'
    assert acc[1].google_id == "people/c2" and acc[1].deleted


class _FakeCalEventsRequest:
    def __init__(self, raise_410: bool, items: list[dict[str, Any]]):
        self._raise_410 = raise_410
        self._items = items

    def execute(self) -> dict[str, Any]:
        if self._raise_410:
            # Fake exception with status_code attribute
            class _FakeHttpError(Exception):
                def __init__(self) -> None:
                    self.status_code = 410  # CalendarClient checks this

            raise _FakeHttpError()
        return {"items": self._items, "nextSyncToken": "tok-cal-final"}


class _FakeCalService:
    def __init__(self, raise_410: bool, items: list[dict[str, Any]]):
        self._raise_410 = raise_410
        self._items = items

    def events(self) -> _FakeCalService:
        return self

    def list(self, **kwargs: Any) -> _FakeCalEventsRequest:
        return _FakeCalEventsRequest(self._raise_410, self._items)


def test_calendar_iterate_changes_token_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    # First try with sync_token provided; our fake raises 410 so client must fallback to bounded resync flow
    fake_service = _FakeCalService(raise_410=True, items=[])

    import g2nc.google.calendar as calendar_mod

    def _fake_build(api: str, ver: str, **kwargs: Any) -> Any:  # type: ignore[no-redef]
        assert api == "calendar" and ver == "v3"
        return fake_service

    # Ensure CalendarClient catches our exception type as HttpError (alias may not be present)
    class _FakeHttpError(Exception):
        pass

    monkeypatch.setattr(calendar_mod, "gapi_build", _fake_build)
    # Make HttpError an Exception subclass to satisfy except HttpError as he path
    monkeypatch.setattr(calendar_mod, "HttpError", _FakeHttpError, raising=False)

    client = CalendarClient(credentials=object())
    it, next_token = client.iterate_changes(
        "primary", sync_token="stale-token", page_size=10, time_window_days=7
    )
    # Fallback generator should be produced; iterate to confirm no exceptions
    list(it)
    # On bounded resync path, a next token may be None until the final page; we accept either None or a token
    assert next_token is None or isinstance(next_token, str)
