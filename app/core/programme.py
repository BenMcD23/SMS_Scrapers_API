"""The parade-night programme as a record: what each flight did on each night,
so it can be set against who was there.

The monthly programme doc (texts/programme_parser.py reads it) is a table of
cells like "Map Reading 2 / Sgt Davies": an activity and who ran it, with
merged cells for both flights or the whole squadron, and "A / B" for a flight
split between two things. Importing a month turns each cell into rows of
ProgrammeSession: one per flight per activity, with a tidy name, a category
(drill, classifications, sport...) and the instructor.

The tidying is done by the shared model chain, which handles the doc's
loose wording far better than rules ("Classifications (AIR) / Senior/Master"
is one activity, "Volleyball / Band" is two). A cell seen before reuses its
earlier result, so a re-import costs nothing and names stay stable, and when
the models are down the rules below do it instead: an import never fails for
want of an LLM.

Stats join sessions to Bader attendance by date and the cadet's current flight
(see programme_stats): turnout by kind of night, by activity, and per night.
"""

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from core.attendance import ABSENT, AUTHORISED, PRESENT, attendance_state
from core.llm import generate
from database.models import Cadet, CadetAttendance, ProgrammeNight, ProgrammeSession

logger = logging.getLogger(__name__)

# Shown in this order. Each key is stored on the session row, so a key never
# changes; a label can.
CATEGORIES: dict[str, str] = {
    "drill": "Drill and inspection",
    "classifications": "Classifications",
    "lessons": "Training lessons",
    "fieldcraft": "Fieldcraft and adventure",
    "first_aid": "First aid",
    "stem": "STEM and aviation",
    "sport": "Sport and fitness",
    "dofe": "Duke of Edinburgh",
    "music": "Band",
    "leadership": "Leadership and NCOs",
    "admin": "Admin and flight time",
    "social": "Social and events",
    "other": "Other",
}

# First match wins, so the specific come before the general ("drill" is in
# "Blue Drill Training" and "Remembrance Parade Practice" is drill too).
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("classifications", ("classification", "moi assessment")),
    ("first_aid", ("first aid", "casualty", "heartstart", "climatic injur")),
    ("dofe", ("dofe", "duke of edinburgh")),
    ("music", ("band",)),
    ("drill", ("drill", "inspection", "passout", "pass out", "remembrance", "parade practice")),
    ("fieldcraft", ("fieldcraft", "iet", "tent", "survival", "bivvi", "bivvy", "road march", "expedition",
                    "map reading", "navigation", "kit check")),
    ("stem", ("stem", "flight sim", "simulator", "synthetic", "cyber", "space", "radio", "comms", "drone",
              "rocket", "atp")),
    ("sport", ("sport", "volleyball", "football", "frisbee", "athletic", "physical", "fitness", "games",
               "dodgeball", "netball", "rounders")),
    ("leadership", ("nco", "jnco", "snco", "leadership", "cadets own", "assessed session", "moi", "resilience", "back to basics")),
    ("lessons", ("history", "rank", "security", "flying ops", "principles of flight", "airmanship",
                 "current affairs", "lesson", "lo1", "lo2", "lo3", "lo4", "lo5", "lo6", "geopolitic")),
    ("admin", ("flight time", "uniform", "admin", "measurement", "brassard", "hig", "training plan",
               "welcome", "prep", "issue", "accoutrement", "greens")),
    ("social", ("halloween", "christmas", "social", "extravaganza", "quiz", "party", "fancy dress",
                "games night", "food", "cook off", "tribunal", "community")),
]

# Words that start an instructor line rather than continue an activity's name.
_PEOPLE = re.compile(
    r"^(cpls?|sgts?|asgt|fs|cwo|wo|fg\s*off|flt\s*lt|plt\s*off|sqn\s*ldr|flt\s*sgt|ci|cdt|cfav|sncos?|jncos?|ncos?|"
    r"flight\s+(commander|ncos)|moi|all|gold|staff|oc)\b",
    re.IGNORECASE,
)
# "LO2 History of the RAF": the syllabus code tells staff nothing a name doesn't.
_LO_PREFIX = re.compile(r"^LO\s*\d+\s*[-:]?\s*", re.IGNORECASE)
_SPLIT = re.compile(r"\s+/\s+")


@dataclass(frozen=True)
class Activity:
    name: str
    category: str
    instructor: str | None


# Each keyword must start a word, so "iet" (IET) doesn't match "variety".
_RULE_RES = [(category, re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + ")", re.IGNORECASE))
             for category, words in _RULES]


def category_for(name: str) -> str:
    for category, pattern in _RULE_RES:
        if pattern.search(name):
            return category
    return "other"


def _tidy(name: str) -> str:
    name = _LO_PREFIX.sub("", " ".join(name.split()))
    return name[:1].upper() + name[1:] if name else name


def activities_by_rules(text: str) -> list[Activity]:
    """A cell as activities without a model: the first line(s) are the
    activity until a line that starts like a person; "A / B" with matching
    "X / Y" instructors is two activities."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    name_lines, people_lines = [lines[0]], []
    for line in lines[1:]:
        (people_lines if people_lines or _PEOPLE.match(line) else name_lines).append(line)
    name = " ".join(name_lines)
    people = " ".join(people_lines) or None

    names = _SPLIT.split(name)
    if len(names) == 1:
        tidy = _tidy(name)
        return [Activity(tidy, category_for(tidy), people)]
    instructors = _SPLIT.split(people) if people else []
    paired = len(instructors) == len(names)
    return [
        Activity(_tidy(n), category_for(n), instructors[i] if paired else people)
        for i, n in enumerate(names)
    ]


AI_SYSTEM_PROMPT = f"""You tidy an RAF Air Cadet squadron's parade-night programme into data.
Each input cell is one table cell from the programme: an activity name, usually followed by who runs it.
For each cell return its activities. A cell is two or more activities only when it lists separate
activities happening side by side, written "A / B" (e.g. "Volleyball / Band", "Drill / Uniform Measurement").
Keep one activity when the slash is part of one thing (e.g. "Classifications (Leading/Senior/Master)",
"Classifications (AIR) / Senior/Master" is one classifications session).
For each activity give:
- name: short and tidy, in title case as staff would say it (drop syllabus codes like "LO2"; keep part
  numbers like "Map Reading 2"; fix obvious typos).
- category: exactly one of: {", ".join(CATEGORIES)}.
  drill = drill, inspections, parade practice; lessons = classroom lessons (history, rank system, flying ops,
  security); fieldcraft = fieldcraft, IET, expedition skills, map reading; stem = STEM, flight sims, radio,
  cyber, space; leadership = NCO sessions, cadets' own and assessed sessions, MOI; admin = flight time,
  uniform, kit, admin, briefings; social = socials, parties, games nights, quizzes; other = anything else.
- instructor: who runs it, as written (ranks and names), or null if the cell doesn't say.
Reply with JSON only: {{"cells": [{{"i": <input index>, "activities": [{{"name": ..., "category": ...,
"instructor": ...}}]}}]}}"""


def activities_by_ai(texts: list[str]) -> dict[int, list[Activity]]:
    """{index: activities} for the cells the model handled properly. Any cell
    it skipped or got wrong is left out, for the rules to do."""
    if not texts:
        return {}
    prompt = "Cells:\n" + json.dumps([{"i": i, "cell": t} for i, t in enumerate(texts)], ensure_ascii=False)
    try:
        reply, _model = generate(prompt, AI_SYSTEM_PROMPT, temperature=0.1, max_tokens=12000,
                                 groq_max_tokens=8000)
    except RuntimeError as e:
        logger.warning(f"programme tidy: every model failed, using rules: {e}")
        return {}
    start, end = reply.find("{"), reply.rfind("}")
    try:
        cells = json.loads(reply[start:end + 1])["cells"]
    except (ValueError, KeyError, TypeError):
        logger.warning("programme tidy: unreadable reply, using rules")
        return {}

    out: dict[int, list[Activity]] = {}
    for cell in cells if isinstance(cells, list) else []:
        try:
            i = int(cell["i"])
            acts = [
                Activity(name=str(a["name"]).strip()[:120], category=a["category"],
                         instructor=(str(a["instructor"]).strip()[:200] or None) if a.get("instructor") else None)
                for a in cell["activities"]
            ]
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(texts) and acts and all(a.name and a.category in CATEGORIES for a in acts):
            out[i] = acts
    return out


def tidy_cells(db: Session, texts: list[str], use_ai: bool = True) -> dict[str, list[Activity]]:
    """{cell text: activities} for every distinct cell. A cell already stored
    keeps what it was given then, so names don't drift between imports; new
    ones go to the model in one call, and the rules cover whatever it misses."""
    distinct = list(dict.fromkeys(t for t in texts if t.strip()))
    known: dict[str, list[Activity]] = {}
    if distinct:
        rows = (db.query(ProgrammeSession).filter(ProgrammeSession.raw.in_(distinct))
                .order_by(ProgrammeSession.id).all())
        seen: dict[str, dict[str, Activity]] = defaultdict(dict)
        for r in rows:
            # One cell is stored once per flight; keep each activity once, in order.
            seen[r.raw].setdefault(r.activity, Activity(r.activity, r.category, r.instructor))
        known = {raw: list(acts.values()) for raw, acts in seen.items()}

    new = [t for t in distinct if t not in known]
    by_ai = activities_by_ai(new) if use_ai else {}
    for i, text in enumerate(new):
        known[text] = by_ai.get(i) or activities_by_rules(text)
    return known


def import_month(db: Session, month: int, year: int, use_ai: bool = True, nights: list[dict] | None = None) -> dict:
    """Read one month's programme doc and store it, replacing that month's
    nights. `nights` is the parsed doc, for a caller that already has it.

    A doc that parses to no nights changes nothing: an empty read must never
    wipe a month that was imported properly before."""
    if nights is None:
        from texts.programme_parser import parse_programme_sessions
        nights = parse_programme_sessions(month, year)
    if not nights:
        return {"month": f"{year}-{month:02d}", "nights": 0, "sessions": 0, "skipped": "no nights in the doc"}

    tidy = tidy_cells(db, [s["text"] for n in nights for s in n["sessions"]], use_ai=use_ai)
    first = date(year, month, 1)
    after = date(year + month // 12, month % 12 + 1, 1)
    # Through the ORM so the sessions go with their nights on every database,
    # and flushed before the new rows so the dates' unique index never sees both.
    for old in db.query(ProgrammeNight).filter(ProgrammeNight.date >= first, ProgrammeNight.date < after):
        db.delete(old)
    db.flush()

    now = datetime.now()
    sessions = 0
    for n in nights:
        night = ProgrammeNight(date=n["date"].date() if isinstance(n["date"], datetime) else n["date"],
                               uniform=n.get("uniform") or None, duty_nco=n.get("dnco") or None,
                               imported_at=now)
        for s in n["sessions"]:
            acts = tidy.get(s["text"], [])
            split = s["split"] or len(acts) > 1
            for act in acts:
                for flight in s["flights"]:
                    night.sessions.append(ProgrammeSession(
                        period=s["period"], flight=flight, activity=act.name, category=act.category,
                        instructor=act.instructor, split=split, raw=s["text"]))
                    sessions += 1
        db.add(night)
    db.commit()
    return {"month": f"{year}-{month:02d}", "nights": len(nights), "sessions": sessions}


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    """(month, year) for every month from start's to end's, inclusive."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((m, y))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def import_months(db: Session, months: list[tuple[int, int]], use_ai: bool = True) -> list[dict]:
    """Import several months, carrying on past one that fails (a month with no
    doc, or a doc in an older layout) and saying why it was skipped."""
    results = []
    for month, year in months:
        try:
            results.append(import_month(db, month, year, use_ai=use_ai))
        except Exception as e:  # one bad month mustn't stop the rest
            db.rollback()
            logger.warning(f"programme import {year}-{month:02d} skipped: {e}")
            results.append({"month": f"{year}-{month:02d}", "nights": 0, "sessions": 0, "skipped": str(e)[:200]})
    return results


# ─── Stats ───────────────────────────────────────────────────────────────────

def _rate(present: int, total: int) -> float | None:
    return round(100 * present / total, 1) if total else None


def programme_stats(db: Session, days: int = 365, today: date | None = None) -> dict:
    """Turnout set against the programme, over the last `days`.

    A cadet counts towards a session when they're in its flight (by their
    flight today: flights rarely change, and the history isn't kept) and
    were on that night's register. Split sessions (a flight divided between
    two things) only count towards the night, never an activity: which half a
    cadet was in isn't recorded. NCO-flight cadets run sessions rather than
    attend them, so they're left out."""
    today = today or date.today()
    since = today - timedelta(days=days)
    nights = (db.query(ProgrammeNight)
              .filter(ProgrammeNight.date >= since, ProgrammeNight.date <= today)
              .order_by(ProgrammeNight.date).all())
    flight_of = {c.cin: (c.flight or "").strip().upper() for c in db.query(Cadet).all()}
    dates = [n.date for n in nights]
    register: dict = defaultdict(dict)  # date -> {cin: state}
    if dates:
        start = datetime.combine(min(dates), datetime.min.time())
        end = datetime.combine(max(dates) + timedelta(days=1), datetime.min.time())
        for r in db.query(CadetAttendance).filter(CadetAttendance.date >= start, CadetAttendance.date < end):
            register[r.date.date()][r.cadet_id] = attendance_state(r.status)

    def counts(day: date, flight: str | None = None) -> tuple[int, int, int]:
        states = [s for cin, s in register.get(day, {}).items()
                  if flight_of.get(cin) in ("A", "B", "C") and (flight is None or flight_of[cin] == flight)]
        return sum(s == PRESENT for s in states), sum(s == AUTHORISED for s in states), len(states)

    by_category: dict = defaultdict(lambda: {"nights": set(), "present": 0, "authorised": 0, "total": 0})
    by_activity: dict = defaultdict(lambda: {"nights": set(), "present": 0, "total": 0, "category": None})
    night_rows = []
    for n in nights:
        present, authorised, total = counts(n.date)
        if total == 0:
            # Planned but not yet registered (or no register that night): a
            # programme without attendance says nothing about turnout.
            continue
        seen = set()
        for s in n.sessions:
            if s.split or (s.flight, s.category, s.activity) in seen:
                continue
            seen.add((s.flight, s.category, s.activity))
            p, a, t = counts(n.date, s.flight)
            cat = by_category[s.category]
            cat["nights"].add(n.date)
            cat["present"] += p
            cat["authorised"] += a
            cat["total"] += t
            act = by_activity[s.activity.casefold()]
            act["name"] = s.activity
            act["category"] = s.category
            act["nights"].add(n.date)
            act["present"] += p
            act["total"] += t
        night_rows.append({
            "date": n.date.isoformat(),
            "rate": _rate(present, total),
            "present": present,
            "total": total,
            "uniform": n.uniform,
            "sessions": _night_summary(n),
        })

    return {
        "days": days,
        "nights": night_rows,
        "categories": [
            {"key": key, "label": CATEGORIES[key], "nights": len(c["nights"]), "rate": _rate(c["present"], c["total"]),
             "authorised_rate": _rate(c["authorised"], c["total"]), "cadet_nights": c["total"]}
            for key in CATEGORIES if (c := by_category.get(key)) and c["total"]
        ],
        # Activities seen at least twice: once is one night, not a pattern.
        "activities": sorted(
            ({"name": a["name"], "category": a["category"], "label": CATEGORIES[a["category"]],
              "times": len(a["nights"]), "rate": _rate(a["present"], a["total"])}
             for a in by_activity.values() if len(a["nights"]) >= 2 and a["total"]),
            key=lambda a: (a["rate"], a["name"]),
        ),
    }


def _night_summary(night: ProgrammeNight) -> list[dict]:
    """What each flight did that night, merged where flights shared an
    activity: [{period, flights: "A, B", activity, category}]."""
    grouped: dict = {}
    for s in sorted(night.sessions, key=lambda s: (s.period, s.id)):
        key = (s.period, s.activity, s.category)
        grouped.setdefault(key, []).append(s.flight)
    order = {"C": 0, "A": 1, "B": 2}
    return [
        {"period": period, "flights": ", ".join(sorted(set(flights), key=lambda f: order.get(f, 9))),
         "activity": activity, "category": category}
        for (period, activity, category), flights in grouped.items()
    ]


def cadet_sessions(db: Session, cin: int, days: int = 365, today: date | None = None) -> dict:
    """One cadet's nights set against the programme: each night they were on
    the register, what their flight did and whether they were there, with
    totals by category so "misses drill nights" shows."""
    today = today or date.today()
    cadet = db.get(Cadet, cin)
    if not cadet:
        return {"error": f"no cadet with CIN {cin}"}
    flight = (cadet.flight or "").strip().upper()
    since = today - timedelta(days=days)
    states = {r.date.date(): attendance_state(r.status)
              for r in db.query(CadetAttendance).filter(CadetAttendance.cadet_id == cin)}
    totals: dict = defaultdict(lambda: {PRESENT: 0, AUTHORISED: 0, ABSENT: 0})
    rows = []
    for n in (db.query(ProgrammeNight).filter(ProgrammeNight.date >= since, ProgrammeNight.date <= today)
              .order_by(ProgrammeNight.date)):
        state = states.get(n.date)
        if state is None:
            continue
        mine = [s for s in n.sessions if s.flight == flight]
        for category in {s.category for s in mine if not s.split}:
            totals[category][state] += 1
        rows.append({"date": n.date.isoformat(), "state": state,
                     "activities": sorted({s.activity for s in mine})})
    return {
        "flight": flight or None,
        "nights": rows,
        "by_category": [
            {"category": CATEGORIES[k], **totals[k], "rate": _rate(totals[k][PRESENT], sum(totals[k].values()))}
            for k in CATEGORIES if k in totals
        ],
    }
