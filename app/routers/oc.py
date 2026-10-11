"""OC dashboard — a single OC-only aggregate endpoint.

Real data where it exists (squadron strength, parade turnout, qualification
coverage and expiries); the committee-request section lives on the frontend.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core import cache
from core.attendance import ABSENT, AUTHORISED, PRESENT, attendance_state
from core.db import get_db
from core.leaving import parse_emails
from core.qualifications import quali_expiry_cutoff
from core.security import require_oc
from database.models import (
    Cadet,
    CadetAttendance,
    CadetQualification,
    Staff,
    StaffAttendance,
)
from routers.stats import (
    JUNIOR_CLASSIFICATION,
    STATS_CACHE_KEY,
    STATS_CACHE_TTL,
    compute_stats,
)

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


def _quals_query(db: Session, exclude_juniors: bool):
    """Cadet qualifications, optionally limited to cadets past Junior — juniors
    hold few quals, so the OC can drop them to see the trained cohort."""
    query = db.query(CadetQualification).join(Cadet)
    if exclude_juniors:
        query = query.filter(
            Cadet.classification.isnot(None),
            Cadet.classification != JUNIOR_CLASSIFICATION,
        )
    return query


def _qual_summary(db: Session, today: datetime, exclude_juniors: bool) -> dict:
    """Counts behind the expiry table — what's already lapsed matters as much as
    what's about to, and the table only ever showed the future."""
    expired = (
        _quals_query(db, exclude_juniors)
        .filter(CadetQualification.date_expires.isnot(None),
                CadetQualification.date_expires < today)
        .count()
    )
    in_30 = (
        _quals_query(db, exclude_juniors)
        .filter(CadetQualification.date_expires >= today,
                CadetQualification.date_expires <= today + timedelta(days=30))
        .count()
    )
    in_90 = (
        _quals_query(db, exclude_juniors)
        .filter(CadetQualification.date_expires >= today,
                CadetQualification.date_expires <= quali_expiry_cutoff(today))
        .count()
    )
    total = _quals_query(db, exclude_juniors).count()
    return {"total": total, "expired": expired, "expiring_30": in_30, "expiring_90": in_90}


@router.get("/oc/dashboard")
async def oc_dashboard(
    exclude_juniors: bool = False,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    # Squadron strength — reuse the stats cache the home page already warms.
    strength = cache.get(STATS_CACHE_KEY)
    if strength is None:
        strength = compute_stats(db)
        cache.set(STATS_CACHE_KEY, strength, STATS_CACHE_TTL)

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
        _quals_query(db, exclude_juniors)
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

    # Badge coverage over the chosen cohort — both are in the cached stats.
    cohort = strength["non_junior"] if exclude_juniors else strength
    coverage = {"cadets": cohort["total_cadets"], "badges": cohort["badges"]}

    return {
        "strength": {**strength, "total_staff": len(staff_rows)},
        "badge_coverage": coverage,
        "staff_attendance": staff_attendance,
        "attendance_trend": _attendance_trend(db),
        "qual_summary": _qual_summary(db, today, exclude_juniors),
        "expiring_quals": expiring_quals,
    }


class ParentEmails(BaseModel):
    emails: str = ""


def _cadet_parent_emails(raw: str | None) -> list[str]:
    try:
        return parse_emails(raw)
    except ValueError:
        # Saved values were validated; a hand-edited bad row shouldn't 500 the page.
        return [p for p in (raw or "").split(";") if "@" in p]


@router.get("/oc/parent-emails")
def parent_emails(db: Session = Depends(get_db), idinfo: dict = Depends(require_oc)):
    """Every parent address on file, ready to paste into a BCC.

    ``bcc`` is ``a@x;b@y;c@z``, de-duplicated across cadets (siblings share a
    parent). The site never emails parents itself — the OC sends from their own
    account. ``cadets`` lets the dashboard edit each cadet's addresses and shows
    who is still missing.
    """
    rows = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    cadets = [
        {
            "cin": c.cin,
            "name": f"{c.first_name} {c.last_name}".strip(),
            "flight": c.flight,
            "parentEmails": _cadet_parent_emails(c.parent_emails),
        }
        for c in rows
    ]
    unique: dict[str, str] = {}
    for c in cadets:
        for email in c["parentEmails"]:
            unique.setdefault(email.lower(), email)
    return {
        "bcc": ";".join(unique.values()),
        "count": len(unique),
        "missing": sum(1 for c in cadets if not c["parentEmails"]),
        "cadets": cadets,
    }


@router.put("/oc/parent-emails/{cin}")
def set_parent_emails(
    cin: int,
    body: ParentEmails,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    """Replace one cadet's parent addresses. Accepts ``;``, comma or newline
    separated text; an empty string clears them."""
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")
    try:
        emails = parse_emails(body.emails)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cadet.parent_emails = ";".join(emails) or None
    db.commit()
    return {"cin": cin, "parentEmails": emails}
