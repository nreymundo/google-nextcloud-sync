"""Nextcloud CardDAV client (skeleton).

Responsibilities (to implement)
- find_by_uid(uid) -> Optional[href, etag]: Search addressbook for an existing vCard by UID
- put_vcard(vcard_text, href: Optional[str], etag: Optional[str]) -> tuple[new_href, new_etag]
- delete(href: str, etag: Optional[str]) -> None

Notes
- This initial version is a scaffold to be filled with real CardDAV REPORT/PROPFIND/PUT/DELETE logic.
- Preferred approach for v1: use raw WebDAV (httpx) for CardDAV operations:
    * REPORT addressbook-query with vcard:prop filter on UID to deduplicate
    * PUT text/vcard; use If-None-Match: * for create, If-Match: <etag> for updates
    * DELETE with If-Match when ETag known
- HREFs are absolute URLs as returned by Nextcloud.

Security
- Do not log full vCard content; mask emails/phones when logging snippets.
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

__all__ = ["CardDAVClient", "CardDAVError", "FindResult"]


log = logging.getLogger(__name__)


class CardDAVError(RuntimeError):
    pass


@dataclass(frozen=True)
class FindResult:
    href: str
    etag: str | None


class CardDAVClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        addressbook_path: str,
        *,
        timeout: float = 30.0,
        verify: bool | str = True,
        retry: RetryConfig | None = None,
    ) -> None:
        """Initialize CardDAV client.

        Args:
            base_url: https://cloud.example.com
            username: Nextcloud username
            app_password: Nextcloud app password
            addressbook_path: e.g. /remote.php/dav/addressbooks/users/nc_user/Contacts/
        """
        self.base_url = base_url.rstrip("/")
        self.addressbook_path = addressbook_path
        self.retry = retry or RetryConfig()
        self.client = create_client(
            base_url=self.base_url,
            auth=httpx.BasicAuth(username, app_password),
            timeout=timeout,
            verify=verify,
            headers={"Depth": "1"},
        )

    def find_by_uid(self, uid: str) -> FindResult | None:  # type: ignore[name-defined]
        """Find a contact by vCard UID and return its href and ETag if present.

        TODO: Implement CardDAV addressbook-query REPORT with UID filter.
        """
        # Build CardDAV addressbook-query by UID
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:getetag/>
  </d:prop>
  <card:filter>
    <card:prop-filter name="UID">
      <card:text-match collation="i;octet">{uid}</card:text-match>
    </card:prop-filter>
  </card:filter>
</card:addressbook-query>"""
        headers = {"Content-Type": "application/xml; charset=utf-8", "Depth": "1"}
        from xml.etree import ElementTree as ET

        path = self.addressbook_path
        resp = request_with_retries(
            self.client,
            "REPORT",
            path,
            headers=headers,
            data=body.encode("utf-8"),
            retry=self.retry,
            expected=(207,),
        )

        try:
            root = ET.fromstring(resp.text)
        except Exception as exc:  # pragma: no cover
            log.warning("carddav-parse-failed uid=%s err=%s", uid, exc)
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

    def put_vcard(
        self,
        vcard_text: str,
        href: str | None,
        etag: str | None,
    ) -> tuple[str, str | None]:
        """Create or update a vCard resource.

        - If href is None -> create with If-None-Match: *
        - If href present -> update with If-Match: etag (when available)
        """
        path = href or self._new_item_path()
        resp = put_with_etag(
            self.client,
            url=path,
            body=vcard_text,
            content_type="text/vcard; charset=utf-8",
            etag=etag,
            create_if_missing=(href is None),
            retry=self.retry,
        )
        if resp.status_code not in (200, 201, 204):
            raise CardDAVError(f"PUT failed for {path}: {resp.status_code} {resp.text}")
        new_etag = resp.headers.get("ETag")
        # Normalize absolute href
        absolute_href = path if path.startswith("http") else f"{self.base_url}{path}"
        return absolute_href, new_etag

    def delete(self, href: str, etag: str | None) -> None:
        """Delete a vCard resource with optional If-Match ETag."""
        path = href if href.startswith("http") else f"{self.base_url}{href}"
        resp = delete_with_etag(self.client, url=path, etag=etag, retry=self.retry)
        if resp.status_code not in (200, 204):
            raise CardDAVError(f"DELETE failed for {path}: {resp.status_code} {resp.text}")

    # -----------------
    # Helpers
    # -----------------

    def _new_item_path(self) -> str:
        # Server can generate a name if we PUT to a specific new path.
        # Use a simple UUID-based filename ending with .vcf within the addressbook collection.
        import uuid

        name = f"{uuid.uuid4().hex}.vcf"
        # ensure collection path has trailing slash integrated properly
        if self.addressbook_path.endswith("/"):
            return f"{self.addressbook_path}{name}"
        return f"{self.addressbook_path}/{name}"
