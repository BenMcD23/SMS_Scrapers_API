"""NCO holidays — self-service absence booking, mirrored to Google Calendar.

An NCO books their own holiday, which is pushed to the shared "NCO Holidays"
calendar as an all-day event. They can cancel it later, which pulls the event
back off the calendar, but the booking itself is never deleted: the row keeps
who booked it, when they added it, and who cancelled it and when.

You can only book your own holiday. Everyone who can see this page (NCO, SNCO,
staff) sees the whole squadron's list; staff can additionally cancel someone
else's booking, which is recorded against their name.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.models import NcoHoliday, User

from core.calendar import (
    calendar_configured, create_holiday_event, delete_holiday_event,
)
from core.db import get_db, get_or_create_user
from core.security import get_user_role, require_staff_or_nco

router = APIRouter()

# A holiday longer than this is almost certainly a typo in the end date.
MAX_HOLIDAY_DAYS = 120
MAX_REASON_CHARS = 500


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


def _parse_date(value: str, field: str) -> datetime:
    """Accept the browser's "YYYY-MM-DD" (or a full ISO timestamp), normalised
    to midnight — holidays are whole days, times would only confuse the range."""
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field} date")
    return parsed.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)


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
    """The whole squadron's bookings, cancelled ones included — this list is the
    audit trail, so nothing is filtered out."""
    user = get_or_create_user(db, idinfo)
    is_staff = _is_staff(idinfo)
    holidays = (
        db.query(NcoHoliday)
        .order_by(NcoHoliday.date_from.desc(), NcoHoliday.id.desc())
        .all()
    )
    return {
        "holidays": [_serialise(h, user, is_staff) for h in holidays],
        "is_staff": is_staff,
        "calendar_configured": calendar_configured(),
    }


@router.post("/nco-holidays")
async def create_holiday(
    body: HolidayBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Book a holiday for yourself. There's deliberately no way to book one for
    someone else — the booking is the audit record of who asked for it."""
    user = get_or_create_user(db, idinfo)

    date_from = _parse_date(body.date_from, "start")
    date_to = _parse_date(body.date_to, "end")
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="The end date is before the start date")
    if (date_to - date_from).days + 1 > MAX_HOLIDAY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"A holiday can't be longer than {MAX_HOLIDAY_DAYS} days — "
                   "book it in shorter blocks",
        )

    reason = (body.reason or "").strip()[:MAX_REASON_CHARS]
    name = _user_name(user)

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
