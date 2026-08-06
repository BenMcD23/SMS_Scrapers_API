"""Self-check for NCO holiday booking, cancellation and the audit trail.

Run: PYTHONPATH=app:. python -m routers.test_nco_holidays

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

    # Everyone on the page sees the whole squadron's bookings.
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

    # Staff are exempt — they can record their own at short notice.
    short = run(nh.create_holiday(
        nh.HolidayBody(date_from=_day(1), date_to=_day(1)), db, staff))
    assert short["booked_by_email"] == "staff@x"

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

    print("nco holidays self-check passed")


if __name__ == "__main__":
    test()
