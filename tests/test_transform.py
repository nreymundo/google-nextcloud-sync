from __future__ import annotations

from g2nc.models import CalendarEvent
from g2nc.transform import event_payload_hash, event_uid, render_ics


def _timed_event() -> CalendarEvent:
    return CalendarEvent(
        google_event_id="evt-1",
        deleted=False,
        title="Team, Sync",
        description="line1\nline2",
        location="Room;A",
        start_raw="2026-01-01T10:00:00Z",
        end_raw="2026-01-01T11:00:00Z",
        all_day=False,
        recurrence=("RRULE:FREQ=DAILY;COUNT=2",),
    )


def test_event_uid_is_stable() -> None:
    assert event_uid("primary", "evt-1") == event_uid("primary", "evt-1")
    assert event_uid("primary", "evt-1") != event_uid("primary", "evt-2")


def test_event_payload_hash_changes_when_event_changes() -> None:
    original = _timed_event()
    changed = CalendarEvent(
        google_event_id=original.google_event_id,
        deleted=original.deleted,
        title="Updated",
        description=original.description,
        location=original.location,
        start_raw=original.start_raw,
        end_raw=original.end_raw,
        all_day=original.all_day,
        recurrence=original.recurrence,
    )

    assert event_payload_hash(original) != event_payload_hash(changed)


def test_render_ics_for_timed_event() -> None:
    ics = render_ics("uid-1", _timed_event())

    assert "UID:uid-1" in ics
    assert r"SUMMARY:Team\, Sync" in ics
    assert "DESCRIPTION:line1\\nline2" in ics
    assert r"LOCATION:Room\;A" in ics
    assert "DTSTART:20260101T100000Z" in ics
    assert "DTEND:20260101T110000Z" in ics
    assert "RRULE:FREQ=DAILY;COUNT=2" in ics


def test_render_ics_for_all_day_event() -> None:
    event = CalendarEvent(
        google_event_id="evt-2",
        deleted=False,
        title="Holiday",
        description=None,
        location=None,
        start_raw="2026-01-02",
        end_raw="2026-01-03",
        all_day=True,
        recurrence=(),
    )

    ics = render_ics("uid-2", event)

    assert "DTSTART;VALUE=DATE:20260102" in ics
    assert "DTEND;VALUE=DATE:20260103" in ics
