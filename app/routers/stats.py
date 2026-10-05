"""Squadron stats — live numbers plus historical snapshots."""

from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core import cache
from core.db import get_db
from core.qualifications import BADGE_TYPES, LEVELED, held_level
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

    # The same breakdown without juniors, who've barely started on badges and
    # drag every percentage down. Counted here so the dashboards can toggle it
    # without another round-trip, and so snapshots carry it for the trend charts.
    non_juniors = [c for c in cadets if not is_junior(c)]

    return {
        "total_cadets": len(cadets),
        "by_flight": flight_counts,
        "by_age": age_counts,
        "by_rank": rank_counts,
        "by_classification": classification_counts,
        "badges": _badge_counts(cadets, quals_by_cadet),
        "non_junior": {
            "total_cadets": len(non_juniors),
            "badges": _badge_counts(non_juniors, quals_by_cadet),
        },
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


@router.get("/stats/history")
def get_stats_history(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    one_year_ago = datetime.now() - timedelta(days=365)
    snapshots = (
        db.query(StatsSnapshot)
        .filter(StatsSnapshot.captured_at >= one_year_ago)
        .order_by(StatsSnapshot.captured_at.asc())
        .all()
    )
    return [{"date": s.captured_at.isoformat(), "data": s.data} for s in snapshots]


@router.post("/stats/snapshot")
def create_stats_snapshot(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    snapshot = StatsSnapshot(captured_at=datetime.now(), data=compute_stats(db))
    db.add(snapshot)
    db.commit()
    return {"status": "ok", "captured_at": snapshot.captured_at.isoformat()}
