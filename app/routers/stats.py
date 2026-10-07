"""Squadron stats — live numbers plus historical snapshots."""

from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from core import cache
from core.db import get_db
from core.qualifications import BADGE_TYPES, LEVELED, held_level, quali_expiry_cutoff
from core.security import require_staff, require_staff_or_nco
from database.models import Cadet, CadetQualification, StatsSnapshot

router = APIRouter()

# Cache keys shared with writers that invalidate cadet-derived data.
STATS_CACHE_KEY = "stats:current"
STATS_CACHE_TTL = 120

# The 12 leveled dashboard badges, driven off the shared qualifications catalog
# (core.qualifications) so classification stays a single source of truth and picks
# up every naming variant via substring matching — see held_level().
STAT_BADGES = [b for b in BADGE_TYPES if b.kind == LEVELED]

# Catalog slug → the key the frontend/history expect, where they differ.
BADGE_KEY_ALIAS = {"flying": "flying_badge", "swimming": "swimming_proficiency"}


# No classification recorded means they haven't passed First Class yet.
JUNIOR_CLASSIFICATION = "Junior Cadet"


def is_junior(cadet: Cadet) -> bool:
    return (cadet.classification or JUNIOR_CLASSIFICATION) == JUNIOR_CLASSIFICATION


def _badge_counts(cadets: list[Cadet], quals_by_cadet: dict) -> dict:
    """badge key -> {level label: cadet count} over the given cadets. The shared
    catalog decides the held level per badge (highest-first substring match)."""
    badges: dict = {}
    for badge in STAT_BADGES:
        out_key = BADGE_KEY_ALIAS.get(badge.key, badge.key)
        level_counts: dict = {}
        for c in cadets:
            lvl = held_level(badge, quals_by_cadet.get(c.cin, ()))
            label = lvl.capitalize() if lvl else "None"
            level_counts[label] = level_counts.get(label, 0) + 1
        badges[out_key] = level_counts
    return badges


def _cohort(cadets: list[Cadet], quals_by_cadet: dict) -> dict:
    """Badge breakdown over a group of cadets, plus the same without juniors —
    who've barely started on badges and drag every percentage down. Counted here
    so the dashboards can toggle it without another round-trip, and so snapshots
    carry it for the trend charts."""
    non_juniors = [c for c in cadets if not is_junior(c)]
    return {
        "total_cadets": len(cadets),
        "badges": _badge_counts(cadets, quals_by_cadet),
        "non_junior": {
            "total_cadets": len(non_juniors),
            "badges": _badge_counts(non_juniors, quals_by_cadet),
        },
    }


def compute_stats(db: Session) -> dict:
    cadets = db.query(Cadet).all()
    today = date_type.today()

    flight_counts: dict = {}
    age_counts: dict = {}
    rank_counts: dict = {}
    classification_counts: dict = {}
    for c in cadets:
        flight = c.flight or "Unknown"
        flight_counts[flight] = flight_counts.get(flight, 0) + 1
        rank = c.rank or "Unknown"
        rank_counts[rank] = rank_counts.get(rank, 0) + 1
        classification = c.classification or JUNIOR_CLASSIFICATION
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
        if c.date_of_birth:
            dob = c.date_of_birth
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            age_counts[str(age)] = age_counts.get(str(age), 0) + 1

    quals_by_cadet: dict = defaultdict(list)
    for q in db.query(CadetQualification).all():
        quals_by_cadet[q.cadet_id].append(q.qual_type)

    cadets_by_flight: dict = defaultdict(list)
    for c in cadets:
        cadets_by_flight[c.flight or "Unknown"].append(c)

    whole = _cohort(cadets, quals_by_cadet)
    return {
        **whole,
        "by_flight": flight_counts,
        "by_age": age_counts,
        "by_rank": rank_counts,
        "by_classification": classification_counts,
        # Same cohort shape per flight, so the dashboard's flight filter and its
        # trend charts read one structure. Only in snapshots taken since it existed.
        "flights": {f: _cohort(cs, quals_by_cadet) for f, cs in cadets_by_flight.items()},
    }


@router.get("/stats/current")
def get_current_stats(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    cached = cache.get(STATS_CACHE_KEY)
    if cached is not None:
        return cached
    stats = compute_stats(db)
    cache.set(STATS_CACHE_KEY, stats, STATS_CACHE_TTL)
    return stats


# Past this many days the trend is bucketed by week, not day: a year of daily
# points is more than a dashboard chart can show and a heavy payload.
WEEKLY_AFTER_DAYS = 183


def _window(days: int | None, start: date_type | None, end: date_type | None) -> tuple[datetime | None, datetime | None]:
    """[since, until) for a look-back of `days`, or an absolute `start`..`end`
    (both inclusive dates, either open-ended). None means unbounded."""
    if days is not None and (start or end):
        raise HTTPException(status_code=400, detail="Give either days or start/end, not both")
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if days is not None:
        return datetime.now() - timedelta(days=days), None
    since = datetime.combine(start, datetime.min.time()) if start else None
    until = datetime.combine(end, datetime.min.time()) + timedelta(days=1) if end else None
    return since, until


@router.get("/stats/history")
def get_stats_history(
    days: int | None = Query(None, ge=1, le=36500, description="Look-back window"),
    start: date_type | None = Query(None, description="First day of an absolute range"),
    end: date_type | None = Query(None, description="Last day of an absolute range"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Snapshots oldest first, the last one per day (or per ISO week for long
    ranges). A snapshot lands on every cadet-quali scrape, so a day with three
    manual runs would otherwise plot three points on one date. With no window
    at all, everything ever captured."""
    since, until = _window(days, start, end)
    query = db.query(StatsSnapshot).order_by(StatsSnapshot.captured_at.asc())
    if since:
        query = query.filter(StatsSnapshot.captured_at >= since)
    if until:
        query = query.filter(StatsSnapshot.captured_at < until)
    weekly = since is None or ((until or datetime.now()) - since).days > WEEKLY_AFTER_DAYS

    # ponytail: buckets in Python over every row in range; a few hundred small
    # rows today. Move to a SQL window query if snapshots ever number thousands.
    latest: dict = {}
    for s in query.all():
        key = s.captured_at.isocalendar()[:2] if weekly else s.captured_at.date()
        latest[key] = s  # ascending order, so the last write is the bucket's latest
    return [{"date": s.captured_at.isoformat(), "data": s.data} for s in latest.values()]


def _cadet_ref(c: Cadet) -> dict:
    return {
        "cin": c.cin,
        "name": f"{c.first_name or ''} {c.last_name or ''}".strip(),
        "flight": c.flight or "Unknown",
        "junior": is_junior(c),
    }


def _badge_award(qual_type: str) -> tuple[str, str] | None:
    """(dashboard badge key, level label) a raw qualification counts toward, or
    None if it isn't one of the dashboard badges."""
    for badge in STAT_BADGES:
        for lvl in badge.levels:  # highest first, same as held_level
            if lvl.matches(qual_type):
                return BADGE_KEY_ALIAS.get(badge.key, badge.key), lvl.level.capitalize()
    return None


@router.get("/stats/awards")
def get_recent_awards(
    days: int | None = Query(None, ge=1, le=36500, description="Look-back window; 30 if no range given"),
    start: date_type | None = Query(None, description="First day of an absolute range"),
    end: date_type | None = Query(None, description="Last day of an absolute range"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Dashboard badges gained in a window, newest first. Read live from the
    qualifications' award dates, so it needs no snapshot history."""
    if days is None and not (start or end):
        days = 30
    since, until = _window(days, start, end)
    query = (
        db.query(CadetQualification)
        .options(joinedload(CadetQualification.cadet))
        .filter(CadetQualification.date_achieved.isnot(None))
    )
    if since:
        query = query.filter(CadetQualification.date_achieved >= since)
    if until:
        query = query.filter(CadetQualification.date_achieved < until)
    quals = query.order_by(CadetQualification.date_achieved.desc()).all()
    out = []
    for q in quals:
        award = _badge_award(q.qual_type)
        if award and q.cadet:
            out.append({
                **_cadet_ref(q.cadet),
                "badge": award[0],
                "level": award[1],
                "date": q.date_achieved.date().isoformat(),
            })
    return out


@router.get("/stats/expiring")
def get_expiring_quals(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Qualifications lapsing within the same 3-month window as the weekly
    expiry email and the OC dashboard, soonest first."""
    now = datetime.now()
    today = datetime(now.year, now.month, now.day)
    quals = (
        db.query(CadetQualification)
        .options(joinedload(CadetQualification.cadet))
        .filter(
            CadetQualification.date_expires >= today,
            CadetQualification.date_expires <= quali_expiry_cutoff(today),
        )
        .order_by(CadetQualification.date_expires)
        .all()
    )
    return [
        {
            **_cadet_ref(q.cadet),
            "qual_type": q.qual_type,
            "date_expires": q.date_expires.date().isoformat(),
            "days_left": (q.date_expires - today).days,
        }
        for q in quals
        if q.cadet
    ]


@router.post("/stats/snapshot")
def create_stats_snapshot(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    snapshot = StatsSnapshot(captured_at=datetime.now(), data=compute_stats(db))
    db.add(snapshot)
    db.commit()
    return {"status": "ok", "captured_at": snapshot.captured_at.isoformat()}
