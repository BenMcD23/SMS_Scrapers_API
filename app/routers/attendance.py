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
from core.ranks import is_nco_rank, nco_team
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


def _nco_counts(db: Session):
    """Same shape as `_grouped_counts`, but only the cadets whose rank puts them
    on the NCO team.

    Rank comes back from SQL and is judged here, so the one rule in `core.ranks`
    decides rather than a hand-rolled SQL `lower()`. It is the cadet's *current*
    rank, so an older night is counted against today's NCO team — the same basis
    the appraisal page uses, and the only one on record: the register doesn't
    store the rank someone held on the night.
    """
    rows = (
        db.query(
            CadetAttendance.date, CadetAttendance.register_type,
            CadetAttendance.status, Cadet.rank,
            func.count(CadetAttendance.cadet_id).label("n"),
        )
        .join(Cadet, CadetAttendance.cadet_id == Cadet.cin)
        .group_by(
            CadetAttendance.date, CadetAttendance.register_type,
            CadetAttendance.status, Cadet.rank,
        )
        .all()
    )
    grouped: dict[tuple, dict] = {}
    for row_date, register_type, status, rank, n in rows:
        if not is_nco_rank(rank):
            continue
        counts = grouped.setdefault((row_date.date(), register_type), _empty_counts())
        counts[attendance_state(status)] += n
    return grouped


@router.get("/attendance/nights")
def attendance_nights(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Every night on record, newest first, with cadet, NCO and staff head counts.

    Returned whole — one row per night is small even over a 15-year register, so
    the client filters and charts it without another round trip. NCOs are a
    subset of the cadet counts, not a fourth group beside them, so a night is
    listed on the strength of its cadet and staff registers alone.
    """
    cadets = _grouped_counts(db, CadetAttendance, CadetAttendance.cadet_id)
    staff = _grouped_counts(db, StaffAttendance, StaffAttendance.staff_id)
    ncos = _nco_counts(db)

    nights = []
    for key in sorted(set(cadets) | set(staff), reverse=True):
        night, register_type = key
        nights.append({
            "date": night.isoformat(),
            "registerType": register_type,
            "cadets": cadets.get(key, _empty_counts()),
            "ncos": ncos.get(key, _empty_counts()),
            "staff": staff.get(key, _empty_counts()),
        })
    return nights


def _roster(db: Session, model, person_model, join_col, night: Date, register_type: str | None,
            ranked_ncos: bool = False):
    """Everyone on one night's register, with the name from their person row.

    `ranked_ncos` marks up the cadet register so the client can narrow it to the
    NCO team without knowing which ranks those are. It stays off for staff —
    a staff sergeant holds the rank but is not a cadet NCO.
    """
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
            "isNco": ranked_ncos and is_nco_rank(person.rank),
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
        "cadets": _roster(db, CadetAttendance, Cadet, CadetAttendance.cadet_id, night, registerType,
                          ranked_ncos=True),
        "staff": _roster(db, StaffAttendance, Staff, StaffAttendance.staff_id, night, registerType),
    }


@router.get("/attendance/ncos")
def nco_attendance(
    start: Date | None = Query(None, alias="from"),
    end: Date | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Each NCO's own register over a date range — one row per NCO, carrying the
    nights they were marked on rather than a single figure.

    Both bounds are inclusive whole days, and an open one means "everything on
    record that side". Counts and percentages are deliberately left to the
    client: it already owns how they're presented, and the page's register-type
    filter has to re-derive them anyway.

    The team is whoever holds an NCO rank *now* — the register doesn't record the
    rank someone held on the night, so a promotion brings the whole of that
    cadet's history onto this page.
    """
    ncos = nco_team(db)
    nights: dict[int, list] = {cadet.cin: [] for cadet in ncos}

    if ncos:
        query = db.query(CadetAttendance).filter(CadetAttendance.cadet_id.in_(list(nights)))
        if start:
            query = query.filter(CadetAttendance.date >= datetime(start.year, start.month, start.day))
        if end:
            # The bound is a whole day, so it runs to the start of the next one.
            query = query.filter(
                CadetAttendance.date < datetime(end.year, end.month, end.day) + timedelta(days=1)
            )
        for record in query.all():
            nights[record.cadet_id].append({
                "date": record.date.date().isoformat(),
                "registerType": record.register_type,
                "status": record.status,
                "state": attendance_state(record.status),
            })

    # Newest first, the same order the per-person register arrives in.
    for entries in nights.values():
        entries.sort(key=lambda n: (n["date"], n["registerType"] or ""), reverse=True)

    return {
        "from": start.isoformat() if start else None,
        "to": end.isoformat() if end else None,
        "ncos": [
            {
                "cin": cadet.cin,
                "name": f"{cadet.first_name or ''} {cadet.last_name or ''}".strip(),
                "rank": cadet.rank,
                "nights": nights[cadet.cin],
            }
            for cadet in ncos
        ],
    }
