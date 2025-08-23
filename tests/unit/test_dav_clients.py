from types import SimpleNamespace

import pytest

from g2nc.nextcloud.caldav import CalDAVClient
from g2nc.nextcloud.caldav import FindResult as CalFind
from g2nc.nextcloud.carddav import CardDAVClient
from g2nc.nextcloud.carddav import FindResult as CardFind


@pytest.fixture
def carddav_client() -> CardDAVClient:
    return CardDAVClient(
        base_url="https://cloud.example.com",
        username="user",
        app_password="pass",
        addressbook_path="/remote.php/dav/addressbooks/users/user/Contacts/",
        timeout=5.0,
        verify=True,
    )


@pytest.fixture
def caldav_client() -> CalDAVClient:
    return CalDAVClient(
        base_url="https://cloud.example.com",
        username="user",
        app_password="pass",
        calendar_path="/remote.php/dav/calendars/user/Personal/",
        timeout=5.0,
        verify=True,
    )


def test_carddav_find_by_uid_parses_multistatus(monkeypatch, carddav_client: CardDAVClient) -> None:
    # Minimal WebDAV 207 response
    xml = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:response>
    <d:href>/remote.php/dav/addressbooks/users/user/Contacts/abcd.vcf</d:href>
    <d:propstat>
      <d:prop>
        <d:getetag>"12345"</d:getetag>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""

    import g2nc.nextcloud.carddav as carddav_mod

    def _fake_request_with_retries(client, method, url, **kwargs):  # type: ignore[no-redef]
        return SimpleNamespace(text=xml, status_code=207, headers={})

    monkeypatch.setattr(carddav_mod, "request_with_retries", _fake_request_with_retries)

    res = carddav_client.find_by_uid("people/c1")
    assert isinstance(res, CardFind)
    assert res is not None
    assert res.href.endswith("/abcd.vcf")
    assert res.etag == '"12345"'


def test_caldav_find_by_uid_parses_multistatus(monkeypatch, caldav_client: CalDAVClient) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/remote.php/dav/calendars/user/Personal/abcd.ics</d:href>
    <d:propstat>
      <d:prop>
        <d:getetag>"W/6789"</d:getetag>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""

    import g2nc.nextcloud.caldav as caldav_mod

    def _fake_request_with_retries(client, method, url, **kwargs):  # type: ignore[no-redef]
        return SimpleNamespace(text=xml, status_code=207, headers={})

    monkeypatch.setattr(caldav_mod, "request_with_retries", _fake_request_with_retries)

    res = caldav_client.find_by_uid("event-1")
    assert isinstance(res, CalFind)
    assert res is not None
    assert res.href.endswith("/abcd.ics")
    assert res.etag == '"W/6789"'


def test_carddav_put_and_delete_paths(monkeypatch, carddav_client: CardDAVClient) -> None:
    import g2nc.nextcloud.carddav as carddav_mod

    # For create (href=None), server should accept and return 201 with ETag
    def _fake_put_with_etag(client, url, body, *, content_type, etag, create_if_missing, retry):  # type: ignore[no-redef]
        assert content_type.startswith("text/vcard")
        # url should be a relative path created by _new_item_path
        assert url.endswith(".vcf")
        return SimpleNamespace(status_code=201, headers={"ETag": '"abc"'})

    def _fake_delete_with_etag(client, url, *, etag, retry):  # type: ignore[no-redef]
        assert url.startswith("https://cloud.example.com/")
        return SimpleNamespace(status_code=204, headers={})

    monkeypatch.setattr(carddav_mod, "put_with_etag", _fake_put_with_etag)
    monkeypatch.setattr(carddav_mod, "delete_with_etag", _fake_delete_with_etag)

    vcard_text = "BEGIN:VCARD\nVERSION:4.0\nUID:people/c1\nFN:Alice\nEND:VCARD\n"
    href, etag = carddav_client.put_vcard(vcard_text, href=None, etag=None)
    # ensure absolute href normalization with base_url prefix
    assert href.startswith("https://cloud.example.com")
    assert etag == '"abc"'

    # delete existing href
    carddav_client.delete(href, etag=etag)


def test_caldav_put_and_delete_paths(monkeypatch, caldav_client: CalDAVClient) -> None:
    import g2nc.nextcloud.caldav as caldav_mod

    def _fake_put_with_etag(client, url, body, *, content_type, etag, create_if_missing, retry):  # type: ignore[no-redef]
        assert content_type.startswith("text/calendar")
        assert url.endswith(".ics")
        return SimpleNamespace(status_code=201, headers={"ETag": '"xyz"'})

    def _fake_delete_with_etag(client, url, *, etag, retry):  # type: ignore[no-redef]
        assert url.startswith("https://cloud.example.com/")
        return SimpleNamespace(status_code=204, headers={})

    monkeypatch.setattr(caldav_mod, "put_with_etag", _fake_put_with_etag)
    monkeypatch.setattr(caldav_mod, "delete_with_etag", _fake_delete_with_etag)

    ics_text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-1\nSUMMARY:Meet\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    href, etag = caldav_client.put_ics(ics_text, href=None, etag=None)
    assert href.startswith("https://cloud.example.com")
    assert etag == '"xyz"'

    caldav_client.delete(href, etag=etag)
