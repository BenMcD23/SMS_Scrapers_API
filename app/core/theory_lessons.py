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


# ─── The catalog ──────────────────────────────────────────────────────────────
# Listed in display order; the frontend groups by category keeping this order.

THEORY_LESSONS: tuple[TheoryLesson, ...] = (
    # Badge theory
    TheoryLesson("blue_radio",              "Blue Radio",                     BADGES),
    TheoryLesson("blue_leadership",         "Blue Leadership",                BADGES),
    TheoryLesson("blue_first_aid",          "Blue First Aid",                 BADGES),
    TheoryLesson("moi_presentation_skills", "MOI Presentation Skills",        BADGES),

    # Classification exams — Leading Cadet
    TheoryLesson("acp_32_2", "ACP 32-2 - Basic Navigation",             LEADING),
    TheoryLesson("acp_33_2", "ACP 33-2 - Principles of Flight (POF)",   LEADING),
    TheoryLesson("acp_34_2", "ACP 34-2 - Airmanship 2",                 LEADING),

    # Classification exams — Senior / Master Cadet
    TheoryLesson("acp_32_3", "ACP 32-3 - Air Navigation",               SENIOR_MASTER),
    TheoryLesson("acp_32_4", "ACP 32-4 - Pilot Navigation",             SENIOR_MASTER),
    TheoryLesson("acp_33_3", "ACP 33-3 - Propulsion",                   SENIOR_MASTER),
    TheoryLesson("acp_33_4", "ACP 33-4 - Airframes",                    SENIOR_MASTER),
    TheoryLesson("acp_34_3", "ACP 34-3 - Aircraft Handling",            SENIOR_MASTER),
    TheoryLesson("acp_34_4", "ACP 34-4 - Operation Flying",             SENIOR_MASTER),
    TheoryLesson("acp_35_3", "ACP 35-3 - Advanced Radio and Radar",     SENIOR_MASTER),
    TheoryLesson("acp_35_4", "ACP 35-4 - Satellite Communication",      SENIOR_MASTER),
)


# ─── Lookups ──────────────────────────────────────────────────────────────────

THEORY_LESSON_BY_KEY: dict[str, TheoryLesson] = {l.key: l for l in THEORY_LESSONS}
