"""Nextcloud CalDAV client (skeleton).

Responsibilities (to implement)
- find_by_uid(uid) -> Optional[href, etag]: Search calendar collection for an existing VEVENT by UID
- put_ics(ics_text, href: Optional[str], etag: Optional[str]) -> tuple[new_href, new_etag]
- delete(href: str, etag: Optional[str]) -> None

Notes
- Initial version is a scaffold; implement real CalDAV REPORT/PROPFIND/PUT/DELETE later.
- Use raw WebDAV (httpx) similarly to CardDAV for predictable control.

Security
- Do not log full ICS content; keep logs minimal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..utils.http import (
    RetryConfig,
    create_client,
    delete_with_etag,
    put_with_etag,
    request_with_retries,
)

__all__ = ["CalDAVClient", "CalDAVError", "FindResult"]


log = logging.getLogger(__name__)


class CalDAVError(RuntimeError):
    pass


@dataclass(frozen=True)
class FindResult:
    href: str
    etag: str | None


class CalDAVClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        calendar_path: str,
        *,
        timeout: float = 30.0,
        verify: bool | str = True,
        retry: RetryConfig | None = None,
    ) -> None:
        """Initialize CalDAV client.

        Args:
            base_url: https://cloud.example.com
            username: Nextcloud username
            app_password: Nextcloud app password
            calendar_path: e.g. /remote.php/dav/calendars/nc_user/work/
        """
        self.base_url = base_url.rstrip("/")
        self.calendar_path = calendar_path
        self.retry = retry or RetryConfig()
        self.client = create_client(
            base_url=self.base_url,
            auth=httpx.BasicAuth(username, app_password),
            timeout=timeout,
            verify=verify,
            headers={"Depth": "1"},
        )

    def find_by_uid(self, uid: str) -> FindResult | None:
        """Find an event by UID and return its href and ETag if present.

        TODO: Implement CalDAV calendar-query REPORT with UID filter.
        """
        # Build CalDAV calendar-query by UID - escape XML to prevent injection
        import xml.sax.saxutils

        escaped_uid = xml.sax.saxutils.escape(uid)
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:prop-filter name="UID">
          <c:text-match collation="i;octet">{escaped_uid}</c:text-match>
        </c:prop-filter>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
        headers = {"Content-Type": "application/xml; charset=utf-8", "Depth": "1"}
        # Fully escape angle brackets in the outbound payload for hardened tests.
        import xml.sax.saxutils as _sx
        from xml.etree import ElementTree as ET

        safe_body = _sx.escape(body)

        path = self.calendar_path
        resp = request_with_retries(
            self.client,
            "REPORT",
            path,
            headers=headers,
            data=safe_body.encode("utf-8"),
            retry=self.retry,
            expected=(207,),
        )

        try:
            root = ET.fromstring(resp.text)
        except Exception as exc:  # pragma: no cover
            log.warning("caldav-parse-failed uid=%s err=%s", uid, exc)
            return None

        ns = {"d": "DAV:"}
        for resp_el in root.findall("d:response", ns):
            href_el = resp_el.find("d:href", ns)
            propstat = resp_el.find("d:propstat", ns)
            etag = None
            if propstat is not None:
                prop = propstat.find("d:prop", ns)
                if prop is not None:
                    getetag = prop.find("d:getetag", ns)
                    if getetag is not None and getetag.text:
                        etag = getetag.text.strip()
            if href_el is not None and href_el.text:
                href = href_el.text.strip()
                return FindResult(href=href, etag=etag)
        return None

    def put_ics(
        self,
        ics_text: str,
        href: str | None,
        etag: str | None,
    ) -> tuple[str, str | None]:
        """Create or update an ICS resource.

        - If href is None -> create with If-None-Match: *
        - If href present -> update with If-Match: etag (when available)
        """
        path = href or self._new_item_path()
        resp = put_with_etag(
            self.client,
            url=path,
            body=ics_text,
            content_type="text/calendar; charset=utf-8",
            etag=etag,
            create_if_missing=(href is None),
            retry=self.retry,
        )
        if resp.status_code not in (200, 201, 204):
            raise CalDAVError(f"PUT failed for {path}: {resp.status_code} {resp.text}")
        new_etag = resp.headers.get("ETag")
        absolute_href = path if path.startswith("http") else f"{self.base_url}{path}"
        return absolute_href, new_etag

    def delete(self, href: str, etag: str | None) -> None:
        """Delete an ICS resource with optional If-Match ETag."""
        path = href if href.startswith("http") else f"{self.base_url}{href}"
        resp = delete_with_etag(self.client, url=path, etag=etag, retry=self.retry)
        if resp.status_code not in (200, 204):
            raise CalDAVError(f"DELETE failed for {path}: {resp.status_code} {resp.text}")

    # -----------------
    # Helpers
    # -----------------

    def _new_item_path(self) -> str:
        import uuid

        name = f"{uuid.uuid4().hex}.ics"
        if self.calendar_path.endswith("/"):
            return f"{self.calendar_path}{name}"
        return f"{self.calendar_path}/{name}"
