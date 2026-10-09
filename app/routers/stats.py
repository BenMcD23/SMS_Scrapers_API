"""Squadron stats — live numbers plus historical snapshots."""

from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload, selectinload

from core import cache
from core.db import get_db
from core.qualifications import (
    BADGE_TYPE_BY_KEY,
    BADGE_TYPES,
    LEVELED,
    flying_blue_stages,
    held_level,
    qual_names_with_flights,
    quali_expiry_cutoff,
)
from core.security import require_staff, require_staff_or_nco
from core.theory_lessons import LEADING, SENIOR_MASTER, THEORY_LESSON_BY_KEY, THEORY_LESSONS
from database.models import Cadet, CadetFlight, CadetQualification, CadetSnapshot, StatsSnapshot, StatsTarget

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

    quals_by_cadet = _quals_by_cadet(db)

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


def _quals_by_cadet(db: Session) -> dict:
    """cin -> qualification names, plus what each cadet's flying record proves
    (qual_names_with_flights), so the dashboards count Blue Flying the same way
    the audit and badge orders do."""
    out: dict = defaultdict(list)
    for q in db.query(CadetQualification).all():
        out[q.cadet_id].append(q.qual_type)
    flights: dict = defaultdict(list)
    for f in db.query(CadetFlight).all():
        flights[f.cadet_id].append(f)
    for cin, cadet_flights in flights.items():
        out[cin] = qual_names_with_flights(out[cin], cadet_flights)
    return out


def _held_levels(quals: list[str]) -> dict:
    """Dashboard badge key -> held level label, for the badges the cadet holds."""
    out = {}
    for badge in STAT_BADGES:
        lvl = held_level(badge, quals)
        if lvl:
            out[BADGE_KEY_ALIAS.get(badge.key, badge.key)] = lvl.capitalize()
    return out


def save_snapshot(db: Session) -> StatsSnapshot:
    """Capture the squadron totals and every cadet as they stand now, in one
    commit. Shared by the cadet-quali scraper, the manual endpoint and the
    weekly job, so all three leave the same history behind."""
    snapshot = StatsSnapshot(captured_at=datetime.now(), data=compute_stats(db))
    db.add(snapshot)
    db.flush()
    quals = _quals_by_cadet(db)
    db.add_all(
        CadetSnapshot(
            snapshot_id=snapshot.id,
            cin=c.cin,
            name=f"{c.first_name or ''} {c.last_name or ''}".strip(),
            flight=c.flight,
            rank=c.rank,
            classification=c.classification,
            junior=is_junior(c),
            badges=_held_levels(quals.get(c.cin, [])),
        )
        for c in db.query(Cadet).all()
    )
    db.commit()
    return snapshot


@router.post("/stats/snapshot")
def create_stats_snapshot(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    snapshot = save_snapshot(db)
    return {"status": "ok", "captured_at": snapshot.captured_at.isoformat()}


# ── drill-down ────────────────────────────────────────────────────────────────

LEVEL_LABELS = {lvl.level.capitalize() for b in STAT_BADGES for lvl in b.levels} | {"None"}
STAT_BADGE_KEYS = {BADGE_KEY_ALIAS.get(b.key, b.key) for b in STAT_BADGES}


def _latest_snapshot_on(db: Session, day: date_type) -> StatsSnapshot | None:
    """The last snapshot captured on or before `day` that has per-cadet rows
    (snapshots from before those existed only hold totals)."""
    until = datetime.combine(day, datetime.min.time()) + timedelta(days=1)
    return (
        db.query(StatsSnapshot)
        .filter(StatsSnapshot.captured_at < until)
        .filter(db.query(CadetSnapshot.id).filter(CadetSnapshot.snapshot_id == StatsSnapshot.id).exists())
        .order_by(StatsSnapshot.captured_at.desc())
        .first()
    )


@router.get("/stats/cadets")
def get_stats_cadets(
    badge: str | None = Query(None, description="Dashboard badge key; pair with level"),
    level: str | None = Query(None, description='Held level label, or "None" for not held'),
    classification: str | None = None,
    min_classification: str | None = Query(None, description="At this classification or beyond (the funnel)"),
    flight: str | None = Query(None, description='Flight letter, or "Unknown" for none'),
    exclude_juniors: bool = False,
    on: date_type | None = Query(None, description="As of this day, from the per-cadet snapshots"),
    blue_flying: str | None = Query(None, description="Blue Flying state from /stats/progress"),
    exam: str | None = Query(None, description="Theory lesson key; the cadets who still need it"),
    exam_passed: bool | None = Query(None, description="With exam: passed it, or not yet"),
    service: str | None = Query(None, description="Time-at-317 band from /stats/progress"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """The cadets behind a number on the stats page: who holds a badge at a
    level, who is at a classification, where they are with Blue Flying or an
    exam, how long they've served. Live by default; `on` a past day reads the
    last per-cadet snapshot taken by then (badges and classification only)."""
    progress_filters = (blue_flying, exam, exam_passed, service)
    if any(f is not None for f in progress_filters) and on is not None and on < date_type.today():
        raise HTTPException(status_code=400, detail="Flying, exam and service filters are live only")
    if blue_flying is not None and blue_flying not in BLUE_FLYING_STATES:
        raise HTTPException(status_code=400, detail=f"Unknown Blue Flying state {blue_flying!r}")
    if (exam is None) != (exam_passed is None):
        raise HTTPException(status_code=400, detail="exam and exam_passed go together")
    if exam is not None and exam not in EXAM_LESSON_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown exam {exam!r}")
    if service is not None and service not in SERVICE_BANDS:
        raise HTTPException(status_code=400, detail=f"Unknown service band {service!r}")
    if (badge is None) != (level is None):
        raise HTTPException(status_code=400, detail="badge and level go together")
    if badge is not None and badge not in STAT_BADGE_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown badge {badge!r}")
    if level is not None and level not in LEVEL_LABELS:
        raise HTTPException(status_code=400, detail=f"Unknown level {level!r}")
    if min_classification is not None and min_classification not in CLASSIFICATION_STEPS:
        raise HTTPException(status_code=400, detail=f"Unknown classification {min_classification!r}")

    if on is not None and on < date_type.today():
        snap = _latest_snapshot_on(db, on)
        if snap is None:
            raise HTTPException(status_code=404, detail="No per-cadet snapshot on or before that day")
        rows = [
            {"cin": r.cin, "name": r.name, "flight": r.flight or "Unknown", "rank": r.rank or "",
             "classification": r.classification or JUNIOR_CLASSIFICATION, "junior": r.junior, "badges": r.badges}
            for r in db.query(CadetSnapshot).filter(CadetSnapshot.snapshot_id == snap.id)
        ]
        as_of = snap.captured_at.isoformat()
    else:
        quals = _quals_by_cadet(db)
        today = date_type.today()
        rows = [
            {**_cadet_ref(c), "rank": c.rank or "", "classification": c.classification or JUNIOR_CLASSIFICATION,
             "badges": _held_levels(quals.get(c.cin, [])),
             "blue_flying": _blue_flying_state(c), "service": _service_band(c.joined_on, today),
             "theory": {t.lesson_key for t in c.theory_progress}}
            for c in _load_progress(db)
        ]
        as_of = None

    def keep(r: dict) -> bool:
        if flight is not None and r["flight"] != flight:
            return False
        if exclude_juniors and r["junior"]:
            return False
        if classification is not None and r["classification"] != classification:
            return False
        if min_classification is not None and _step(r["classification"]) < CLASSIFICATION_STEPS.index(min_classification):
            return False
        if badge is not None and r["badges"].get(badge, "None") != level:
            return False
        if blue_flying is not None and r["blue_flying"] != blue_flying:
            return False
        if service is not None and r["service"] != service:
            return False
        if exam is not None:
            lesson = THEORY_LESSON_BY_KEY[exam]
            if r["classification"] not in EXAM_COHORTS[lesson.category]:
                return False
            if (exam in r["theory"]) != exam_passed:
                return False
        return True

    cadets = sorted((r for r in rows if keep(r)), key=lambda r: r["name"].casefold())
    for r in cadets:
        r["level"] = r["badges"].get(badge, "None") if badge else None
        for key in ("badges", "blue_flying", "service", "theory"):
            r.pop(key, None)
    return {"as_of": as_of, "cadets": cadets}


# ── history per cadet: funnel and retention ───────────────────────────────────

CLASSIFICATION_STEPS = ["Junior Cadet", "First Class Cadet", "Leading Cadet", "Senior Cadet", "Master Air Cadet"]


def _step(classification: str | None) -> int:
    c = classification or JUNIOR_CLASSIFICATION
    return CLASSIFICATION_STEPS.index(c) if c in CLASSIFICATION_STEPS else 0


def _cadet_timelines(db: Session) -> tuple[dict, list[datetime]]:
    """cin -> [(captured_at, row)] oldest first, plus every per-cadet snapshot
    date. ponytail: loads every per-cadet row (~cadets × weeks, tens of
    thousands after years); aggregate in SQL if it ever gets slow."""
    timelines: dict = defaultdict(list)
    dates: set = set()
    q = (
        db.query(StatsSnapshot.captured_at, CadetSnapshot)
        .join(CadetSnapshot, CadetSnapshot.snapshot_id == StatsSnapshot.id)
        .order_by(StatsSnapshot.captured_at.asc())
    )
    for captured_at, row in q:
        timelines[row.cin].append((captured_at, row))
        dates.add(captured_at)
    return timelines, sorted(dates)


@router.get("/stats/funnel")
def get_classification_funnel(
    flight: str | None = None,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """How many cadets on strength have reached each classification, and the
    typical time between steps.

    Cadets the Volunteer Portal sync has dated are timed exactly: joining to
    First Class, then classification to classification. Anyone else is timed
    from the per-cadet history, and only for a step they were seen moving into
    *and* seen moving into the one before — someone already Leading when
    tracking began says nothing about how long Leading takes. Weekly snapshots
    make those accurate to about a week."""
    cadets = [c for c in db.query(Cadet).all() if flight is None or (c.flight or "Unknown") == flight]
    reached = [sum(1 for c in cadets if _step(c.classification) >= i) for i in range(len(CLASSIFICATION_STEPS))]

    durations: dict = defaultdict(list)
    # The Volunteer Portal sync gives exact dates (joined, then each
    # classification passed); those cadets are timed from them.
    dated = set()
    for c in cadets:
        for step, days in _portal_step_days(c).items():
            durations[step].append(days)
            dated.add(c.cin)

    timelines, _ = _cadet_timelines(db)
    for cin, points in timelines.items():
        if cin in dated:
            continue
        if flight is not None and (points[-1][1].flight or "Unknown") != flight:
            continue
        entered: dict = {}  # step -> first time seen at it, only if seen arriving
        prev = _step(points[0][1].classification)
        for at, row in points[1:]:
            step = _step(row.classification)
            if step > prev:
                entered.setdefault(step, at)
            prev = step
        for step, at in entered.items():
            if step - 1 in entered:
                durations[step].append((at - entered[step - 1]).days)

    steps = []
    for i, name in enumerate(CLASSIFICATION_STEPS):
        times = durations.get(i, [])
        steps.append({
            "name": name,
            "reached": reached[i],
            "pct_of_previous": None if i == 0 or not reached[i - 1] else round(100 * reached[i] / reached[i - 1]),
            "median_days_from_previous": sorted(times)[len(times) // 2] if times else None,
            "timed_cadets": len(times),
        })
    return {"total": len(cadets), "steps": steps}


def _portal_step_days(c: Cadet) -> dict[int, int]:
    """step -> days it took this cadet to reach it from the step before, from
    the portal's dates. Step 1 (First Class) counts from joining 317; a cadet
    who arrived already First Class from another squadron isn't timed for it."""
    dates = {CLASSIFICATION_STEPS.index(name): date_type.fromisoformat(d)
             for name, d in (c.classification_dates or {}).items() if name in CLASSIFICATION_STEPS}
    if c.joined_on:
        dates[0] = c.joined_on
    return {
        step: (when - dates[step - 1]).days
        for step, when in dates.items()
        if step > 0 and step - 1 in dates and when >= dates[step - 1]
    }


RETENTION_MARKS = {"6m": 182, "12m": 365}


@router.get("/stats/retention")
def get_intake_retention(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Cadets grouped by the month they first appeared in a per-cadet snapshot,
    and how many were still turning up in snapshots 6 and 12 months later.

    The first snapshot's cadets are left out: they were already on strength,
    so it isn't when they joined. A mark is null until enough time has passed
    for every cadet in the intake to have reached it."""
    timelines, dates = _cadet_timelines(db)
    if not dates:
        return []
    first_ever, latest = dates[0], dates[-1]
    on_strength = {cin for (cin,) in db.query(Cadet.cin)}

    intakes: dict = defaultdict(list)
    for cin, points in timelines.items():
        joined = points[0][0]
        if joined.date() == first_ever.date():
            continue
        intakes[joined.strftime("%Y-%m")].append((cin, joined, points[-1][0]))

    out = []
    for month in sorted(intakes):
        group = intakes[month]
        row = {"intake": month, "joined": len(group), "still_on_strength": sum(1 for c, _, _ in group if c in on_strength)}
        for key, days in RETENTION_MARKS.items():
            due = max(j for _, j, _ in group) + timedelta(days=days)
            row[key] = None if latest < due else sum(1 for _, j, last in group if last >= j + timedelta(days=days))
        out.append(row)
    return out


# ── targets ───────────────────────────────────────────────────────────────────

class TargetIn(BaseModel):
    badge: str
    min_level: str | None = None
    flight: str | None = None
    exclude_juniors: bool = False
    target_pct: int = Field(ge=1, le=100)
    due: date_type


def _badge_levels(key: str) -> list[str]:
    for b in STAT_BADGES:
        if BADGE_KEY_ALIAS.get(b.key, b.key) == key:
            return [lvl.level.capitalize() for lvl in reversed(b.levels)]  # lowest first
    return []


def _check_target(body: TargetIn) -> None:
    levels = _badge_levels(body.badge)
    if not levels:
        raise HTTPException(status_code=400, detail=f"Unknown badge {body.badge!r}")
    if body.min_level is not None and body.min_level not in levels:
        raise HTTPException(status_code=400, detail=f"{body.min_level!r} isn't a level of that badge")


def _target_out(t: StatsTarget) -> dict:
    return {
        "id": t.id, "badge": t.badge, "min_level": t.min_level, "flight": t.flight,
        "exclude_juniors": t.exclude_juniors, "target_pct": t.target_pct, "due": t.due.isoformat(),
        "levels": [lvl for lvl in _badge_levels(t.badge)
                   if t.min_level is None or _badge_levels(t.badge).index(lvl) >= _badge_levels(t.badge).index(t.min_level)],
        "created_by": t.created_by,
    }


@router.get("/stats/badge-levels")
def get_badge_levels(idinfo: dict = Depends(require_staff_or_nco)):
    """Each dashboard badge's levels, lowest first, from the shared catalog —
    so the targets form offers exactly the levels the API will accept."""
    return {BADGE_KEY_ALIAS.get(b.key, b.key): _badge_levels(BADGE_KEY_ALIAS.get(b.key, b.key)) for b in STAT_BADGES}


@router.get("/stats/targets")
def list_targets(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)):
    """Every target, soonest due first. `levels` lists the levels that count
    toward it, so the page can sum them from any snapshot."""
    return [_target_out(t) for t in db.query(StatsTarget).order_by(StatsTarget.due, StatsTarget.id)]


@router.post("/stats/targets", status_code=201)
def create_target(body: TargetIn, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    _check_target(body)
    t = StatsTarget(**body.model_dump(), created_by=idinfo.get("email"), created_at=datetime.now())
    db.add(t)
    db.commit()
    return _target_out(t)


@router.put("/stats/targets/{target_id}")
def update_target(target_id: int, body: TargetIn, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    t = db.get(StatsTarget, target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Target not found")
    _check_target(body)
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    db.commit()
    return _target_out(t)


@router.delete("/stats/targets/{target_id}", status_code=204)
def delete_target(target_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    t = db.get(StatsTarget, target_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Target not found")
    db.delete(t)
    db.commit()


# ── training progress: Blue Flying, classification exams, time served ─────────

# Where a cadet is with Blue Flying (core.qualifications.flying_blue_stages):
# done, missing PTT and/or a flight, or not started (no Blue ATP ground school,
# which comes with First Class training).
BLUE_FLYING_STATES = ["done", "needs_ptt", "needs_flight", "needs_ptt_and_flight", "not_started"]

# Who still needs each classification exam: Leading subjects are what First
# Class cadets are working on, Senior/Master subjects what Leading and Senior
# cadets are working on.
EXAM_COHORTS = {
    LEADING: ("First Class Cadet",),
    SENIOR_MASTER: ("Leading Cadet", "Senior Cadet"),
}
EXAM_LESSON_KEYS = {lesson.key for lesson in THEORY_LESSONS if lesson.category in EXAM_COHORTS}

# Time at 317 from the join date: (label, months from, months to).
SERVICE_BAND_MONTHS = [
    ("Under 6 months", 0, 6),
    ("6–12 months", 6, 12),
    ("1–2 years", 12, 24),
    ("2–3 years", 24, 36),
    ("3+ years", 36, None),
]
SERVICE_UNKNOWN = "Not synced"
SERVICE_BANDS = [label for label, _, _ in SERVICE_BAND_MONTHS] + [SERVICE_UNKNOWN]


def _load_progress(db: Session, flight: str | None = None, exclude_juniors: bool = False) -> list[Cadet]:
    cadets = (
        db.query(Cadet)
        .options(selectinload(Cadet.qualifications), selectinload(Cadet.flights),
                 selectinload(Cadet.theory_progress))
        .all()
    )
    return [c for c in cadets
            if (flight is None or (c.flight or "Unknown") == flight) and not (exclude_juniors and is_junior(c))]


def _blue_flying_state(c: Cadet) -> str:
    names = qual_names_with_flights([q.qual_type for q in c.qualifications], c.flights)
    # Held outright — the flying record, or Bader, says so (Bronze and up count too).
    if held_level(BADGE_TYPE_BY_KEY["flying"], names):
        return "done"
    ground, ptt, flown = (s["done"] for s in flying_blue_stages(names, c.flights))
    if not ground:
        return "not_started"
    if ptt and not flown:
        return "needs_flight"
    if flown and not ptt:
        return "needs_ptt"
    return "needs_ptt_and_flight"


def _service_band(joined: date_type | None, today: date_type) -> str:
    if joined is None:
        return SERVICE_UNKNOWN
    months = (today.year - joined.year) * 12 + today.month - joined.month - (today.day < joined.day)
    for label, lo, hi in SERVICE_BAND_MONTHS:
        if months >= lo and (hi is None or months < hi):
            return label
    return SERVICE_BAND_MONTHS[0][0]  # a join date in the future reads as just joined


@router.get("/stats/progress")
def get_training_progress(
    flight: str | None = None,
    exclude_juniors: bool = False,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """Live training progress from the Volunteer Portal data: where cadets are
    with Blue Flying, who has flown in the last year, how many of the cadets
    who need each classification exam have passed it, and time served at 317.
    Each count has a matching /stats/cadets filter for the drill-down."""
    today = date_type.today()
    year_ago = today - timedelta(days=365)
    cadets = _load_progress(db, flight, exclude_juniors)

    blue = dict.fromkeys(BLUE_FLYING_STATES, 0)
    service = dict.fromkeys(SERVICE_BANDS, 0)
    flown = 0
    for c in cadets:
        blue[_blue_flying_state(c)] += 1
        service[_service_band(c.joined_on, today)] += 1
        flown += any(f.activity != "simulator" and f.date >= year_ago for f in c.flights)

    exams = []
    for lesson in THEORY_LESSONS:
        cohort = EXAM_COHORTS.get(lesson.category)
        if cohort is None:
            continue
        needing = [c for c in cadets if c.classification in cohort]
        exams.append({
            "key": lesson.key,
            "name": lesson.name,
            "category": lesson.category,
            "cadets": len(needing),
            "passed": sum(1 for c in needing if any(t.lesson_key == lesson.key for t in c.theory_progress)),
        })

    return {
        "total": len(cadets),
        "flown_last_year": flown,
        "blue_flying": blue,
        "exams": exams,
        "service": service,
    }
