"""Squadron-wide attendance — per-night summaries and the roster behind each one.

The per-person register lives on the cadet/staff detail endpoints; this is the
other direction, pivoting the same two tables by night instead of by person.
"""

from datetime import date as Date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Cadet, CadetAttendance, Staff, StaffAttendance

from core.attendance import attendance_state, PRESENT, AUTHORISED, ABSENT
from core.db import get_db
from core.security import require_staff

router = APIRouter()


def _empty_counts():
    return {PRESENT: 0, AUTHORISED: 0, ABSENT: 0}


def _grouped_counts(db: Session, model, person_id_col):
    """(date, register_type) -> state counts, aggregated in SQL then classified
    here so the present/authorised/absent rule stays in one place."""
    rows = (
        db.query(
            model.date, model.register_type, model.status,
            func.count(person_id_col).label("n"),
        )
        .group_by(model.date, model.register_type, model.status)
        .all()
    )
    grouped: dict[tuple, dict] = {}
    for row_date, register_type, status, n in rows:
        key = (row_date.date(), register_type)
        counts = grouped.setdefault(key, _empty_counts())
        counts[attendance_state(status)] += n
    return grouped


@router.get("/attendance/nights")
def attendance_nights(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Every night on record, newest first, with cadet and staff head counts.

    Returned whole — one row per night is small even over a 15-year register, so
    the client filters and charts it without another round trip.
    """
    cadets = _grouped_counts(db, CadetAttendance, CadetAttendance.cadet_id)
    staff = _grouped_counts(db, StaffAttendance, StaffAttendance.staff_id)

    nights = []
    for key in sorted(set(cadets) | set(staff), reverse=True):
        night, register_type = key
        nights.append({
            "date": night.isoformat(),
            "registerType": register_type,
            "cadets": cadets.get(key, _empty_counts()),
            "staff": staff.get(key, _empty_counts()),
        })
    return nights


def _roster(db: Session, model, person_model, join_col, night: Date, register_type: str | None):
    """Everyone on one night's register, with the name from their person row."""
    start = datetime(night.year, night.month, night.day)
    query = (
        db.query(model, person_model)
        .join(person_model, join_col == person_model.cin)
        .filter(model.date >= start, model.date < start + timedelta(days=1))
    )
    if register_type:
        query = query.filter(model.register_type == register_type)

    people = []
    for record, person in query.all():
        people.append({
            "cin": person.cin,
            "name": f"{person.first_name or ''} {person.last_name or ''}".strip(),
            "rank": person.rank,
            "status": record.status,
            "state": attendance_state(record.status),
            "registerType": record.register_type,
        })
    # Absent first — on a squadron-wide view the exceptions are what's looked at.
    order = {ABSENT: 0, AUTHORISED: 1, PRESENT: 2}
    people.sort(key=lambda p: (order[p["state"]], p["name"].casefold()))
    return people


@router.get("/attendance/nights/{night}")
def attendance_night_detail(
    night: Date,
    registerType: str | None = Query(None),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Who was on the register for one night, split into cadets and staff."""
    return {
        "date": night.isoformat(),
        "registerType": registerType,
        "cadets": _roster(db, CadetAttendance, Cadet, CadetAttendance.cadet_id, night, registerType),
        "staff": _roster(db, StaffAttendance, Staff, StaffAttendance.staff_id, night, registerType),
    }
