"""Self-check for NCO holiday booking, cancellation and the audit trail.

Covers the rules that make this feature what it is: you can only book your own
holiday, it needs two weeks' notice, cancelling removes the calendar event but
never the record, and the booking keeps who added it and when even after it's
cancelled.
"""
import asyncio
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import NcoHoliday
import routers.nco_holidays as nh
from core.db import get_or_create_user


def _day(offset: int) -> str:
    """A date `offset` days from today, as the browser would send it. Bookings
    are expressed relative to today so the notice-period rule keeps being
    exercised the same way whenever this runs."""
    return (nh._today() + timedelta(days=offset)).date().isoformat()


def _on(holiday: dict, field: str) -> str:
    """The date half of a serialised timestamp, to compare against `_day`."""
    return holiday[field][:10]


def _idinfo(name: str) -> dict:
    return {"sub": name, "email": f"{name}@x", "given_name": name, "family_name": "T"}


def test():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    # Stand in for Google Calendar — record the calls instead of making them.
    created: list[dict] = []
    deleted: list[str] = []
    nh.calendar_configured = lambda: True
    nh.create_holiday_event = lambda name, email, f, t, r: (
        created.append({"name": name, "from": f, "to": t}) or f"evt-{len(created)}"
    )
    nh.delete_holiday_event = lambda event_id: (deleted.append(event_id) or True)
    updated: list[dict] = []
    nh.update_holiday_event = lambda eid, name, email, f, t, r: (
        updated.append({"id": eid, "from": f, "to": t}) or bool(eid)
    )

    staff_emails = {"staff@x"}
    nh._is_staff = lambda idinfo: idinfo.get("email") in staff_emails

    alice, bob, staff = (_idinfo("alice"), _idinfo("bob"), _idinfo("staff"))
    for who in (alice, bob, staff):
        get_or_create_user(db, who)

    run = asyncio.run
    body = nh.HolidayBody(date_from=_day(30), date_to=_day(34), reason="Family")
    holiday = run(nh.create_holiday(body, db, alice))
    hid = holiday["id"]

    # Booked against the person who asked for it, on the calendar, and stamped
    # with the date it was added.
    assert holiday["booked_by_email"] == "alice@x"
    assert holiday["on_calendar"] is True
    assert holiday["cancelled"] is False
    assert holiday["created_at"] is not None
    assert len(created) == 1 and created[0]["name"] == "alice T"

    # Everyone on the page sees the whole squadron's upcoming bookings.
    assert hid in [h["id"] for h in run(nh.list_holidays(db, bob))["holidays"]]

    # Only the author or staff can remove one.
    for who in (bob,):
        try:
            run(nh.cancel_holiday(hid, db, who))
            raise AssertionError("cancelled someone else's holiday")
        except HTTPException as e:
            assert e.status_code == 403
    assert run(nh.list_holidays(db, bob))["holidays"][0]["can_cancel"] is False
    assert run(nh.list_holidays(db, staff))["holidays"][0]["can_cancel"] is True

    # Cancelling clears the calendar event but keeps the record intact.
    cancelled = run(nh.cancel_holiday(hid, db, alice))
    assert deleted == ["evt-1"]
    assert cancelled["cancelled"] is True
    assert cancelled["cancelled_by_name"] == "alice T"
    assert cancelled["on_calendar"] is False
    assert cancelled["booked_by_email"] == "alice@x"    # original booking survives
    assert cancelled["created_at"] == holiday["created_at"]
    assert db.query(NcoHoliday).filter(NcoHoliday.id == hid).first() is not None
    assert hid in [h["id"] for h in run(nh.list_holidays(db, alice))["holidays"]]
    # ...but the audit trail is staff-only: another NCO no longer sees a
    # cancelled booking that isn't theirs, while staff still do.
    assert hid not in [h["id"] for h in run(nh.list_holidays(db, bob))["holidays"]]
    assert hid in [h["id"] for h in run(nh.list_holidays(db, staff))["holidays"]]

    # ...and can't be cancelled twice.
    try:
        run(nh.cancel_holiday(hid, db, staff))
        raise AssertionError("double cancel allowed")
    except HTTPException as e:
        assert e.status_code == 409

    # Staff cancelling someone else's is recorded against the staff member.
    other = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(60), date_to=_day(60)), db, bob))
    staff_cancelled = run(nh.cancel_holiday(other["id"], db, staff))
    assert staff_cancelled["booked_by_email"] == "bob@x"
    assert staff_cancelled["cancelled_by_name"] == "staff T"

    # Bad ranges are rejected before anything reaches the calendar.
    calls_before = len(created)
    for bad, code in (
        (nh.HolidayBody(date_from=_day(40), date_to=_day(30)), 400),
        (nh.HolidayBody(date_from=_day(30), date_to=_day(400)), 400),
        (nh.HolidayBody(date_from="not-a-date", date_to=_day(30)), 400),
    ):
        try:
            run(nh.create_holiday(bad, db, alice))
            raise AssertionError("invalid range accepted")
        except HTTPException as e:
            assert e.status_code == code
    assert len(created) == calls_before

    # ── Notice period ────────────────────────────────────────────────────────
    # An NCO needs MIN_NOTICE_DAYS before the first day off. The boundary is
    # inclusive: exactly that many days out is fine, one day inside it is not.
    calls_before = len(created)
    for offset in (-1, 0, 1, nh.MIN_NOTICE_DAYS - 1):
        try:
            run(nh.create_holiday(
                nh.HolidayBody(date_from=_day(offset), date_to=_day(offset + 1)), db, alice))
            raise AssertionError(f"booking {offset} days out accepted")
        except HTTPException as e:
            assert e.status_code == 400
            assert "notice" in e.detail
    assert len(created) == calls_before    # nothing reached the calendar

    on_boundary = run(nh.create_holiday(nh.HolidayBody(
        date_from=_day(nh.MIN_NOTICE_DAYS), date_to=_day(nh.MIN_NOTICE_DAYS)), db, alice))
    assert on_boundary["on_calendar"] is True

    # Staff don't book here at all — this list is NCO absence, and they manage
    # it rather than appearing on it.
    calls_before = len(created)
    try:
        run(nh.create_holiday(nh.HolidayBody(date_from=_day(30), date_to=_day(31)), db, staff))
        raise AssertionError("staff booked a holiday")
    except HTTPException as e:
        assert e.status_code == 403
    assert len(created) == calls_before
    assert run(nh.list_holidays(db, staff))["can_book"] is False
    assert run(nh.list_holidays(db, alice))["can_book"] is True

    # The form reads the rule off the list response rather than hardcoding it.
    alice_view = run(nh.list_holidays(db, alice))
    assert alice_view["min_notice_days"] == nh.MIN_NOTICE_DAYS
    assert alice_view["earliest_booking_date"] == _day(nh.MIN_NOTICE_DAYS)
    staff_view = run(nh.list_holidays(db, staff))
    assert staff_view["min_notice_days"] == 0
    assert staff_view["earliest_booking_date"] is None

    # A booking made while Calendar was down saves anyway, flags itself, and
    # syncs on retry.
    nh.create_holiday_event = lambda *a, **k: None
    offline = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(90), date_to=_day(91)), db, alice))
    assert offline["on_calendar"] is False
    assert offline["cancelled"] is False
    nh.create_holiday_event = lambda *a, **k: "evt-recovered"
    assert run(nh.sync_holiday(offline["id"], db, alice))["on_calendar"] is True

    # A cancel that Google refuses still cancels the booking, and leaves the
    # event id behind so the retry can clear it rather than orphaning it.
    nh.create_holiday_event = lambda *a, **k: "evt-stuck"
    stuck = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(100), date_to=_day(101)), db, alice))
    nh.delete_holiday_event = lambda event_id: False
    still_there = run(nh.cancel_holiday(stuck["id"], db, alice))
    assert still_there["cancelled"] is True
    assert still_there["on_calendar"] is True    # flagged for retry
    nh.delete_holiday_event = lambda event_id: (deleted.append(event_id) or True)
    assert run(nh.sync_holiday(stuck["id"], db, alice))["on_calendar"] is False
    assert "evt-stuck" in deleted

    # A single-day holiday is a valid range.
    one_day = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(120), date_to=_day(120)), db, alice))
    assert one_day["date_from"] == one_day["date_to"]
    assert datetime.fromisoformat(one_day["date_from"]).hour == 0

    # ── Double booking and extending ─────────────────────────────────────────
    nh.create_holiday_event = lambda name, email, f, t, r: (
        created.append({"name": name, "from": f, "to": t}) or f"evt-{len(created)}"
    )
    base = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(200), date_to=_day(204), reason="Trip"), db, alice))
    base_id, base_event = base["id"], "evt-%d" % len(created)

    # Dates already booked off change nothing, so they're refused rather than
    # silently duplicated — the message names the booking that already covers it.
    rows_before, calls_before = db.query(NcoHoliday).count(), len(created)
    for same in (
        nh.HolidayBody(date_from=_day(200), date_to=_day(204)),   # exactly it
        nh.HolidayBody(date_from=_day(201), date_to=_day(203)),   # inside it
        nh.HolidayBody(date_from=_day(202), date_to=_day(202)),   # one day inside
    ):
        try:
            run(nh.create_holiday(same, db, alice))
            raise AssertionError("double booked the same dates")
        except HTTPException as e:
            assert e.status_code == 409
            assert "already booked" in e.detail
    assert db.query(NcoHoliday).count() == rows_before   # no rows, no calendar calls
    assert len(created) == calls_before

    # Booking over the end of it lengthens that booking instead of adding a
    # second row, and moves the same calendar event rather than making another.
    updated.clear()
    longer = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(203), date_to=_day(210), reason="Ignored"), db, alice))
    assert longer["id"] == base_id
    assert _on(longer, "date_from") == _day(200) and _on(longer, "date_to") == _day(210)
    assert longer["reason"] == "Trip"        # the reason already on the calendar wins
    assert db.query(NcoHoliday).count() == rows_before
    assert len(created) == calls_before
    assert updated == [{"id": base_event, "from": nh._today() + timedelta(days=200),
                        "to": nh._today() + timedelta(days=210)}]

    # ...and the same going backwards off the front.
    earlier = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(195), date_to=_day(201)), db, alice))
    assert earlier["id"] == base_id
    assert _on(earlier, "date_from") == _day(195) and _on(earlier, "date_to") == _day(210)
    assert earlier["on_calendar"] is True

    # A range spanning two separate bookings is a tidy-up, not an extension.
    run(nh.create_holiday(nh.HolidayBody(date_from=_day(300), date_to=_day(301)), db, alice))
    run(nh.create_holiday(nh.HolidayBody(date_from=_day(320), date_to=_day(321)), db, alice))
    try:
        run(nh.create_holiday(nh.HolidayBody(date_from=_day(299), date_to=_day(322)), db, alice))
        raise AssertionError("merged two bookings into one")
    except HTTPException as e:
        assert e.status_code == 409 and "2 of your bookings" in e.detail

    # Someone else's booking on the same dates is not a clash — the rule is one
    # absence per person, not one per squadron.
    assert run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(200), date_to=_day(204)), db, bob))["id"] != base_id

    # A cancelled booking doesn't block rebooking the same dates.
    gone = run(nh.create_holiday(nh.HolidayBody(date_from=_day(500), date_to=_day(501)), db, alice))
    run(nh.cancel_holiday(gone["id"], db, alice))
    again = run(nh.create_holiday(nh.HolidayBody(date_from=_day(500), date_to=_day(501)), db, alice))
    assert again["id"] != gone["id"]

    # ── Editing ──────────────────────────────────────────────────────────────
    assert run(nh.list_holidays(db, alice))["holidays"][0]["can_edit"] is not None

    updated.clear()
    moved = run(nh.edit_holiday(
        base_id, nh.HolidayBody(date_from=_day(230), date_to=_day(232), reason="Moved"),
        db, alice))
    assert _on(moved, "date_from") == _day(230) and _on(moved, "date_to") == _day(232)
    assert moved["reason"] == "Moved"
    assert updated and updated[-1]["id"] == base_event   # same event, new dates

    # The notice rule still applies to a new first day.
    for offset in (0, 1, nh.MIN_NOTICE_DAYS - 1):
        try:
            run(nh.edit_holiday(
                base_id, nh.HolidayBody(date_from=_day(offset), date_to=_day(offset + 1)),
                db, alice))
            raise AssertionError(f"edited to {offset} days out")
        except HTTPException as e:
            assert e.status_code == 400 and "notice" in e.detail

    # But it's about moving the start, not about touching the row at all — a
    # holiday that's nearly here can still have its reason fixed.
    soon = NcoHoliday(
        user_id=db.query(NcoHoliday).filter(NcoHoliday.id == base_id).first().user_id,
        date_from=nh._today() + timedelta(days=3), date_to=nh._today() + timedelta(days=4),
        reason="Typo", booked_by_name="alice T", booked_by_email="alice@x",
        google_event_id="evt-soon", created_at=datetime.now(),
    )
    db.add(soon)
    db.commit()
    fixed = run(nh.edit_holiday(
        soon.id, nh.HolidayBody(date_from=_day(3), date_to=_day(4), reason="Fixed"),
        db, alice))
    assert fixed["reason"] == "Fixed"

    # Bad ranges and clashes with your own other bookings are refused.
    for bad, code in (
        (nh.HolidayBody(date_from=_day(240), date_to=_day(239)), 400),
        (nh.HolidayBody(date_from=_day(240), date_to=_day(600)), 400),
        (nh.HolidayBody(date_from=_day(299), date_to=_day(302)), 409),   # hits day 300–301
    ):
        try:
            run(nh.edit_holiday(base_id, bad, db, alice))
            raise AssertionError("invalid edit accepted")
        except HTTPException as e:
            assert e.status_code == code

    # Only the author or staff can edit one, and never a cancelled one.
    try:
        run(nh.edit_holiday(base_id, nh.HolidayBody(date_from=_day(240), date_to=_day(241)), db, bob))
        raise AssertionError("edited someone else's holiday")
    except HTTPException as e:
        assert e.status_code == 403
    staff_edit = run(nh.edit_holiday(
        base_id, nh.HolidayBody(date_from=_day(1), date_to=_day(1)), db, staff))
    assert _on(staff_edit, "date_from") == _day(1)        # staff are exempt from notice
    run(nh.cancel_holiday(base_id, db, alice))
    try:
        run(nh.edit_holiday(base_id, nh.HolidayBody(date_from=_day(240), date_to=_day(241)), db, alice))
        raise AssertionError("edited a cancelled holiday")
    except HTTPException as e:
        assert e.status_code == 409

    # An edit Google refuses leaves the booking flagged for the existing retry.
    nh.update_holiday_event = lambda *a, **k: False
    stale = run(nh.edit_holiday(
        soon.id, nh.HolidayBody(date_from=_day(3), date_to=_day(5)), db, alice))
    assert _on(stale, "date_to") == _day(5)               # the record is still right
    assert stale["on_calendar"] is False             # ...the calendar isn't, yet
