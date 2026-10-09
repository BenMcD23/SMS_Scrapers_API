"""Turns the raw Volunteer Portal JSON in Cadet_Portal_Data into the records the
squadron's normal tables hold: flights, join date, classification dates and
the classification exams that tick theory lessons.

The portal's shapes are undocumented, so every parser here is defensive: an
unexpected shape gives an empty result, never an exception. Field names follow
what the portal returned in the first real syncs.
"""

from __future__ import annotations

from datetime import date, datetime

from core.theory_lessons import THEORY_LESSON_BY_KEY

# The portal writes "no date" as .NET's DateTime.MinValue.
_NO_DATE = "0001-01-01"


def _day(value) -> date | None:
    """A date from a portal timestamp ("2025-07-16T00:00:00"), or None."""
    if not isinstance(value, str) or not value.strip() or value.startswith(_NO_DATE):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _text(row: dict, *keys) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _rows(value) -> list[dict]:
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def flights(raw) -> list[dict]:
    """Every flight and simulator session, newest first. The portal groups them
    under "powered" and "gliding", then by category (AEF, VGS, PTT, ...)."""
    raw = raw if isinstance(raw, dict) else {}
    out = []
    for group_key in ("powered", "gliding"):
        for group in _rows(raw.get(group_key)):
            for e in _rows(group.get("entries")):
                when = _day(e.get("date"))
                if not when:
                    continue
                category = _text(e, "aefCategory") or _text(group, "groupName")
                powered = e.get("isPowered")
                activity = (
                    "simulator" if (category or "").upper() == "PTT"
                    else ("powered" if powered else "gliding") if isinstance(powered, bool)
                    else group_key
                )
                minutes = e.get("durationMinutes")
                out.append({
                    "date": when,
                    "activity": activity,
                    "aircraft": _text(e, "type"),
                    "category": category,
                    "duty": _text(e, "duty"),
                    "sortie": _text(e, "sortie"),
                    "unit": _text(e, "flyingUnit"),
                    # A simulator session is logged with 0 minutes; that means unknown.
                    "minutes": round(minutes) if isinstance(minutes, (int, float)) and minutes > 0 else None,
                })
    return sorted(out, key=lambda f: f["date"], reverse=True)


CLASSIFICATION_STAGES = (
    ("First Class Cadet", "firstClassPart3Passed"),
    ("Leading Cadet", "leadingCadetPassed"),
    ("Senior Cadet", "seniorCadetPassed"),
    ("Master Air Cadet", "masterAirCadetPassed"),
)


def classification_dates(raw) -> dict[str, str]:
    """{classification: ISO date passed} for each one the cadet has passed."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for name, key in CLASSIFICATION_STAGES:
        when = _day(raw.get(key))
        if when:
            out[name] = when.isoformat()
    return out


def joined_on(raw) -> date | None:
    """When they joined their current squadron: the start of the open primary
    unit (falling back to any open unit)."""
    units = [u for u in _rows(raw) if _day(u.get("startDate")) and not _day(u.get("endDate"))]
    primary = [u for u in units if u.get("isPrimaryUnit") is True] or units
    starts = [_day(u.get("startDate")) for u in primary]
    return max(starts) if starts else None


# ── Exam results → theory lessons ─────────────────────────────────────────────
# Portal course names → theory lesson keys (core/theory_lessons). Results are
# named for the exam ("Rocketry Exam", "Leading Cadet/CCF Part 2 Principles of
# Flight Exam"), so match on a distinctive phrase; most specific first, since
# "navigation" alone appears in three subjects.
EXAM_LESSON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("pilot navigation", "acp_32_4"),
    ("air navigation", "acp_32_3"),
    ("navigation on land", "acp_32_2"),
    ("basic navigation", "acp_32_2"),
    ("principles of flight", "acp_33_2"),
    ("piston engine", "acp_33_3"),
    ("jet engine", "jet_engine_propulsion"),
    ("airframe", "acp_33_4"),
    ("aircraft handling", "acp_34_3"),
    ("air power", "air_power"),
    ("military aircraft", "military_aircraft"),
    ("rocketry", "rocketry"),
    ("airmanship", "acp_34_2"),
    ("radio and radar", "acp_35_3"),
    ("satellite", "acp_35_4"),
)


def exam_lesson_key(course: str) -> str | None:
    name = course.lower()
    for phrase, key in EXAM_LESSON_KEYWORDS:
        if phrase in name:
            return key
    return None


def completed_theory(raw_exams) -> dict[str, datetime]:
    """{lesson_key: earliest pass} for each passed portal exam we can map.
    Result status 2 is a pass, 1 is in progress (as the RAFAC Dashy extension reads it)."""
    raw = raw_exams if isinstance(raw_exams, dict) else {}
    out: dict[str, datetime] = {}
    for r in _rows(raw.get("results")):
        key = exam_lesson_key(_text(r, "courseName") or "")
        when = _day(r.get("updatedDate"))
        if r.get("status") == 2 and key in THEORY_LESSON_BY_KEY and when:
            at = datetime(when.year, when.month, when.day)
            out[key] = min(out.get(key, at), at)
    return out
