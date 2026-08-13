"""OC dashboard — a single OC-only aggregate endpoint.

Real data where it exists (squadron strength, parade turnout, qualification
coverage and expiries); the committee-request section lives on the frontend.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.models import (
    Cadet, CadetAttendance, CadetQualification, Staff, StaffAttendance,
)

from core.attendance import attendance_state, PRESENT, AUTHORISED, ABSENT
from core.db import get_db
from core.qualifications import quali_expiry_cutoff
from core.security import require_oc
from routers.stats import compute_stats

router = APIRouter()

PARADE_NIGHT = "Parade Night"
# How many recent parade nights the turnout trend covers — roughly a term.
TREND_NIGHTS = 12


def _empty_counts():
    return {PRESENT: 0, AUTHORISED: 0, ABSENT: 0}


def _counts_for_nights(db: Session, model, dates: list[datetime]) -> dict:
    """date -> state counts, for the given parade nights only."""
    if not dates:
        return {}
    rows = (
        db.query(model.date, model.status)
        .filter(model.register_type == PARADE_NIGHT, model.date.in_(dates))
        .all()
    )
    counts: dict = {}
    for row_date, status in rows:
        bucket = counts.setdefault(row_date, _empty_counts())
        bucket[attendance_state(status)] += 1
    return counts


def _attendance_trend(db: Session) -> list[dict]:
    """Turnout over the most recent parade nights, newest last so it charts
    left-to-right in time order."""
    dates = [
        row[0] for row in (
            db.query(CadetAttendance.date)
            .filter(CadetAttendance.register_type == PARADE_NIGHT)
            .distinct()
            .order_by(CadetAttendance.date.desc())
            .limit(TREND_NIGHTS)
            .all()
        )
    ]
    if not dates:
        return []

    cadet_counts = _counts_for_nights(db, CadetAttendance, dates)
    staff_counts = _counts_for_nights(db, StaffAttendance, dates)
    return [
        {
            "date": night.date().isoformat(),
            "cadets": cadet_counts.get(night, _empty_counts()),
            "staff": staff_counts.get(night, _empty_counts()),
        }
        for night in sorted(dates)
    ]


def _qual_summary(db: Session, today: datetime) -> dict:
    """Counts behind the expiry table — what's already lapsed matters as much as
    what's about to, and the table only ever showed the future."""
    expired = (
        db.query(CadetQualification)
        .filter(CadetQualification.date_expires.isnot(None),
                CadetQualification.date_expires < today)
        .count()
    )
    in_30 = (
        db.query(CadetQualification)
        .filter(CadetQualification.date_expires >= today,
                CadetQualification.date_expires <= today + timedelta(days=30))
        .count()
    )
    in_90 = (
        db.query(CadetQualification)
        .filter(CadetQualification.date_expires >= today,
                CadetQualification.date_expires <= quali_expiry_cutoff(today))
        .count()
    )
    total = db.query(CadetQualification).count()
    return {"total": total, "expired": expired, "expiring_30": in_30, "expiring_90": in_90}


@router.get("/oc/dashboard")
async def oc_dashboard(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    strength = compute_stats(db)

    now = datetime.now()
    today = datetime(now.year, now.month, now.day)

    staff_rows = db.query(Staff).order_by(Staff.last_name, Staff.first_name).all()
    staff_attendance = [
        {
            "cin": s.cin,
            "name": f"{s.first_name} {s.last_name}".strip(),
            "rank": s.rank or "",
            "attendance": s.attendance or {},
        }
        for s in staff_rows
    ]

    # Qualifications expiring within the next 3 months (everything in-window, not
    # just the un-notified ones the weekly email dedupes on).
    quals = (
        db.query(CadetQualification)
        .join(Cadet)
        .filter(
            CadetQualification.date_expires >= today,
            CadetQualification.date_expires <= quali_expiry_cutoff(today),
        )
        .order_by(CadetQualification.date_expires)
        .all()
    )
    expiring_quals = [
        {
            "cadet_name": f"{q.cadet.first_name} {q.cadet.last_name}".strip(),
            "qual_type": q.qual_type,
            "date_expires": q.date_expires.strftime("%d/%m/%Y"),
            "days_left": (q.date_expires - now).days,
        }
        for q in quals
    ]

    return {
        "strength": {**strength, "total_staff": len(staff_rows)},
        "staff_attendance": staff_attendance,
        "attendance_trend": _attendance_trend(db),
        "qual_summary": _qual_summary(db, today),
        "expiring_quals": expiring_quals,
    }
