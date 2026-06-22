"""Squadron stats — live numbers plus historical snapshots."""

from datetime import datetime, timedelta, date as date_type

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.models import Cadet, CadetQualification, StatsSnapshot

from core import cache
from core.db import get_db
from core.security import require_staff, require_staff_or_nco

router = APIRouter()

# Cache keys shared with writers that invalidate cadet-derived data.
STATS_CACHE_KEY = "stats:current"
STATS_CACHE_TTL = 120

BADGE_TYPES = [
    "duke_of_edinburgh", "first_aid", "leadership", "cyber", "radio",
    "road_marching", "space", "music", "flying_badge", "fieldcraft",
    "shooting", "swimming_proficiency",
]

# Maps raw SMS qual names → (badge_category, level)
QUAL_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    # Duke of Edinburgh
    "Blue Pre-Duke of Edinburgh Award":     ("duke_of_edinburgh", "Blue"),
    "Bronze Duke of Edinburgh Award":       ("duke_of_edinburgh", "Bronze"),
    "Silver Duke of Edinburgh Award":       ("duke_of_edinburgh", "Silver"),
    # First Aid
    "St John Youth First Aid":              ("first_aid", "Blue"),
    "St John Essential First Aid":          ("first_aid", "Blue"),
    "St John Activity First Aid":           ("first_aid", "Bronze"),
    "Cadet First Aid Instructor Award":     ("first_aid", "Gold"),
    "St John Activity First Aid Assessor":  ("first_aid", "Gold"),
    # Leadership
    "Blue Air Cadet Foundation Leadership":   ("leadership", "Blue"),
    "Bronze Air Cadet Foundation Leadership": ("leadership", "Bronze"),
    "Bronze Leadership":                      ("leadership", "Bronze"),
    "Silver Air Cadet Foundation Leadership": ("leadership", "Silver"),
    "Silver Leadership":                      ("leadership", "Silver"),
    "Gold Leadership":                        ("leadership", "Gold"),
    # Cyber
    "RAFAC Bronze Cyber Course":              ("cyber", "Bronze"),
    "Cyber - Bronze Award":                   ("cyber", "Bronze"),
    "CyberFirst Adventurer":                  ("cyber", "Bronze"),
    "Cyber - Silver Award":                   ("cyber", "Silver"),
    # Radio
    "Radio - Basic Operator (Blue)":          ("radio", "Blue"),
    "Radio - Operator (Bronze)":              ("radio", "Bronze"),
    "Radio - Advanced Voice Procedure (Silver)": ("radio", "Silver"),
    # Road Marching
    "Blue Road Marching":                     ("road_marching", "Blue"),
    "Bronze Road Marching":                   ("road_marching", "Bronze"),
    # Space
    "Blue Space Studies":                     ("space", "Blue"),
    "Bronze Space Studies":                   ("space", "Bronze"),
    # Music
    "Musician (Blue) - Drum":                 ("music", "Blue"),
    "Musician (Blue) - Lyre":                 ("music", "Blue"),
    "Wing Musician (Bronze) - Drums":         ("music", "Bronze"),
    "Wing Musician (Bronze) - Lyre":          ("music", "Bronze"),
    "Regional Musician (Silver) - Drums":     ("music", "Silver"),
    "Regional Musician (Silver) - Lyre":      ("music", "Silver"),
    "National Musician (Gold) - Lyre":        ("music", "Gold"),
    # Flying Badge
    "PTT Blue":                               ("flying_badge", "Blue"),
    "Blue ATP Ground School":                 ("flying_badge", "Blue"),
    "Aviation FAM PTT":                       ("flying_badge", "Blue"),
    "RAFAC Aviation Training Package Blue Training Badge":   ("flying_badge", "Blue"),
    "Bronze ATP Ground School":               ("flying_badge", "Bronze"),
    "RAFAC Aviation Training Package Bronze Training Badge": ("flying_badge", "Bronze"),
    # Fieldcraft
    "Blue Fieldcraft Skills":                 ("fieldcraft", "Blue"),
    "Bronze Fieldcraft Skills":               ("fieldcraft", "Bronze"),
    # Shooting
    "Blue Shot (Air Rifle)":                  ("shooting", "Blue"),
    "Blue Shot (L98A2)":                      ("shooting", "Blue"),
    "Blue Shot (Small Bore)":                 ("shooting", "Blue"),
    "Blue Shot (Target Rifle)":               ("shooting", "Blue"),
    "Bronze Shot (Air Rifle)":                ("shooting", "Bronze"),
    "Bronze Shot (L98A2)":                    ("shooting", "Bronze"),
    "Bronze Shot (Small Bore)":               ("shooting", "Bronze"),
    "Bronze Shot (Target Rifle)":             ("shooting", "Bronze"),
    "Silver Shot (Air Rifle)":                ("shooting", "Silver"),
    "Gold Shot (Air Rifle)":                  ("shooting", "Gold"),
    # Swimming
    "Basic Swimming Competence":              ("swimming_proficiency", "Basic"),
    "Intermediate Swimming Competence":       ("swimming_proficiency", "Intermediate"),
}

# Higher index = higher level; used to pick the best level per cadet per badge
LEVEL_RANK = {
    "None": 0, "Blue": 1, "Basic": 1, "Bronze": 2, "Intermediate": 2,
    "Silver": 3, "Advanced": 3, "Gold": 4, "Nijmegen": 5,
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
        # No classification recorded means they haven't passed First Class yet.
        classification = c.classification or "Junior Cadet"
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
        if c.date_of_birth:
            dob = c.date_of_birth
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            age_counts[str(age)] = age_counts.get(str(age), 0) + 1

    # Badge breakdown — map raw qual names → (category, level), pick highest per cadet
    best_level: dict = {}
    for q in db.query(CadetQualification).all():
        mapping = QUAL_CATEGORY_MAP.get(q.qual_type)
        if not mapping:
            continue
        category, level = mapping
        key = (q.cadet_id, category)
        current = best_level.get(key, "None")
        if LEVEL_RANK.get(level, 0) > LEVEL_RANK.get(current, 0):
            best_level[key] = level

    badges: dict = {}
    for badge in BADGE_TYPES:
        level_counts: dict = {}
        for c in cadets:
            level = best_level.get((c.cin, badge), "None")
            level_counts[level] = level_counts.get(level, 0) + 1
        badges[badge] = level_counts

    return {
        "total_cadets": len(cadets),
        "by_flight": flight_counts,
        "by_age": age_counts,
        "by_rank": rank_counts,
        "by_classification": classification_counts,
        "badges": badges,
    }


@router.get("/stats/current")
async def get_current_stats(
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
async def get_stats_history(
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
async def create_stats_snapshot(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    snapshot = StatsSnapshot(captured_at=datetime.now(), data=compute_stats(db))
    db.add(snapshot)
    db.commit()
    return {"status": "ok", "captured_at": snapshot.captured_at.isoformat()}
