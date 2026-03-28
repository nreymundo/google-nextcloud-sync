from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from g2nc.google.client import GoogleAuthError, GoogleCalendarClient
from g2nc.models import GoogleAuthConfig
from g2nc.ports import SyncTokenInvalidatedError


def _auth(tmp_path: Path) -> GoogleAuthConfig:
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "token",
                "refresh_token": "refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client",
                "client_secret": "secret",
                "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
            }
        ),
        encoding="utf-8",
    )
    return GoogleAuthConfig(
        credentials_file=None,
        credentials_json=None,
        token_file=token_file,
        scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    )


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        self.resp = SimpleNamespace(status=status)


class _FakeRequest:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self._response = response

    def execute(self) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeEventsApi:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> _FakeRequest:
        self.calls.append(kwargs)
        return _FakeRequest(self._responses.pop(0))


class _FakeService:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self._events_api = _FakeEventsApi(responses)

    def events(self) -> _FakeEventsApi:
        return self._events_api


def test_fetch_event_changes_handles_pages_and_deleted_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = GoogleCalendarClient(_auth(tmp_path))
    service = _FakeService(
        [
            {
                "items": [
                    {
                        "id": "evt-1",
                        "summary": "Meeting",
                        "start": {"dateTime": "2026-01-01T10:00:00Z"},
                        "end": {"dateTime": "2026-01-01T11:00:00Z"},
                    }
                ],
                "nextPageToken": "page-2",
            },
            {
                "items": [{"id": "evt-2", "status": "cancelled"}],
                "nextSyncToken": "sync-2",
            },
        ]
    )
    monkeypatch.setattr(client, "_build_service", lambda: service)

    changes = client.fetch_event_changes("primary", "sync-1")

    assert changes.next_sync_token == "sync-2"
    assert [event.google_event_id for event in changes.events] == ["evt-1", "evt-2"]
    assert changes.events[1].deleted is True
    assert service.events().calls[0]["syncToken"] == "sync-1"
    assert service.events().calls[1]["pageToken"] == "page-2"


def test_fetch_event_changes_raises_on_410(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = GoogleCalendarClient(_auth(tmp_path))
    service = _FakeService([_HttpError(410)])
    monkeypatch.setattr(client, "_build_service", lambda: service)
    monkeypatch.setattr("g2nc.google.client.HttpError", _HttpError)

    with pytest.raises(SyncTokenInvalidatedError):
        client.fetch_event_changes("primary", "sync-1")


def test_build_service_refreshes_expired_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _auth(tmp_path)
    client = GoogleCalendarClient(auth)
    refreshed: dict[str, bool] = {"called": False}

    class _FakeCredentials:
        valid = False
        expired = True
        refresh_token = "refresh"

        @classmethod
        def from_authorized_user_info(
            cls, info: dict[str, Any], scopes: tuple[str, ...]
        ) -> _FakeCredentials:
            assert info["refresh_token"] == "refresh"
            assert scopes == auth.scopes
            return cls()

        def refresh(self, request: object) -> None:
            del request
            refreshed["called"] = True
            self.valid = True

        def to_json(self) -> str:
            return json.dumps({"token": "new-token"})

    monkeypatch.setattr("g2nc.google.client.Credentials", _FakeCredentials)
    monkeypatch.setattr("g2nc.google.client.Request", lambda: object())
    monkeypatch.setattr(
        "g2nc.google.client.build",
        lambda api, version, credentials, cache_discovery: (
            api,
            version,
            credentials,
            cache_discovery,
        ),
    )

    service = client._build_service()

    assert refreshed["called"] is True
    assert service[0] == "calendar"
    assert json.loads(auth.token_file.read_text(encoding="utf-8"))["token"] == "new-token"


def test_build_service_requires_bootstrapped_token(tmp_path: Path) -> None:
    auth = GoogleAuthConfig(
        credentials_file=None,
        credentials_json=None,
        token_file=tmp_path / "missing.json",
        scopes=("https://www.googleapis.com/auth/calendar.readonly",),
    )

    with pytest.raises(GoogleAuthError, match="Run auth bootstrap first"):
        GoogleCalendarClient(auth)._build_service()
