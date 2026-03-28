from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from requests import Response

from g2nc.models import CalendarEvent, NextcloudConfig
from g2nc.nextcloud.client import NextcloudCalendarClient


def _event() -> CalendarEvent:
    return CalendarEvent(
        google_event_id="evt-1",
        deleted=False,
        title="Meeting",
        description="desc",
        location="room",
        start_raw="2026-01-01T10:00:00Z",
        end_raw="2026-01-01T11:00:00Z",
        all_day=False,
        recurrence=(),
    )


def _response(status: int, text: str = "", headers: dict[str, str] | None = None) -> Response:
    response = Response()
    response.status_code = status
    response._content = text.encode("utf-8")
    if headers:
        response.headers.update(headers)
    return response


@dataclass
class _SessionStub:
    report_responses: list[Response] = field(default_factory=list)
    put_responses: list[Response] = field(default_factory=list)
    delete_responses: list[Response] = field(default_factory=list)
    requests: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    puts: list[tuple[str, dict[str, str], bytes]] = field(default_factory=list)
    deletes: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    auth: tuple[str, str] | None = None

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.requests.append((method, url, kwargs))
        return self.report_responses.pop(0)

    def put(self, url: str, **kwargs: Any) -> Response:
        headers = kwargs.get("headers", {})
        data = kwargs.get("data", b"")
        assert isinstance(headers, dict)
        assert isinstance(data, bytes)
        self.puts.append((url, headers, data))
        return self.put_responses.pop(0)

    def delete(self, url: str, **kwargs: Any) -> Response:
        headers = kwargs.get("headers", {})
        assert isinstance(headers, dict)
        self.deletes.append((url, headers))
        return self.delete_responses.pop(0)


def test_upsert_event_creates_when_uid_not_found() -> None:
    client = NextcloudCalendarClient(
        NextcloudConfig(username="alice", app_password="secret", timeout_seconds=30)
    )
    session = _SessionStub(
        report_responses=[_response(207, '<d:multistatus xmlns:d="DAV:" />')],
        put_responses=[_response(201, headers={"ETag": '"etag-1"'})],
    )
    client._session = session

    result = client.upsert_event(
        calendar_url="https://cloud.example/remote.php/dav/calendars/alice/work/",
        uid="uid-1",
        event=_event(),
        known_href=None,
        known_etag=None,
    )

    assert result.href == "uid-1.ics"
    assert result.etag == '"etag-1"'
    assert session.requests[0][0] == "REPORT"
    assert session.puts[0][0].endswith("uid-1.ics")


def test_upsert_event_retries_with_latest_etag_on_precondition_failure() -> None:
    client = NextcloudCalendarClient(
        NextcloudConfig(username="alice", app_password="secret", timeout_seconds=30)
    )
    report_xml = (
        '<d:multistatus xmlns:d="DAV:">'
        "<d:response>"
        "<d:href>/remote.php/dav/calendars/alice/work/uid-1.ics</d:href>"
        '<d:propstat><d:prop><d:getetag>"fresh"</d:getetag></d:prop></d:propstat>'
        "</d:response>"
        "</d:multistatus>"
    )
    session = _SessionStub(
        report_responses=[_response(207, report_xml), _response(207, report_xml)],
        put_responses=[_response(412), _response(204, headers={"ETag": '"fresh"'})],
    )
    client._session = session

    result = client.upsert_event(
        calendar_url="https://cloud.example/remote.php/dav/calendars/alice/work/",
        uid="uid-1",
        event=_event(),
        known_href="uid-1.ics",
        known_etag='"stale"',
    )

    assert result.href == "uid-1.ics"
    assert session.puts[0][1]["If-Match"] == '"fresh"'
    assert session.puts[1][1]["If-Match"] == '"fresh"'


def test_delete_event_treats_404_as_success() -> None:
    client = NextcloudCalendarClient(
        NextcloudConfig(username="alice", app_password="secret", timeout_seconds=30)
    )
    session = _SessionStub(delete_responses=[_response(404)])
    client._session = session

    client.delete_event(
        calendar_url="https://cloud.example/remote.php/dav/calendars/alice/work/",
        href="uid-1.ics",
        etag='"etag-1"',
    )

    assert session.deletes[0][1]["If-Match"] == '"etag-1"'
