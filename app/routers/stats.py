"""Squadron stats — live numbers plus historical snapshots."""

from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from core import cache
from core.db import get_db
from core.qualifications import BADGE_TYPES, LEVELED, held_level, quali_expiry_cutoff
from core.security import require_staff, require_staff_or_nco
from database.models import Cadet, CadetQualification, CadetSnapshot, StatsSnapshot, StatsTarget

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


def _quals_by_cadet(db: Session) -> dict:
    out: dict = defaultdict(list)
    for q in db.query(CadetQualification).all():
        out[q.cadet_id].append(q.qual_type)
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
    flight: str | None = Query(None, description='Flight letter, or "Unknown" for none'),
    exclude_juniors: bool = False,
    on: date_type | None = Query(None, description="As of this day, from the per-cadet snapshots"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """The cadets behind a number on the stats page: who holds a badge at a
    level, who is at a classification. Live by default; `on` a past day reads
    the last per-cadet snapshot taken by then."""
    if (badge is None) != (level is None):
        raise HTTPException(status_code=400, detail="badge and level go together")
    if badge is not None and badge not in STAT_BADGE_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown badge {badge!r}")
    if level is not None and level not in LEVEL_LABELS:
        raise HTTPException(status_code=400, detail=f"Unknown level {level!r}")

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
        rows = [
            {**_cadet_ref(c), "rank": c.rank or "", "classification": c.classification or JUNIOR_CLASSIFICATION,
             "badges": _held_levels(quals.get(c.cin, []))}
            for c in db.query(Cadet).all()
        ]
        as_of = None

    def keep(r: dict) -> bool:
        if flight is not None and r["flight"] != flight:
            return False
        if exclude_juniors and r["junior"]:
            return False
        if classification is not None and r["classification"] != classification:
            return False
        if badge is not None and r["badges"].get(badge, "None") != level:
            return False
        return True

    cadets = sorted((r for r in rows if keep(r)), key=lambda r: r["name"].casefold())
    for r in cadets:
        r["level"] = r["badges"].get(badge, "None") if badge else None
        del r["badges"]
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
    typical time between steps where the per-cadet history saw it happen.

    A step is only timed for a cadet seen moving into it *and* seen moving
    into the one before: someone already Leading when tracking began says
    nothing about how long Leading takes. Weekly snapshots make it accurate
    to about a week."""
    cadets = [c for c in db.query(Cadet).all() if flight is None or (c.flight or "Unknown") == flight]
    reached = [sum(1 for c in cadets if _step(c.classification) >= i) for i in range(len(CLASSIFICATION_STEPS))]

    timelines, _ = _cadet_timelines(db)
    durations: dict = defaultdict(list)
    for points in timelines.values():
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
