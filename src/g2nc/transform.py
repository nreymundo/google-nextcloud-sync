from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from g2nc.models import CalendarEvent


def event_uid(google_calendar_id: str, google_event_id: str) -> str:
    seed = f"{google_calendar_id}:{google_event_id}".encode()
    digest = hashlib.sha1(seed).hexdigest()
    return f"g2nc-{digest}@sync.local"


def event_payload_hash(event: CalendarEvent) -> str:
    payload = {
        "google_event_id": event.google_event_id,
        "deleted": event.deleted,
        "title": event.title,
        "description": event.description,
        "location": event.location,
        "start_raw": event.start_raw,
        "end_raw": event.end_raw,
        "all_day": event.all_day,
        "recurrence": list(event.recurrence),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest


def _escape_ics_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace(";", "\\;")
    escaped = escaped.replace(",", "\\,")
    escaped = escaped.replace("\n", "\\n")
    return escaped


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_date(value: str) -> str:
    return value.replace("-", "")


def render_ics(uid: str, event: CalendarEvent) -> str:
    lines: list[str] = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//g2nc//EN", "BEGIN:VEVENT"]
    now_utc = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    lines.append(f"DTSTAMP:{now_utc}")
    lines.append(f"UID:{uid}")

    title = event.title if event.title else "(untitled)"
    lines.append(f"SUMMARY:{_escape_ics_text(title)}")

    if event.description:
        lines.append(f"DESCRIPTION:{_escape_ics_text(event.description)}")
    if event.location:
        lines.append(f"LOCATION:{_escape_ics_text(event.location)}")

    if event.all_day:
        lines.append(f"DTSTART;VALUE=DATE:{_format_date(event.start_raw)}")
        lines.append(f"DTEND;VALUE=DATE:{_format_date(event.end_raw)}")
    else:
        start_value = _parse_datetime(event.start_raw).strftime("%Y%m%dT%H%M%SZ")
        end_value = _parse_datetime(event.end_raw).strftime("%Y%m%dT%H%M%SZ")
        lines.append(f"DTSTART:{start_value}")
        lines.append(f"DTEND:{end_value}")

    for recurrence in event.recurrence:
        if recurrence.startswith("RRULE:"):
            lines.append(recurrence)

    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(lines)
