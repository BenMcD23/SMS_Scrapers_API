"""NCO holidays — self-service absence booking, mirrored to Google Calendar.

An NCO books their own holiday, which is pushed to the shared "NCO Holidays"
calendar as an all-day event. They can edit it or cancel it later, which moves
or pulls the event, but the booking itself is never deleted: the row keeps who
booked it, when they added it, and who cancelled it and when.

An NCO can't book the same day twice. Booking a range that runs over one of
their existing bookings extends that booking instead of creating a second row,
since that's someone lengthening one absence rather than taking two.

You can only book your own holiday, and only NCOs book at all — this tracks NCO
absence, and staff leave isn't the squadron's business to record here. Everyone
who can see this page sees the whole list; staff can additionally edit or cancel
someone else's booking, which is recorded against their name.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.models import NcoHoliday, User

from core.calendar import (
    calendar_configured, create_holiday_event, delete_holiday_event,
    update_holiday_event,
)
from core.db import get_db, get_or_create_user
from core.security import get_user_role, require_staff_or_nco

router = APIRouter()

# A holiday longer than this is almost certainly a typo in the end date.
MAX_HOLIDAY_DAYS = 120
MAX_REASON_CHARS = 500

# Notice an NCO has to give: the first day of the holiday must be at least this
# many days after today, so booking on the 1st means the 15th is the earliest
# day off. Staff are exempt — they're the ones who'd need to record something at
# short notice, and they can only ever book their own anyway.
MIN_NOTICE_DAYS = 14


class HolidayBody(BaseModel):
    date_from: str          # "YYYY-MM-DD"
    date_to: str            # "YYYY-MM-DD", inclusive
    reason: str = ""


# ── helpers ──────────────────────────────────────────────────────────────────

def _user_name(user: User) -> str:
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or user.email


def _is_staff(idinfo: dict) -> bool:
    return get_user_role(idinfo["email"]) == "staff"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _today() -> datetime:
    """Midnight today, to compare against the midnight-normalised booking dates."""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _min_notice_days(idinfo: dict) -> int:
    """Notice this viewer has to give. 0 for staff, who are exempt."""
    return 0 if _is_staff(idinfo) else MIN_NOTICE_DAYS


def _parse_date(value: str, field: str) -> datetime:
    """Accept the browser's "YYYY-MM-DD" (or a full ISO timestamp), normalised
    to midnight — holidays are whole days, times would only confuse the range."""
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field} date")
    return parsed.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


def _span(holiday: NcoHoliday) -> str:
    """The booking's dates, for error messages that name what you clashed with."""
    if holiday.date_from.date() == holiday.date_to.date():
        return f"{holiday.date_from:%-d %b %Y}"
    return f"{holiday.date_from:%-d %b} – {holiday.date_to:%-d %b %Y}"


def _validate_range(date_from: datetime, date_to: datetime) -> None:
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="The end date is before the start date")
    if (date_to - date_from).days + 1 > MAX_HOLIDAY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"A holiday can't be longer than {MAX_HOLIDAY_DAYS} days — "
                   "book it in shorter blocks",
        )


def _require_notice(date_from: datetime, notice_days: int) -> None:
    if not notice_days:
        return
    earliest = _today() + timedelta(days=notice_days)
    if date_from < earliest:
        raise HTTPException(
            status_code=400,
            detail=f"Holidays need at least {notice_days} days' notice — the "
                   f"earliest you can book from is {earliest:%-d %b %Y}",
        )


def _overlapping(db: Session, user_id: int, date_from: datetime, date_to: datetime,
                 exclude_id: int | None = None) -> list[NcoHoliday]:
    """This person's live bookings whose dates run over date_from..date_to.
    Ranges are inclusive at both ends, so two overlap when each starts on or
    before the other ends. Cancelled rows are history, not a clash."""
    query = db.query(NcoHoliday).filter(
        NcoHoliday.user_id == user_id,
        NcoHoliday.cancelled_at.is_(None),
        NcoHoliday.date_from <= date_to,
        NcoHoliday.date_to >= date_from,
    )
    if exclude_id is not None:
        query = query.filter(NcoHoliday.id != exclude_id)
    return query.order_by(NcoHoliday.date_from, NcoHoliday.id).all()


def _resync(holiday: NcoHoliday) -> None:
    """Point the calendar event at the row's current dates. Anything that goes
    wrong drops the event id, so the row shows "Not synced" and the Retry button
    already on it recreates the event — no second recovery path to maintain.

    ponytail: a transient patch failure can leave a stale event behind for Retry
    to duplicate. Delete-then-recreate here if that ever actually happens."""
    if not update_holiday_event(
        holiday.google_event_id, holiday.booked_by_name, holiday.booked_by_email,
        holiday.date_from, holiday.date_to, holiday.reason or "",
    ):
        holiday.google_event_id = None


def _serialise(holiday: NcoHoliday, viewer: User, is_staff: bool) -> dict:
    cancelled = holiday.cancelled_at is not None
    return {
        "id": holiday.id,
        "date_from": _iso(holiday.date_from),
        "date_to": _iso(holiday.date_to),
        "reason": holiday.reason or "",
        "booked_by_name": holiday.booked_by_name,
        "booked_by_email": holiday.booked_by_email,
        "created_at": _iso(holiday.created_at),
        "cancelled": cancelled,
        "cancelled_at": _iso(holiday.cancelled_at),
        "cancelled_by_name": holiday.cancelled_by_name,
        "is_mine": holiday.user_id == viewer.id,
        # Cancelling is the author's call; staff can also do it to fix mistakes.
        "can_cancel": not cancelled and (holiday.user_id == viewer.id or is_staff),
        "can_edit": not cancelled and (holiday.user_id == viewer.id or is_staff),
        # False means the booking is recorded here but never made it onto the
        # calendar — the UI flags it so someone can retry the push.
        "on_calendar": bool(holiday.google_event_id),
    }


def _get_or_404(db: Session, holiday_id: int) -> NcoHoliday:
    holiday = db.query(NcoHoliday).filter(NcoHoliday.id == holiday_id).first()
    if not holiday:
        raise HTTPException(status_code=404, detail="Holiday not found")
    return holiday


# ── endpoints ────────────────────────────────────────────────────────────────

@router.get("/nco-holidays")
async def list_holidays(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Staff get the whole squadron's bookings, cancelled ones included — that
    list is the audit trail. NCOs get everyone's upcoming bookings plus their
    own history, which is all the "Upcoming"/"Mine" tabs ever show."""
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    holidays = (
        db.query(NcoHoliday)
        .order_by(NcoHoliday.date_from.desc(), NcoHoliday.id.desc())
        .all()
    )
    if not is_staff:
        today = _today()
        holidays = [
            h for h in holidays
            if h.user_id == user.id or (h.cancelled_at is None and h.date_to >= today)
        ]
    notice_days = _min_notice_days(idinfo)
    return {
        "holidays": [_serialise(h, user, is_staff) for h in holidays],
        "is_staff": is_staff,
        # Staff manage the list rather than appearing on it, so the UI drops the
        # booking form and the "Mine" filter for them entirely.
        "can_book": not is_staff,
        "calendar_configured": calendar_configured(),
        # The booking form reads these rather than hardcoding the rule, so the
        # date picker can't drift from what the API actually enforces.
        "min_notice_days": notice_days,
        "earliest_booking_date": (
            (_today() + timedelta(days=notice_days)).date().isoformat()
            if notice_days else None
        ),
    }


@router.post("/nco-holidays")
async def create_holiday(
    body: HolidayBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Book a holiday for yourself. There's deliberately no way to book one for
    someone else — the booking is the audit record of who asked for it."""
    if _is_staff(idinfo):
        raise HTTPException(
            status_code=403,
            detail="This calendar is for NCO absence — staff leave isn't booked here",
        )
    user = get_or_create_user(db, idinfo)

    date_from = _parse_date(body.date_from, "start")
    date_to = _parse_date(body.date_to, "end")
    _validate_range(date_from, date_to)
    _require_notice(date_from, _min_notice_days(idinfo))

    reason = (body.reason or "").strip()[:MAX_REASON_CHARS]
    name = _user_name(user)

    overlaps = _overlapping(db, user.id, date_from, date_to)
    if len(overlaps) > 1:
        # Swallowing several bookings into one would need the absorbed rows
        # marked as something other than "cancelled" to stay honest, and this
        # only happens if someone booked scattered days first. Let them tidy up.
        raise HTTPException(
            status_code=409,
            detail=f"Those dates run over {len(overlaps)} of your bookings "
                   f"({', '.join(_span(h) for h in overlaps)}) — remove them and "
                   "book the whole period in one go",
        )
    if overlaps:
        return _extend(db, overlaps[0], date_from, date_to, reason, user, idinfo)

    # Record the booking before pushing it, so a Calendar outage can only cost
    # us the calendar entry (recoverable with /sync) and never the booking.
    holiday = NcoHoliday(
        user_id=user.id,
        date_from=date_from,
        date_to=date_to,
        reason=reason,
        booked_by_name=name,
        booked_by_email=user.email,
        created_at=datetime.now(),
    )
    db.add(holiday)
    db.commit()

    holiday.google_event_id = create_holiday_event(
        name, user.email, date_from, date_to, reason,
    )
    db.commit()
    db.refresh(holiday)
    return _serialise(holiday, user, _is_staff(idinfo))


def _extend(db: Session, existing: NcoHoliday, date_from: datetime, date_to: datetime,
            reason: str, user: User, idinfo: dict) -> dict:
    """Stretch a booking the new dates run over, rather than leaving the NCO with
    two rows for one absence. Booking dates they already have off changes
    nothing, so that's the duplicate the caller was told to expect."""
    merged_from = min(date_from, existing.date_from)
    merged_to = max(date_to, existing.date_to)
    if (merged_from, merged_to) == (existing.date_from, existing.date_to):
        raise HTTPException(
            status_code=409,
            detail=f"You've already booked {_span(existing)} off — edit that "
                   "booking if you want to change it",
        )

    existing.date_from = merged_from
    existing.date_to = merged_to
    # The original reason is the one that's been on the calendar, so it wins;
    # a new one only fills a blank.
    if reason and not (existing.reason or "").strip():
        existing.reason = reason
    _resync(existing)
    db.commit()
    db.refresh(existing)
    return _serialise(existing, user, _is_staff(idinfo))


@router.patch("/nco-holidays/{holiday_id}")
async def edit_holiday(
    holiday_id: int,
    body: HolidayBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Change a booking's dates or reason in place, moving the calendar event
    with it. Same permissions as cancelling: your own, or anyone's if staff."""
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    holiday = _get_or_404(db, holiday_id)

    if holiday.user_id != user.id and not is_staff:
        raise HTTPException(
            status_code=403, detail="You can only edit your own holidays",
        )
    if holiday.cancelled_at is not None:
        raise HTTPException(
            status_code=409,
            detail="This holiday is cancelled — book a new one instead",
        )

    date_from = _parse_date(body.date_from, "start")
    date_to = _parse_date(body.date_to, "end")
    _validate_range(date_from, date_to)
    # Notice only bites when the first day actually moves. Otherwise fixing a
    # typo in the reason would be blocked on a holiday that's about to start,
    # which isn't what the rule is for.
    if date_from != holiday.date_from:
        _require_notice(date_from, _min_notice_days(idinfo))

    clash = _overlapping(db, holiday.user_id, date_from, date_to, exclude_id=holiday.id)
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"Those dates run over another booking ({_span(clash[0])}) — "
                   "remove that one first",
        )

    holiday.date_from = date_from
    holiday.date_to = date_to
    holiday.reason = (body.reason or "").strip()[:MAX_REASON_CHARS]
    _resync(holiday)
    db.commit()
    db.refresh(holiday)
    return _serialise(holiday, user, is_staff)


@router.post("/nco-holidays/{holiday_id}/cancel")
async def cancel_holiday(
    holiday_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Pull a holiday off the calendar. The row stays — it just gains a
    cancellation stamp, so the booking and its cancellation are both on record."""
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    holiday = _get_or_404(db, holiday_id)

    if holiday.user_id != user.id and not is_staff:
        raise HTTPException(
            status_code=403, detail="You can only remove your own holidays",
        )
    if holiday.cancelled_at is not None:
        raise HTTPException(status_code=409, detail="This holiday is already cancelled")

    # Only drop the event id once Google confirms the delete, so a failed call
    # leaves something for the retry to clean up rather than orphaning the event.
    if holiday.google_event_id and delete_holiday_event(holiday.google_event_id):
        holiday.google_event_id = None

    holiday.cancelled_at = datetime.now()
    holiday.cancelled_by_name = _user_name(user)
    holiday.cancelled_by_email = user.email
    db.commit()
    db.refresh(holiday)
    return _serialise(holiday, user, is_staff)


@router.post("/nco-holidays/{holiday_id}/sync")
async def sync_holiday(
    holiday_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Retry the calendar push for a booking that saved while Calendar was
    unreachable, or the delete for one cancelled in the same state."""
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    holiday = _get_or_404(db, holiday_id)

    if holiday.user_id != user.id and not is_staff:
        raise HTTPException(
            status_code=403, detail="You can only sync your own holidays",
        )
    if not calendar_configured():
        raise HTTPException(
            status_code=503, detail="The NCO Holidays calendar isn't configured",
        )

    if holiday.cancelled_at is not None:
        if holiday.google_event_id and delete_holiday_event(holiday.google_event_id):
            holiday.google_event_id = None
        elif holiday.google_event_id:
            raise HTTPException(status_code=502, detail="Google Calendar didn't respond")
    elif not holiday.google_event_id:
        event_id = create_holiday_event(
            holiday.booked_by_name, holiday.booked_by_email,
            holiday.date_from, holiday.date_to, holiday.reason or "",
        )
        if not event_id:
            raise HTTPException(status_code=502, detail="Google Calendar didn't respond")
        holiday.google_event_id = event_id

    db.commit()
    db.refresh(holiday)
    return _serialise(holiday, user, is_staff)
