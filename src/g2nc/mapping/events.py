"""Google Calendar Event → ICS VEVENT mapping.

Rules (v1)
- UID = Google event.id (PRD §6/§7).
- Map SUMMARY, DESCRIPTION, LOCATION, DTSTART, DTEND.
- Time handling:
  - All-day → DATE values (no TZ). Use Google-provided 'date' fields as-is.
  - Timed → timezone-aware datetime using Google 'dateTime' + 'timeZone' (utils.timezones).
- Minimum viable recurrence: expose RRULE when present (best-effort v1).
  - Google may supply recurrence as ['RRULE:...'] lines.
  - EXDATE and others are deferred to a later pass if needed.

Public API
- event_to_ics(event: Mapping[str, Any], *, default_tz="UTC", include_valarm=False, display_alarm_minutes=15) -> str
  Returns a full VCALENDAR text containing a single VEVENT mapped from Google Event.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from icalendar import Calendar, Event, vCalAddress, vText  # type: ignore

from ..utils.timezones import parse_google_datetime, to_ics_value

__all__ = ["event_to_ics"]


def _add_attendees(vevent: Event, event: Mapping[str, Any]) -> None:
    for att in event.get("attendees") or []:
        email = att.get("email")
        if not email:
            continue
        cn = att.get("displayName") or email
        attendee = vCalAddress(f"MAILTO:{email}")
        attendee.params["cn"] = vText(str(cn))
        role = att.get("role") or "REQ-PARTICIPANT"
        attendee.params["role"] = vText(role)
        if att.get("optional"):
            attendee.params["role"] = vText("OPT-PARTICIPANT")
        vevent.add("attendee", attendee)


def _add_organizer(vevent: Event, event: Mapping[str, Any]) -> None:
    org = event.get("organizer") or {}
    email = org.get("email")
    if not email:
        return
    cn = org.get("displayName") or email
    organizer = vCalAddress(f"MAILTO:{email}")
    organizer.params["cn"] = vText(str(cn))
    vevent["organizer"] = organizer


def _add_recurrence_best_effort(vevent: Event, event: Mapping[str, Any]) -> None:
    rec = event.get("recurrence") or []
    # Google may provide ['RRULE:...', 'EXDATE:...'] lines. We only add RRULE here.
    for line in rec:
        if not isinstance(line, str):
            continue
        # Only RRULE in v1 (simple)
        if line.startswith("RRULE:"):
            # icalendar accepts dict form, but also parses raw RFC string values reasonably.
            # Store the naked RRULE value (without prefix); icalendar will serialize as RRULE.
            vevent.add("rrule", line[len("RRULE:") :].strip())
        # Future: handle EXDATE/RDATE if needed


def _add_valarm(vevent: Event, minutes_before: int) -> None:
    # Minimal DISPLAY alarm
    from icalendar import Alarm  # type: ignore

    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", "Reminder")
    # Trigger as negative duration (e.g. -PT15M)
    alarm.add("trigger", f"-PT{int(minutes_before)}M")
    vevent.add_component(alarm)


def event_to_ics(
    event: Mapping[str, Any],
    *,
    default_tz: str = "UTC",
    include_valarm: bool = False,
    display_alarm_minutes: int = 15,
) -> str:
    """Map Google Event -> VCALENDAR text with single VEVENT."""
    uid = event.get("id")
    if not uid:
        raise ValueError("Google event missing 'id' (required for UID).")

    cal = Calendar()
    cal.add("prodid", "-//g2nc//google-nextcloud-sync//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")

    ve = Event()
    ve.add("uid", uid)

    if event.get("summary"):
        ve.add("summary", event.get("summary"))

    if event.get("description"):
        ve.add("description", event.get("description"))

    if event.get("location"):
        ve.add("location", event.get("location"))

    # DTSTART / DTEND
    start_payload = event.get("start") or {}
    end_payload = event.get("end") or {}

    start_val, start_all_day, _tzid_used_s = parse_google_datetime(
        start_payload, default_tz=default_tz
    )
    end_val, end_all_day, _tzid_used_e = parse_google_datetime(end_payload, default_tz=default_tz)

    # All-day events should use DATE (not DATETIME)
    if start_all_day != end_all_day:
        # Defensive: if mismatched, coerce both to all-day if either is all-day
        start_all_day = end_all_day = True

    if start_all_day:
        ve.add("dtstart", to_ics_value(start_val, True))
        ve.add("dtend", to_ics_value(end_val, True))
    else:
        ve.add("dtstart", to_ics_value(start_val, False))
        ve.add("dtend", to_ics_value(end_val, False))

    # Status
    status = event.get("status")
    if status:
        ve.add("status", status.upper())

    # Visibility (transparency, privacy)
    visibility = event.get("visibility")
    if visibility:
        # Map to CLASS or X- attributes if necessary; in v1, store as X- field
        ve.add("class", "PUBLIC" if visibility == "public" else "PRIVATE")

    # Organizer / attendees
    _add_organizer(ve, event)
    _add_attendees(ve, event)

    # Recurrence (best-effort RRULE only)
    _add_recurrence_best_effort(ve, event)

    # VALARM (optional)
    if include_valarm and isinstance(display_alarm_minutes, int) and display_alarm_minutes > 0:
        _add_valarm(ve, int(display_alarm_minutes))

    cal.add_component(ve)
    return cal.to_ical().decode("utf-8")
