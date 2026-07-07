"""Catalog of squadron *theory* lessons.

A "theory lesson" is a chunk of training a cadet can complete the **theory**
element of before the formal assessment / qualification is finished. Tracking
these lets part-finished progress be visible (e.g. a cadet has sat through Blue
Radio theory but hasn't done the radio assessment yet, so it won't appear in
their scraped Bader qualifications).

This is the single source of truth consumed by:

* the **record** view — mark cadets as having completed a lesson's theory, and
* the **progress** view — filter cadets by any set of lessons.

Lessons are grouped by ``category`` so the frontend can lay them out under the
right heading (badge theory, then the classification exams under Leading and
Senior/Master). Order here is the display order.
"""

from __future__ import annotations

from typing import NamedTuple


# ─── Categories ───────────────────────────────────────────────────────────────

BADGES = "Badges"
LEADING = "Leading"
SENIOR_MASTER = "Senior/Master"


class TheoryLesson(NamedTuple):
    key: str        # stable slug used by the API / frontend / DB rows
    name: str       # human-readable label
    category: str   # grouping heading (display order preserved below)
    # The qualification this theory leads to, so the progress view can show
    # whether the cadet has since earned it. Either:
    #   ("badge", badge_key)          — held if held_level(badge) is not None
    #   ("classification", min_name)  — held if classification >= min_name
    qual: tuple[str, str]


# ─── The catalog ──────────────────────────────────────────────────────────────
# Listed in display order; the frontend groups by category keeping this order.

LEADING_CADET = "Leading Cadet"
SENIOR_CADET = "Senior Cadet"

THEORY_LESSONS: tuple[TheoryLesson, ...] = (
    # Badge theory
    TheoryLesson("blue_radio",              "Blue Radio",              BADGES, ("badge", "radio")),
    TheoryLesson("blue_leadership",         "Blue Leadership",         BADGES, ("badge", "leadership")),
    TheoryLesson("blue_first_aid",          "Blue First Aid",          BADGES, ("badge", "first_aid")),
    TheoryLesson("moi_presentation_skills", "MOI Presentation Skills", BADGES, ("badge", "presentation_skills")),

    # Classification exams — Leading Cadet
    TheoryLesson("acp_32_2", "ACP 32-2 - Basic Navigation",           LEADING, ("classification", LEADING_CADET)),
    TheoryLesson("acp_33_2", "ACP 33-2 - Principles of Flight (POF)", LEADING, ("classification", LEADING_CADET)),
    TheoryLesson("acp_34_2", "ACP 34-2 - Airmanship 2",               LEADING, ("classification", LEADING_CADET)),

    # Classification exams — Senior / Master Cadet
    TheoryLesson("acp_32_3", "ACP 32-3 - Air Navigation",           SENIOR_MASTER, ("classification", SENIOR_CADET)),
    TheoryLesson("acp_32_4", "ACP 32-4 - Pilot Navigation",         SENIOR_MASTER, ("classification", SENIOR_CADET)),
    TheoryLesson("acp_33_3", "ACP 33-3 - Propulsion",               SENIOR_MASTER, ("classification", SENIOR_CADET)),
    TheoryLesson("acp_33_4", "ACP 33-4 - Airframes",                SENIOR_MASTER, ("classification", SENIOR_CADET)),
    TheoryLesson("acp_34_3", "ACP 34-3 - Aircraft Handling",        SENIOR_MASTER, ("classification", SENIOR_CADET)),
    TheoryLesson("acp_34_4", "ACP 34-4 - Operation Flying",         SENIOR_MASTER, ("classification", SENIOR_CADET)),
    TheoryLesson("acp_35_3", "ACP 35-3 - Advanced Radio and Radar", SENIOR_MASTER, ("classification", SENIOR_CADET)),
    TheoryLesson("acp_35_4", "ACP 35-4 - Satellite Communication",  SENIOR_MASTER, ("classification", SENIOR_CADET)),
)


# ─── Lookups ──────────────────────────────────────────────────────────────────

THEORY_LESSON_BY_KEY: dict[str, TheoryLesson] = {l.key: l for l in THEORY_LESSONS}


# ─── Has the cadet earned the qualification this theory leads to? ──────────────
# Classification ladder, lowest → highest. Rank by index; unknown/None ranks below
# all, so "has this classification or better" is a simple >= on the index.
CLASSIFICATION_ORDER: tuple[str, ...] = (
    "Second Class Cadet",
    "First Class Cadet",
    "Leading Cadet",
    "Senior Cadet",
    "Master Air Cadet",
)


def _class_rank(name: str | None) -> int:
    try:
        return CLASSIFICATION_ORDER.index(name)
    except ValueError:
        return -1


def lesson_qual_held(lesson: TheoryLesson, qual_names, classification: str | None) -> bool:
    """True if the cadet has earned the qualification ``lesson`` leads to.

    ``qual_names`` is the cadet's raw ``qual_type`` strings; ``classification``
    their highest classification. Imports from ``core.qualifications`` are local
    to avoid a circular import at module load."""
    kind, target = lesson.qual
    if kind == "badge":
        from core.qualifications import BADGE_TYPE_BY_KEY, held_level
        badge = BADGE_TYPE_BY_KEY.get(target)
        return bool(badge and held_level(badge, qual_names))
    if kind == "classification":
        return _class_rank(classification) >= _class_rank(target)
    return False
