"""Turns the raw Volunteer Portal JSON in Cadet_Portal_Data into tidy records.

The portal's shapes are undocumented, so every parser here is defensive: an
unexpected shape gives an empty result, never an exception, and field names
follow what the portal returned in real syncs (exams, learning, unit history)
or what the RAFAC Dashy extension reads (WHTs, shooting log, fieldcraft,
flying). A dropped field shows up as a blank cell, not a broken page.
"""

from __future__ import annotations

from datetime import date, datetime

from core.theory_lessons import THEORY_LESSON_BY_KEY

# The portal writes "no date" as .NET's DateTime.MinValue.
_NO_DATE = "0001-01-01"


def _day(value) -> str | None:
    """An ISO date (YYYY-MM-DD) from a portal timestamp, or None."""
    if not isinstance(value, str) or not value.strip() or value.startswith(_NO_DATE):
        return None
    return value.strip()[:10]


def _text(row: dict, *keys) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _rows(value, *envelopes) -> list[dict]:
    """The list of dict rows, whether bare or wrapped in one of `envelopes`."""
    if isinstance(value, dict):
        value = next((value[k] for k in envelopes if isinstance(value.get(k), list)), [])
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def whts(raw) -> list[dict]:
    return [
        {"weapon": _text(r, "weaponCategory"), "status": _text(r, "status") or "Unknown",
         "expires": _day(r.get("whtDateExpires"))}
        for r in _rows(raw, "whTs", "whts")
        if _text(r, "weaponCategory")
    ]


def shooting_log(raw) -> list[dict]:
    out = [
        {"weapon": _text(r, "weaponCategory", "WeaponCategory", "Weapon"),
         "practice": _text(r, "shootingPractice", "ShootingPractice", "Practice"),
         "outcome": _text(r, "shootingRecordOutcomeType", "Outcome"),
         "date": _day(_text(r, "rangeDate", "RangeDate", "Date")),
         "score": _text(r, "actualScore", "ActualScore"),
         "max_score": _text(r, "highestPossibleScore", "HighestPossibleScore")}
        for r in _rows(raw, "shootingLog", "ShootingLog")
    ]
    return sorted((r for r in out if r["weapon"]), key=lambda r: r["date"] or "", reverse=True)


FIELDCRAFT_LEVELS = ("Blue", "Bronze", "Silver", "Gold")


def fieldcraft(raw) -> list[dict]:
    out = [
        {"level": _text(r, "badgeLevel").title(), "reference": _text(r, "reference"),
         "title": _text(r, "title"), "date": _day(r.get("completedDate")),
         "delivered_by": _text(r, "deliveredBy")}
        for r in _rows(raw, "completions")
        if r.get("lessonId") is not None
    ]
    return sorted(out, key=lambda r: r["date"] or "")


CLASSIFICATION_STAGES = (
    ("First Class Cadet", "firstClassPart3Passed"),
    ("Leading Cadet", "leadingCadetPassed"),
    ("Senior Cadet", "seniorCadetPassed"),
    ("Master Air Cadet", "masterAirCadetPassed"),
)


def classification(raw) -> list[dict]:
    """The date each classification was passed, in ladder order (None = not yet)."""
    raw = raw if isinstance(raw, dict) else {}
    return [{"name": name, "date": _day(raw.get(key))} for name, key in CLASSIFICATION_STAGES]


def _exam_status(code) -> str:
    return {2: "completed", 1: "in_progress"}.get(code, "unknown")


def exams(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    enrolments = [
        {"course": _text(r, "courseName"), "classification": _text(r, "cadetClassification"),
         "enrolled_on": _day(r.get("dateEnrolled"))}
        for r in _rows(raw.get("enrolments"))
        if r.get("enrolled")
    ]
    results = [
        {"course": _text(r, "courseName"), "classification": _text(r, "cadetClassification"),
         "status": _exam_status(r.get("status")), "date": _day(r.get("updatedDate"))}
        for r in _rows(raw.get("results"))
    ]
    return {"enrolments": enrolments, "results": results}


def flying(raw) -> list[dict]:
    """Sorties, newest first — the portal splits them into powered and gliding groups."""
    raw = raw if isinstance(raw, dict) else {}
    out = []
    for activity in ("powered", "gliding"):
        for group in _rows(raw.get(activity)):
            for e in _rows(group.get("entries")):
                if not _day(_text(e, "date")):
                    continue
                powered = e.get("isPowered")
                minutes = e.get("durationMinutes")
                out.append({
                    "date": _day(_text(e, "date")),
                    "activity": activity if not isinstance(powered, bool) else ("powered" if powered else "gliding"),
                    "aircraft": _text(e, "type"),
                    "sortie": _text(e, "sortie"),
                    "unit": _text(e, "flyingUnit"),
                    "minutes": round(minutes) if isinstance(minutes, (int, float)) and minutes >= 0 else None,
                })
    return sorted(out, key=lambda r: r["date"], reverse=True)


def learning(raw) -> list[dict]:
    out = [
        {"title": _text(r, "title"), "complete": r.get("satisfied") is True,
         "date": _day(r.get("dateLastAccessed")), "platform": _text(r, "learningPlatform")}
        for r in _rows(raw)
        if _text(r, "title")
    ]
    return sorted(out, key=lambda r: r["date"] or "", reverse=True)


def unit_history(raw) -> list[dict]:
    out = [
        {"unit": _text(r, "unitName"), "start": _day(r.get("startDate")), "end": _day(r.get("endDate")),
         "primary": r.get("isPrimaryUnit") is True}
        for r in _rows(raw)
        if _text(r, "unitName")
    ]
    return sorted(out, key=lambda r: r["start"] or "")


PARSERS = {
    "whts": whts, "shooting_log": shooting_log, "fieldcraft": fieldcraft,
    "classification": classification, "exams": exams, "flying": flying,
    "learning": learning, "unit_history": unit_history,
}


def parse_all(rows) -> dict:
    """Every data set for one cadet, parsed; missing ones come back as None so
    the UI can say "not synced" rather than "none"."""
    by_name = {r.dataset: r for r in rows}
    out = {name: (parse(by_name[name].data) if name in by_name else None) for name, parse in PARSERS.items()}
    synced = [r.synced_at for r in rows]
    out["synced_at"] = max(synced).isoformat() if synced else None
    return out


# ── Summaries for the squadron dashboard ──────────────────────────────────────

EXPIRY_WARNING_DAYS = 90


def wht_summary(records: list[dict], today: date) -> dict:
    """Per weapon: "current", "expiring" (within EXPIRY_WARNING_DAYS) or "expired"."""
    out = {}
    for r in records:
        if not r["expires"]:
            state = "current" if r["status"].lower() in ("pass", "passed", "current", "valid") else "expired"
        else:
            days = (date.fromisoformat(r["expires"]) - today).days
            state = "expired" if days < 0 else "expiring" if days <= EXPIRY_WARNING_DAYS else "current"
        # Keep the best result if a weapon appears twice (a retest).
        rank = {"expired": 0, "expiring": 1, "current": 2}
        prev = out.get(r["weapon"])
        if prev is None or rank[state] > rank[prev["state"]]:
            out[r["weapon"]] = {"state": state, "expires": r["expires"]}
    return out


# ── Exam results → theory lessons ─────────────────────────────────────────────
# Learn course names → our ACP theory lesson keys. Most specific phrase first,
# since "navigation" alone appears in three exams.
EXAM_LESSON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("pilot navigation", "acp_32_4"),
    ("air navigation", "acp_32_3"),
    ("navigation on land", "acp_32_2"),
    ("basic navigation", "acp_32_2"),
    ("principles of flight", "acp_33_2"),
    ("propulsion", "acp_33_3"),
    ("airframe", "acp_33_4"),
    ("aircraft handling", "acp_34_3"),
    ("operational flying", "acp_34_4"),
    ("operation flying", "acp_34_4"),
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
    """{lesson_key: completed_at} for each passed portal exam we can map."""
    out: dict[str, datetime] = {}
    for r in exams(raw_exams)["results"]:
        key = exam_lesson_key(r["course"])
        if r["status"] == "completed" and key in THEORY_LESSON_BY_KEY and r["date"]:
            when = datetime.fromisoformat(r["date"])
            out[key] = min(out.get(key, when), when)
    return out
