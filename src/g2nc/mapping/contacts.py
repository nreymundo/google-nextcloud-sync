"""Google People API → vCard 3.0/4.0 mapping.

Key rules
- UID = Google resourceName (e.g., "people/c12345") to ensure idempotency.
- Prefer vCard 4.0 unless config requires 3.0.
- Do not log raw PII; callers should redact when logging.

Public API
- person_to_vcard(person: Mapping[str, Any], *, version: str = "4.0", categories_from_groups: bool = True, include_photo_uri: bool = True) -> str
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import vobject

__all__ = ["person_to_vcard"]


def _first(seq: Iterable[Mapping[str, Any]] | None) -> Mapping[str, Any] | None:
    if not seq:
        return None
    for x in seq:
        return x
    return None


def _name_fields(person: Mapping[str, Any]) -> tuple[str, str, str, str, str, str, str]:
    """Return (uid, fn, family, given, additional, prefix, suffix)."""
    uid = str(person.get("resourceName") or "")
    names = person.get("names") or []
    primary = _first(names)
    if not primary:
        return uid, uid or "Unknown", "", "", "", "", ""
    fn = str(primary.get("displayName") or "").strip() or uid
    family = str(primary.get("familyName") or "")
    given = str(primary.get("givenName") or "")
    additional = str(primary.get("middleName") or "")
    prefix = str(primary.get("honorificPrefix") or "")
    suffix = str(primary.get("honorificSuffix") or "")
    return uid, fn, family, given, additional, prefix, suffix


def _emails(person: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for e in person.get("emailAddresses") or []:
        val = e.get("value")
        if not val:
            continue
        typ = (e.get("type") or e.get("formattedType") or "").upper() or None
        out.append((str(val), typ))
    return out


def _phones(person: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for p in person.get("phoneNumbers") or []:
        val = p.get("value")
        if not val:
            continue
        typ = (p.get("type") or p.get("formattedType") or "").upper() or None
        out.append((str(val), typ))
    return out


def _urls(person: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for u in person.get("urls") or []:
        val = u.get("value") or u.get("url")
        if not val:
            continue
        out.append(str(val))
    return out


def _org(person: Mapping[str, Any]) -> tuple[str | None, str | None]:
    org = _first(person.get("organizations"))
    if not org:
        return None, None
    return (
        str(org.get("name")) if org.get("name") else None,
        str(org.get("title")) if org.get("title") else None,
    )


def _note(person: Mapping[str, Any]) -> str | None:
    bio = _first(person.get("biographies"))
    if not bio:
        return None
    val = bio.get("value")
    return str(val) if val else None


def _birthday(person: Mapping[str, Any]) -> str | None:
    b = _first(person.get("birthdays"))
    if not b:
        return None
    # The People API birthday may be split into date parts
    if b.get("date"):
        d = b["date"]
        y = d.get("year")
        m = d.get("month")
        day = d.get("day")
        if m and day:
            # vCard BDAY prefers YYYY-MM-DD; allow missing year with -- if absent
            if y:
                return f"{int(y):04d}-{int(m):02d}-{int(day):02d}"
            return f"--{int(m):02d}-{int(day):02d}"
    # or freeform text
    if b.get("text"):
        return str(b["text"])
    return None


def _addresses(person: Mapping[str, Any]) -> list[Any]:
    out: list[Any] = []
    for a in person.get("addresses") or []:
        adr = vobject.vcard.Address(
            street=str(a.get("streetAddress") or ""),
            city=str(a.get("city") or ""),
            region=str(a.get("region") or ""),
            code=str(a.get("postalCode") or ""),
            country=str(a.get("country") or ""),
        )
        out.append(adr)
    return out


def _nicknames(person: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for n in person.get("nicknames") or []:
        val = n.get("value")
        if val:
            out.append(str(val))
    return out


def _categories_from_groups(person: Mapping[str, Any]) -> list[str]:
    cats: list[str] = []
    for m in person.get("memberships") or []:
        g = (m.get("contactGroupMembership") or {}).get("contactGroupResourceName")
        if g:
            cats.append(str(g))
    return cats


def _photo_uri(person: Mapping[str, Any]) -> str | None:
    p = _first(person.get("photos"))
    if not p:
        return None
    url = p.get("url")
    if url:
        return str(url)
    return None


def person_to_vcard(
    person: Mapping[str, Any],
    *,
    version: str = "4.0",
    categories_from_groups: bool = True,
    include_photo_uri: bool = True,
) -> str:
    """Map People API person -> vCard text."""
    uid, fn, family, given, additional, prefix, suffix = _name_fields(person)

    v = vobject.vCard()
    v.add("version").value = version
    v.add("uid").value = uid
    v.add("fn").value = fn

    # N: Family;Given;Additional;Prefix;Suffix
    n = v.add("n")
    family_str = family or ""
    given_str = given or ""
    additional_list = [additional] if additional else []
    prefix_list = [prefix] if prefix else []
    suffix_list = [suffix] if suffix else []
    n.value = vobject.vcard.Name(
        family=family_str,
        given=given_str,
        additional=additional_list,
        prefix=prefix_list,
        suffix=suffix_list,
    )

    # Emails
    for email, typ in _emails(person):
        prop = v.add("email")
        prop.value = email
        if typ:
            prop.type_param = typ  # vobject encodes TYPE

    # Phones
    for phone, typ in _phones(person):
        prop = v.add("tel")
        prop.value = phone
        if typ:
            prop.type_param = typ

    # URLs
    for url in _urls(person):
        prop = v.add("url")
        prop.value = url

    # ORG and TITLE
    org, title = _org(person)
    if org:
        v.add("org").value = [org]
    if title:
        v.add("title").value = title

    # Nicknames
    for nick in _nicknames(person):
        v.add("nickname").value = nick

    # Notes
    note = _note(person)
    if note:
        v.add("note").value = note

    # Birthday
    bday = _birthday(person)
    if bday:
        v.add("bday").value = bday

    # Addresses
    for adr in _addresses(person):
        prop = v.add("adr")
        prop.value = adr

    # Categories from groups (resource names), optional
    if categories_from_groups:
        cats = _categories_from_groups(person)
        if cats:
            v.add("categories").value = cats

    # Photo as URI (Nextcloud may fetch); binary embedding can be added later
    if include_photo_uri:
        uri = _photo_uri(person)
        if uri:
            p = v.add("photo")
            p.value = uri
            p.params["VALUE"] = ["uri"]

    return str(v.serialize())
