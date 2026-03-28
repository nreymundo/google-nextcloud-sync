from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Final
from xml.etree import ElementTree

from requests import Response, Session

from g2nc.models import CalendarEvent, NextcloudConfig, UpsertResult
from g2nc.transform import render_ics

DAV_NAMESPACE: Final[str] = "DAV:"


class NextcloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class _RemoteEvent:
    href: str
    etag: str | None


class NextcloudCalendarClient:
    def __init__(self, config: NextcloudConfig) -> None:
        self._timeout = config.timeout_seconds
        self._session = Session()
        self._session.auth = (config.username, config.app_password)

    def upsert_event(
        self,
        calendar_url: str,
        uid: str,
        event: CalendarEvent,
        known_href: str | None,
        known_etag: str | None,
    ) -> UpsertResult:
        ics = render_ics(uid, event)
        existing = self._find_event_by_uid(calendar_url, uid)

        href = known_href
        etag = known_etag
        if existing is not None:
            href = existing.href
            etag = existing.etag

        if href is None:
            href = self._href_from_uid(uid)

        target_url = urllib.parse.urljoin(_ensure_trailing_slash(calendar_url), href)
        headers: dict[str, str] = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag is not None:
            headers["If-Match"] = etag

        response = self._session.put(
            target_url,
            data=ics.encode("utf-8"),
            headers=headers,
            timeout=self._timeout,
        )
        if response.status_code == 412 and etag is not None:
            latest = self._find_event_by_uid(calendar_url, uid)
            if latest is not None:
                headers["If-Match"] = latest.etag if latest.etag is not None else "*"
                response = self._session.put(
                    target_url,
                    data=ics.encode("utf-8"),
                    headers=headers,
                    timeout=self._timeout,
                )

        self._assert_success(response, {200, 201, 204}, "PUT", target_url)
        etag_header = response.headers.get("ETag")
        return UpsertResult(href=href, etag=etag_header)

    def delete_event(self, calendar_url: str, href: str, etag: str | None) -> None:
        target_url = urllib.parse.urljoin(_ensure_trailing_slash(calendar_url), href)
        headers: dict[str, str] = {}
        if etag is not None:
            headers["If-Match"] = etag

        response = self._session.delete(target_url, headers=headers, timeout=self._timeout)
        if response.status_code in {404, 410}:
            return
        self._assert_success(response, {200, 204}, "DELETE", target_url)

    def _find_event_by_uid(self, calendar_url: str, uid: str) -> _RemoteEvent | None:
        url = _ensure_trailing_slash(calendar_url)
        report_body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><d:getetag/><d:href/></d:prop>"
            "<c:filter>"
            '<c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT">'
            '<c:prop-filter name="UID">'
            f'<c:text-match collation="i;octet" match-type="equals">{uid}</c:text-match>'
            "</c:prop-filter>"
            "</c:comp-filter>"
            "</c:comp-filter>"
            "</c:filter>"
            "</c:calendar-query>"
        )
        response = self._session.request(
            method="REPORT",
            url=url,
            data=report_body.encode("utf-8"),
            headers={
                "Depth": "1",
                "Content-Type": "application/xml; charset=utf-8",
            },
            timeout=self._timeout,
        )
        self._assert_success(response, {207}, "REPORT", url)

        root = ElementTree.fromstring(response.content)
        for node in root.findall(f"{{{DAV_NAMESPACE}}}response"):
            href_node = node.find(f"{{{DAV_NAMESPACE}}}href")
            if href_node is None or href_node.text is None:
                continue
            href_value = href_node.text
            propstat = node.find(f"{{{DAV_NAMESPACE}}}propstat")
            if propstat is None:
                continue
            prop = propstat.find(f"{{{DAV_NAMESPACE}}}prop")
            if prop is None:
                continue
            etag_node = prop.find(f"{{{DAV_NAMESPACE}}}getetag")
            etag = etag_node.text if etag_node is not None else None
            parsed = urllib.parse.urlparse(href_value)
            href_path = parsed.path
            calendar_path = urllib.parse.urlparse(url).path
            normalized = href_path.replace(calendar_path, "", 1).lstrip("/")
            if normalized:
                return _RemoteEvent(href=normalized, etag=etag)
        return None

    def _href_from_uid(self, uid: str) -> str:
        return f"{uid}.ics"

    def _assert_success(
        self,
        response: Response,
        status_codes: set[int],
        method: str,
        url: str,
    ) -> None:
        if response.status_code not in status_codes:
            raise NextcloudError(
                f"{method} {url} failed with status {response.status_code}: {response.text[:500]}"
            )


def _ensure_trailing_slash(value: str) -> str:
    if value.endswith("/"):
        return value
    return f"{value}/"
