"""Timezone helpers for Google Calendar → ICS mapping.

Responsibilities
- Normalize/resolve TZIDs using stdlib zoneinfo (with tzdata fallback).
- Parse Google Calendar event date/datetime payloads into Python types.
- Provide convenient transforms for ICS libraries (icalendar), returning
  date for all-day and timezone-aware datetime for timed events.

Google Calendar payloads (examples)
- All-day:
  {"date": "2025-08-23"}  # optional "timeZone" may be present but not used for all-day
- Timed:
  {"dateTime": "2025-08-23T14:00:00-04:00", "timeZone": "America/New_York"}
  {"dateTime": "2025-08-23T18:00:00", "timeZone": "UTC"}  # naive dt with explicit tzid

Public API
- get_zoneinfo(tzid: str | None) -> ZoneInfo | None
- ensure_tz(dt: datetime, tzid: str | None, default_tz: str = "UTC") -> datetime
- parse_google_datetime(payload: Mapping[str, Any], default_tz: str = "UTC") -> tuple[date|datetime, bool, str]
  returns (value_for_ics, is_all_day, tzid_used)
- to_ics_value(value: date | datetime, is_all_day: bool) -> date | datetime
  passthrough for libraries that expect the right type

Notes
- For all-day events, ICS expects a DATE (no time, no TZ). Consumers treat this as local all-day.
- For timed events, prefer timezone-aware datetimes. If Google payload supplies a naive datetime
  with an explicit timeZone, we localize to that zone.
- If a tzid is unknown, fallback to UTC.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser as dtparser

__all__ = [
    "ensure_tz",
    "get_zoneinfo",
    "parse_google_datetime",
    "to_ics_value",
]

DateOrDateTime = date | datetime


def get_zoneinfo(tzid: str | None) -> ZoneInfo | None:
    """Resolve a TZID to ZoneInfo, returning None if not found or not provided."""
    if not tzid:
        return None
    try:
        return ZoneInfo(tzid)
    except ZoneInfoNotFoundError:
        # Fallbacks for common aliases
        if tzid.upper() in {"UTC", "Z"}:
            return ZoneInfo("UTC")
        return None


def ensure_tz(dt: datetime, tzid: str | None, default_tz: str = "UTC") -> datetime:
    """Ensure a datetime is timezone-aware.

    - If dt already timezone-aware, return as-is.
    - If naive, try tzid; else default_tz; else UTC.
    """
    if dt.tzinfo is not None:
        return dt
    z = get_zoneinfo(tzid) or get_zoneinfo(default_tz) or ZoneInfo("UTC")
    return dt.replace(tzinfo=z)


def _parse_all_day(payload: Mapping[str, object]) -> tuple[date, bool, str]:
    # Google provides YYYY-MM-DD; if timeZone present, we still keep DATE for ICS.
    s = str(payload.get("date"))
    y, m, d = [int(x) for x in s.split("-")]
    tzid = str(payload.get("timeZone")) if payload.get("timeZone") else "UTC"
    return date(y, m, d), True, tzid


def _parse_timed(payload: Mapping[str, object], default_tz: str) -> tuple[datetime, bool, str]:
    raw = str(payload.get("dateTime"))
    tzid = str(payload.get("timeZone")) if payload.get("timeZone") else None
    dt = dtparser.isoparse(raw)
    dt = ensure_tz(dt, tzid, default_tz=default_tz)
    # Normalize to keep tz-aware with original or default zone (do not convert to UTC here)
    return dt, False, (tzid or default_tz or "UTC")


def parse_google_datetime(
    payload: Mapping[str, object], default_tz: str = "UTC"
) -> tuple[DateOrDateTime, bool, str]:
    """Parse Google Calendar 'start'/'end' payload to (value, is_all_day, tzid_used).

    - If payload contains "date" => return date object, is_all_day=True (tzid is informational).
    - If payload contains "dateTime" => return timezone-aware datetime, is_all_day=False (tzid used).
    """
    if "date" in payload and payload.get("date") is not None:
        return _parse_all_day(payload)
    if "dateTime" in payload and payload.get("dateTime") is not None:
        return _parse_timed(payload, default_tz=default_tz)
    raise ValueError("Google datetime payload must contain either 'date' or 'dateTime'.")


def to_ics_value(value: DateOrDateTime, is_all_day: bool) -> DateOrDateTime:
    """Return the appropriate Python type for ICS libraries (icalendar).

    - all-day → date
    - timed → timezone-aware datetime (unaltered)
    """
    if is_all_day:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        # Convert datetime to date safely (ignore time)
        if isinstance(value, datetime):
            return value.date()
        raise TypeError("Expected date or datetime for all-day value.")
    else:
        if isinstance(value, datetime):
            # Ensure tz-aware; if not, set UTC
            return value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
        raise TypeError("Expected datetime for timed ICS value.")
