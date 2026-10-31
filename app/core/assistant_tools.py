"""The read-only tools the squadron assistant can call.

Each tool is one question the database can answer (who's in B flight, whose
first aid lapses this term, what's on the shelf), plus `aggregate`, which
counts and averages over whole datasets through the same allowlisted query
builder as the OC's custom dashboard. They return plain JSON for the model to
reason over:

* **Read-only.** Nothing here writes, so a confused or manipulated model can
  give a wrong answer but can't change a record.
* **Personal data on purpose only.** Cadets' dates of birth, ages and email
  addresses are included (staff asked for them) unless the person asking has
  "Hide personal data" on. Phone numbers, parents' details, addresses,
  medical, dietary and absence reasons never leave the database through here.
* **Bounded.** Lists are capped and flagged when cut, so one broad question
  can't ship a whole table into a prompt.

The agent loop that calls them is core/assistant.py.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from core import dashboard_query, programme
from core.attendance import ABSENT, AUTHORISED, PRESENT, attendance_state
from core.config import CHATBOT_URL
from core.leaving import LAPSE_DAYS, leaving_status
from core.qualifications import BADGE_TYPE_BY_KEY, BADGE_TYPES, held_level, qual_names_with_flights
from core.ranks import nco_team
from core.recruit_form import age_on
from core.theory_lessons import THEORY_LESSONS
from database.models import (
    AllEvent,
    BadgeOrder,
    Cadet,
    CadetAbsence,
    CadetAttendance,
    CadetEvent,
    CadetFlight,
    CadetLeavingProcess,
    CadetQualification,
    CadetTheoryLesson,
    CadetTheoryProgress,
    CadetWht,
    Event317,
    NcoAppraisal,
    NcoComment,
    ProgrammeNight,
    SessionPlan,
    Staff,
    StaffAttendance,
    StatsSnapshot,
    StoresItem,
    StoresOrder,
)

MAX_ROWS = 80
# A whole squadron fits, so "list everyone with their age" isn't cut short.
MAX_CADETS = 150


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    # (db, args, personal): personal is False while "Hide personal data" is on.
    run: Callable[[Session, BaseModel, bool], dict]


def _name(c: Cadet) -> str:
    return f"{c.first_name or ''} {c.last_name or ''}".strip()


def _ref(c: Cadet) -> dict:
    """How every tool names a cadet, so the model can carry a CIN from one
    answer into the next tool call."""
    return {"cin": c.cin, "name": _name(c), "rank": c.rank, "flight": c.flight}


def _capped(key: str, rows: list, limit: int = MAX_ROWS, **extra) -> dict:
    out = {**extra, "count": len(rows), key: rows[:limit]}
    if len(rows) > limit:
        out["truncated"] = f"showing the first {limit} of {len(rows)}; narrow the question to see the rest"
    return out


def _clip(text, limit: int) -> str | None:
    if text is None:
        return None
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _user_name(user) -> str | None:
    if not user:
        return None
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or None


def _today() -> date:
    return datetime.now().date()


def _personal(c: Cadet, personal: bool) -> dict:
    """A cadet's date of birth, age and email, or nothing while hidden."""
    if not personal:
        return {}
    return {"date_of_birth": _iso(c.date_of_birth), "age": age_on(_day(c.date_of_birth), _today()), "email": c.email}


def _day(value) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def _iso(value) -> str | None:
    d = _day(value)
    return d.isoformat() if d else None


def _matches(value: str | None, wanted: str | None) -> bool:
    return wanted is None or (value or "").strip().casefold() == wanted.strip().casefold()


# ─── squadron_overview ───────────────────────────────────────────────────────

class NoArgs(BaseModel):
    pass


def squadron_overview(db: Session, _args: NoArgs, personal: bool = False) -> dict:
    cadets = db.query(Cadet).all()
    by = defaultdict(lambda: defaultdict(int))
    for c in cadets:
        by["flight"][c.flight or "None"] += 1
        by["rank"][c.rank or "None"] += 1
        by["classification"][c.classification or "None"] += 1
    return {
        "today": _today().isoformat(),
        "cadets": len(cadets),
        "by_flight": dict(by["flight"]),
        "by_rank": dict(by["rank"]),
        "by_classification": dict(by["classification"]),
        "not_seen_on_sms": [_ref(c) for c in cadets if c.missing_since],
    }


# ─── find_cadets ─────────────────────────────────────────────────────────────

class FindCadetsArgs(BaseModel):
    name: str | None = Field(None, max_length=100, description="part of a first or last name")
    flight: str | None = Field(None, max_length=10, description="flight letter, e.g. B")
    rank: str | None = Field(None, max_length=40, description="rank as stored, e.g. Cadet, Cpl, Sgt, FS, CWO")
    classification: str | None = Field(None, max_length=60, description="e.g. Leading Cadet")
    min_age: int | None = Field(None, ge=0, le=30, description="only cadets at least this old today")
    max_age: int | None = Field(None, ge=0, le=30, description="only cadets at most this old today")


def find_cadets(db: Session, args: FindCadetsArgs, personal: bool = False) -> dict:
    words = (args.name or "").casefold().split()
    today = _today()
    rows = []
    for c in db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name):
        full = _name(c).casefold()
        if words and not all(w in full for w in words):
            continue
        if not (_matches(c.flight, args.flight) and _matches(c.rank, args.rank)
                and _matches(c.classification, args.classification)):
            continue
        # Age filters work even with personal data hidden: the answer can
        # still say who's 18, just not their birthday.
        age = age_on(_day(c.date_of_birth), today)
        if (args.min_age is not None or args.max_age is not None) and age is None:
            continue
        if (args.min_age is not None and age < args.min_age) or (args.max_age is not None and age > args.max_age):
            continue
        rows.append({**_ref(c), **_personal(c, personal), "classification": c.classification,
                     "joined_on": _iso(c.joined_on)})
    return _capped("cadets", rows, limit=MAX_CADETS)


# ─── cadet_record ────────────────────────────────────────────────────────────

class CinArgs(BaseModel):
    cin: int = Field(description="the cadet's CIN, from find_cadets")


def cadet_record(db: Session, args: CinArgs, personal: bool = False) -> dict:
    c = db.get(Cadet, args.cin)
    if not c:
        return {"error": f"no cadet with CIN {args.cin}; use find_cadets to look the CIN up"}
    today = _today()
    quals = db.query(CadetQualification).filter_by(cadet_id=c.cin).all()
    flights = db.query(CadetFlight).filter_by(cadet_id=c.cin).all()
    names = qual_names_with_flights([q.qual_type for q in quals], flights)
    badges = {b.name: lvl for b in BADGE_TYPES if (lvl := held_level(b, names))}

    since = datetime.combine(today - timedelta(weeks=12), datetime.min.time())
    register = (db.query(CadetAttendance).filter(CadetAttendance.cadet_id == c.cin,
                                                 CadetAttendance.date >= since).all())
    counts = {PRESENT: 0, AUTHORISED: 0, ABSENT: 0}
    for r in register:
        counts[attendance_state(r.status)] += 1
    last_present = max((r.date for r in db.query(CadetAttendance).filter_by(cadet_id=c.cin)
                        if attendance_state(r.status) == PRESENT), default=None)

    orders = db.query(StoresOrder).filter_by(cadet_id=c.cin, completed=False).all()
    return {
        **_ref(c),
        **_personal(c, personal),
        "classification": c.classification,
        "classification_dates": c.classification_dates or {},
        "joined_on": _iso(c.joined_on),
        "not_seen_on_sms_since": _iso(c.missing_since),
        "badges": badges,
        "qualifications": sorted(
            ({"name": q.qual_type, "achieved": _iso(q.date_achieved), "expires": _iso(q.date_expires)}
             for q in quals),
            key=lambda q: q["name"],
        ),
        "weapon_handling_tests": [
            {"weapon": w.weapon, "passed": _iso(w.date_passed), "expires": _iso(w.date_expires),
             "current": w.date_expires is None or w.date_expires >= today}
            for w in db.query(CadetWht).filter_by(cadet_id=c.cin).order_by(CadetWht.date_passed.desc())
        ],
        "attendance_last_12_weeks": counts,
        "last_present": _iso(last_present),
        # Reasons are left out: they're often medical or family matters.
        "booked_absences": [
            {"from": _iso(a.date_from), "to": _iso(a.date_to)}
            for a in db.query(CadetAbsence).filter(CadetAbsence.cadet_id == c.cin,
                                                   CadetAbsence.date_to >= since)
        ],
        "flying": {"sessions": len(flights), "minutes": sum(f.minutes or 0 for f in flights),
                   "last": _iso(max((f.date for f in flights), default=None))},
        "open_stores_orders": [
            {"order_id": o.id, "created": _iso(o.created_at),
             "items": [{"item": i.item_type, "size": i.size or None, "given": i.given_at is not None,
                        "ready_to_collect": i.ready_to_collect is not None} for i in o.order_items]}
            for o in orders
        ],
    }


# ─── expiring ────────────────────────────────────────────────────────────────

class ExpiringArgs(BaseModel):
    within_days: int = Field(90, ge=0, le=730, description="look this many days ahead")
    include_expired: bool = Field(False, description="also list ones that have already lapsed")


def expiring(db: Session, args: ExpiringArgs, personal: bool = False) -> dict:
    today = _today()
    until = today + timedelta(days=args.within_days)
    rows = []
    for q in db.query(CadetQualification).filter(CadetQualification.date_expires.isnot(None)):
        expires = _day(q.date_expires)
        if q.cadet and expires <= until and (args.include_expired or expires >= today):
            rows.append({**_ref(q.cadet), "what": q.qual_type, "expires": expires.isoformat(),
                         "days_left": (expires - today).days})
    # Only each cadet's latest test per weapon counts: an old lapsed one
    # doesn't matter once they've retested.
    latest: dict = {}
    for w in db.query(CadetWht).filter(CadetWht.date_expires.isnot(None)):
        key = (w.cadet_id, w.weapon)
        if key not in latest or w.date_passed > latest[key].date_passed:
            latest[key] = w
    for w in latest.values():
        if w.cadet and w.date_expires <= until and (args.include_expired or w.date_expires >= today):
            rows.append({**_ref(w.cadet), "what": f"WHT {w.weapon}", "expires": w.date_expires.isoformat(),
                         "days_left": (w.date_expires - today).days})
    rows.sort(key=lambda r: r["days_left"])
    return _capped("expiring", rows, today=today.isoformat())


# ─── attendance ──────────────────────────────────────────────────────────────

class AttendanceArgs(BaseModel):
    weeks: int = Field(8, ge=1, le=52, description="how many weeks back to count")
    flight: str | None = Field(None, max_length=10)
    max_rate: int | None = Field(None, ge=0, le=100,
                                 description="only cadets at or below this attendance percentage "
                                             "(cadets on no register in the window are always kept)")


def attendance(db: Session, args: AttendanceArgs, personal: bool = False) -> dict:
    since = datetime.combine(_today() - timedelta(weeks=args.weeks), datetime.min.time())
    counts: dict = defaultdict(lambda: {PRESENT: 0, AUTHORISED: 0, ABSENT: 0})
    nights = set()
    for r in db.query(CadetAttendance).filter(CadetAttendance.date >= since):
        counts[r.cadet_id][attendance_state(r.status)] += 1
        nights.add(_day(r.date))
    # Over all time, not the window: for a cadet who's stopped coming, when
    # they were last there is the point.
    last_present: dict = {}
    for r in db.query(CadetAttendance):
        if attendance_state(r.status) == PRESENT and r.date > last_present.get(r.cadet_id, datetime.min):
            last_present[r.cadet_id] = r.date

    rows = []
    for c in db.query(Cadet).all():
        if not _matches(c.flight, args.flight):
            continue
        n = counts[c.cin]
        total = sum(n.values())
        # None: not on a single register in the window. That's usually a
        # cadet who's stopped coming, so a "who's below X%" question keeps them.
        rate = round(100 * n[PRESENT] / total) if total else None
        if args.max_rate is not None and rate is not None and rate > args.max_rate:
            continue
        rows.append({**_ref(c), **n, "rate_percent": rate, "last_present": _iso(last_present.get(c.cin))})
    # Worst first, starting with those on no register at all: the question is
    # nearly always "who's not coming".
    rows.sort(key=lambda r: (r["rate_percent"] is not None, r["rate_percent"] or 0))
    return _capped("cadets", rows, since=since.date().isoformat(), register_nights=len(nights))


# ─── badge_holders ───────────────────────────────────────────────────────────

class BadgeArgs(BaseModel):
    badge: str = Field(description="badge key: " + ", ".join(b.key for b in BADGE_TYPES))
    holds: bool = Field(True, description="true for who holds it, false for who doesn't")
    level: str | None = Field(None, max_length=20, description="only this level, e.g. bronze")


def badge_holders(db: Session, args: BadgeArgs, personal: bool = False) -> dict:
    badge = BADGE_TYPE_BY_KEY.get(args.badge)
    if not badge:
        return {"error": f"unknown badge {args.badge!r}", "badges": [b.key for b in BADGE_TYPES]}
    names: dict = defaultdict(list)
    for q in db.query(CadetQualification.cadet_id, CadetQualification.qual_type):
        names[q.cadet_id].append(q.qual_type)
    flights: dict = defaultdict(list)
    for f in db.query(CadetFlight).all():
        flights[f.cadet_id].append(f)

    rows = []
    for c in db.query(Cadet).order_by(Cadet.flight, Cadet.last_name):
        lvl = held_level(badge, qual_names_with_flights(names[c.cin], flights[c.cin]))
        held = lvl is not None and (args.level is None or lvl == args.level.casefold())
        if held == args.holds:
            rows.append({**_ref(c), "level": lvl})
    return _capped("cadets", rows, badge=badge.name, levels=[lv.level for lv in badge.levels])


# ─── stores ──────────────────────────────────────────────────────────────────

class StockArgs(BaseModel):
    item_type: str | None = Field(None, max_length=60, description="part of an item name, e.g. beret")


def stores_stock(db: Session, args: StockArgs, personal: bool = False) -> dict:
    wanted = (args.item_type or "").casefold()
    rows = [
        {"item": i.item_type, "size": i.size, "gender": i.gender, "quantity": i.quantity,
         "box": i.box.label if i.box else None}
        for i in db.query(StoresItem).order_by(StoresItem.item_type, StoresItem.size)
        if wanted in i.item_type.casefold()
    ]
    return _capped("stock", rows)


def open_orders(db: Session, _args: NoArgs, personal: bool = False) -> dict:
    """Every item still owed to a cadet, with whether the shelf has it."""
    stock: dict = defaultdict(int)
    for i in db.query(StoresItem).all():
        stock[(i.item_type.casefold(), i.size.casefold())] += i.quantity
    rows = []
    for o in db.query(StoresOrder).filter_by(completed=False).order_by(StoresOrder.created_at):
        for i in o.order_items:
            if i.given_at:
                continue
            rows.append({
                "order_id": o.id,
                "cadet": _ref(o.cadet) if o.cadet else None,
                "created": _iso(o.created_at),
                "item": i.item_type,
                "size": i.size or None,
                "needs_sizing": i.need_sizing,
                "ready_to_collect": i.ready_to_collect is not None,
                "in_stock": stock[(i.item_type.casefold(), (i.size or "").casefold())] if i.size else None,
            })
    return _capped("items", rows)


# ─── upcoming_events ─────────────────────────────────────────────────────────

class EventsArgs(BaseModel):
    days: int = Field(90, ge=1, le=365, description="look this many days ahead")


def upcoming_events(db: Session, args: EventsArgs, personal: bool = False) -> dict:
    now = datetime.now()
    rows = [
        # adult_ic and contact_number are left out: a named adult's mobile.
        {"title": e.title, "reference": e.reference, "from": e.date_from.isoformat(timespec="minutes"),
         "to": e.date_to.isoformat(timespec="minutes"), "category": e.category, "cost": e.cost,
         "dress": e.dress, "location": e.location.first_line if e.location else None}
        for e in db.query(Event317).filter(Event317.date_to >= now,
                                           Event317.date_from <= now + timedelta(days=args.days))
        .order_by(Event317.date_from)
    ]
    return _capped("events", rows)


# ─── RAFAC documents ─────────────────────────────────────────────────────────

class DocsArgs(BaseModel):
    question: str = Field(min_length=3, max_length=1000,
                          description="a policy question, with the detail that matters (activity, age, role)")


def rafac_docs(db: Session, args: DocsArgs, personal: bool = False) -> dict:
    """The Docs Assistant (routers/chat.py) as a tool: answers from the RAFAC
    controlled documents, with the documents it quotes. "Couldn't check" comes
    back as an error, so the model says it couldn't look rather than guessing."""
    try:
        res = httpx.post(f"{CHATBOT_URL}/ask", json={"question": args.question.strip()}, timeout=60.0)
    except httpx.HTTPError:
        return {"error": "the RAFAC documents service is unreachable just now"}
    if res.status_code != 200:
        return {"error": "the RAFAC documents service failed to answer"}
    try:
        body = res.json()
    except ValueError:
        return {"error": "the RAFAC documents service sent back something unreadable"}
    return {
        "found": bool(body.get("found", True)),
        "answer": _clip(body.get("answer"), 3000),
        "sources": [{"n": s.get("n"), "document": s.get("filename"), "where": s.get("location")}
                    for s in (body.get("sources") or [])[:8] if isinstance(s, dict)],
    }


# ─── Events ──────────────────────────────────────────────────────────────────

class CadetEventsArgs(BaseModel):
    cin: int | None = Field(None, description="one cadet's events")
    event: str | None = Field(None, max_length=100, description="part of an event title: who went")


def cadet_events(db: Session, args: CadetEventsArgs, personal: bool = False) -> dict:
    """Events cadets have been on (Bader's cadet event history), by cadet, by
    event, or every event with how many went."""
    def title(e: AllEvent) -> str:
        return f"{e.parent.title} – {e.title}" if e.parent else e.title

    if args.cin is not None:
        rows = (db.query(CadetEvent).filter_by(cadet_id=args.cin).all())
        return _capped("events", sorted({title(r.event) for r in rows if r.event}))
    events = db.query(AllEvent).all()
    if args.event:
        wanted = args.event.casefold()
        events = [e for e in events if wanted in title(e).casefold()]
        rows = [{"event": title(e), "cadets": [_ref(ce.cadet) for ce in e.cadet_events if ce.cadet]}
                for e in events]
        return _capped("events", [r for r in rows if r["cadets"]], limit=20)
    counts = sorted(({"event": title(e), "category": e.category, "cadets": len(e.cadet_events)}
                     for e in events if e.cadet_events), key=lambda r: -r["cadets"])
    return _capped("events", counts)


# ─── Theory and classification ───────────────────────────────────────────────

class TheoryArgs(BaseModel):
    cin: int | None = Field(None, description="one cadet's theory progress")
    lesson: str | None = Field(None, max_length=80, description="part of a lesson name, e.g. Airmanship")


def theory_progress(db: Session, args: TheoryArgs, personal: bool = False) -> dict:
    """Theory lessons done towards badges and classifications: which parts a
    cadet has had, and who has finished, part-done or not started a lesson."""
    done = defaultdict(set)
    for r in db.query(CadetTheoryProgress):
        done[r.cadet_id].add(r.lesson_key)
    parts = defaultdict(lambda: defaultdict(set))
    for r in db.query(CadetTheoryLesson):
        parts[r.cadet_id][r.lesson_key].add(r.part)

    if args.cin is not None:
        c = db.get(Cadet, args.cin)
        if not c:
            return {"error": f"no cadet with CIN {args.cin}"}
        return {**_ref(c), "classification": c.classification, "lessons": [
            {"lesson": lesson.name, "group": lesson.category, "parts": lesson.parts,
             "done": lesson.key in done[c.cin], "parts_had": sorted(parts[c.cin][lesson.key])}
            for lesson in THEORY_LESSONS if lesson.key in done[c.cin] or parts[c.cin][lesson.key]
        ]}

    lessons = [lesson for lesson in THEORY_LESSONS
               if not args.lesson or args.lesson.casefold() in lesson.name.casefold()]
    cadets = db.query(Cadet).order_by(Cadet.last_name).all()
    out = []
    for lesson in lessons:
        finished = [c for c in cadets if lesson.key in done[c.cin]]
        partial = [c for c in cadets if lesson.key not in done[c.cin] and parts[c.cin][lesson.key]]
        row = {"lesson": lesson.name, "group": lesson.category, "finished": len(finished),
               "part_done": len(partial)}
        if args.lesson:
            row |= {"finished_cadets": [_ref(c) for c in finished][:MAX_ROWS],
                    "part_done_cadets": [{**_ref(c), "parts_had": sorted(parts[c.cin][lesson.key])}
                                         for c in partial][:MAX_ROWS]}
        out.append(row)
    return {"lessons": out}


# ─── Badge orders ────────────────────────────────────────────────────────────

def badge_orders(db: Session, _args: NoArgs, personal: bool = False) -> dict:
    """Badges ordered for cadets and not yet handed over."""
    rows = []
    for o in db.query(BadgeOrder).order_by(BadgeOrder.created_at):
        for i in o.order_items:
            if i.given_at:
                continue
            rows.append({"cadet": _ref(o.cadet) if o.cadet else None, "badge": i.badge_name,
                         "ordered": _iso(o.created_at), "replacement": i.replacement,
                         "ready_to_collect": i.ready_to_collect is not None})
    return _capped("badges", rows)


# ─── NCO team ────────────────────────────────────────────────────────────────

class AppraisalArgs(BaseModel):
    cin: int | None = Field(None, description="one NCO's appraisals in full")


def nco_appraisals(db: Session, args: AppraisalArgs, personal: bool = False) -> dict:
    """NCO appraisals: when each NCO was last appraised, when the next review
    is due, and any flags; one NCO's in full when asked."""
    today = _today()
    if args.cin is not None:
        rows = (db.query(NcoAppraisal).filter_by(cadet_id=args.cin)
                .order_by(NcoAppraisal.appraisal_date.desc()).all())
        return _capped("appraisals", [{
            "date": _iso(a.appraisal_date), "next_review": _iso(a.next_review_date),
            "cause_for_concern": a.cause_for_concern, "extend_probation": a.extend_probation,
            **{k: _clip(getattr(a, k), 800) for k in
               ("general_observations", "effectiveness_in_role", "strengths", "weaknesses", "targets")},
        } for a in rows], limit=5)
    latest: dict = {}
    for a in db.query(NcoAppraisal).order_by(NcoAppraisal.appraisal_date):
        latest[a.cadet_id] = a
    rows = []
    for c in nco_team(db):
        a = latest.get(c.cin)
        rows.append({**_ref(c), "last_appraised": _iso(a.appraisal_date) if a else None,
                     "next_review": _iso(a.next_review_date) if a else None,
                     "overdue": bool(a) and _day(a.next_review_date) < today,
                     "cause_for_concern": bool(a and a.cause_for_concern)})
    return _capped("ncos", rows, never_appraised=sum(1 for r in rows if not r["last_appraised"]))


class CommentsArgs(BaseModel):
    cin: int | None = Field(None, description="only comments about this cadet")
    days: int = Field(90, ge=1, le=730, description="how far back to look")


def nco_comments(db: Session, args: CommentsArgs, personal: bool = False) -> dict:
    """Notes NCOs and staff have left about cadets or the squadron, newest first."""
    since = datetime.combine(_today() - timedelta(days=args.days), datetime.min.time())
    q = db.query(NcoComment).filter(NcoComment.comment_date >= since)
    if args.cin is not None:
        q = q.filter(NcoComment.cadet_id == args.cin)
    rows = [{"date": _iso(c.comment_date), "about": c.cadet_name or "the squadron",
             "by": _user_name(c.author), "subject": c.subject, "note": _clip(c.body, 500),
             "replies": len(c.replies)}
            for c in q.order_by(NcoComment.comment_date.desc())]
    return _capped("comments", rows)


class PlansArgs(BaseModel):
    status: str | None = Field(None, max_length=30,
                               description="draft, submitted, amendments_requested or approved")


def session_plans(db: Session, args: PlansArgs, personal: bool = False) -> dict:
    """NCOs' session plans: what's being run when, by whom, and where each is
    in review."""
    q = db.query(SessionPlan)
    if args.status:
        q = q.filter(SessionPlan.status == args.status)
    rows = [{"session": p.session_name, "date": _iso(p.session_date), "ic": p.session_ic,
             "status": p.status, "by": _user_name(p.author), "aim": _clip(p.aim, 200),
             "reviewed_by": p.reviewed_by}
            for p in q.order_by(SessionPlan.session_date.desc())]
    return _capped("plans", rows)


# ─── Leaving and trends ──────────────────────────────────────────────────────

def leaving(db: Session, _args: NoArgs, personal: bool = False) -> dict:
    """Cadets in the leaving process: parents told they've stopped coming, and
    whether the two weeks to reply are up. Lapsed cadets not yet in it are in
    attendance (no register in the window)."""
    now = datetime.now()
    rows = []
    for p in db.query(CadetLeavingProcess).order_by(CadetLeavingProcess.sent_at):
        status, days_left = leaving_status(p.sent_at, now)
        rows.append({**_ref(p.cadet), "parents_told": _iso(p.sent_at), "status": status,
                     "days_left": days_left, "adjutant_asked": _iso(p.adj_requested_at)})
    return _capped("cadets", rows, lapse_days=LAPSE_DAYS)


class HistoryArgs(BaseModel):
    months: int = Field(12, ge=1, le=60, description="how far back")


def stats_history(db: Session, args: HistoryArgs, personal: bool = False) -> dict:
    """Squadron strength over time from the weekly stats snapshots: total and
    by flight, rank and classification. Spread evenly to at most 60 points."""
    since = datetime.now() - timedelta(days=31 * args.months)
    snaps = db.query(StatsSnapshot).filter(StatsSnapshot.captured_at >= since).order_by(StatsSnapshot.captured_at).all()
    if len(snaps) > 60:
        step = len(snaps) / 60
        snaps = [snaps[int(i * step)] for i in range(60)] + [snaps[-1]]
    return {"points": [
        {"date": _iso(s.captured_at), **{k: (s.data or {}).get(k) for k in
                                         ("total_cadets", "by_flight", "by_rank", "by_classification")}}
        for s in snaps
    ]}


# ─── Staff ───────────────────────────────────────────────────────────────────

class StaffArgs(BaseModel):
    weeks: int = Field(12, ge=1, le=104, description="attendance over the last N weeks")


def staff_list(db: Session, args: StaffArgs, personal: bool = False) -> dict:
    """Adult staff by name and rank, with their recent attendance. Their
    emails, phones and addresses are never given."""
    since = datetime.combine(_today() - timedelta(weeks=args.weeks), datetime.min.time())
    counts: dict = defaultdict(lambda: {PRESENT: 0, AUTHORISED: 0, ABSENT: 0})
    for r in db.query(StaffAttendance).filter(StaffAttendance.date >= since):
        counts[r.staff_id][attendance_state(r.status)] += 1
    rows = [{"cin": s.cin, "name": f"{s.first_name} {s.last_name}".strip(), "rank": s.rank,
             **counts[s.cin]}
            for s in db.query(Staff).order_by(Staff.last_name)]
    return _capped("staff", rows, since=since.date().isoformat())


# ─── Programme ───────────────────────────────────────────────────────────────

class ProgrammeArgs(BaseModel):
    start: date | None = Field(None, description="first night to look at, YYYY-MM-DD (default 5 weeks ago)")
    end: date | None = Field(None, description="last night, YYYY-MM-DD (default 3 weeks ahead)")
    activity: str | None = Field(None, max_length=80,
                                 description="part of an activity's name: who missed it on the nights it ran")
    cin: int | None = Field(None, description="one cadet's nights against the programme, with turnout by category")


def programme_tool(db: Session, args: ProgrammeArgs, personal: bool = False) -> dict:
    """The parade-night programme: what each flight did on each night, who
    missed a given activity, or one cadet's nights. Turnout by kind of night
    over the period comes with the night list."""
    today = _today()
    start = args.start or today - timedelta(weeks=5)
    end = args.end or today + timedelta(weeks=3)
    if args.cin is not None:
        return programme.cadet_sessions(db, args.cin, days=max(7, (today - start).days))
    nights = (db.query(ProgrammeNight).filter(ProgrammeNight.date >= start, ProgrammeNight.date <= end)
              .order_by(ProgrammeNight.date).all())
    if not args.activity:
        return {
            **_capped("nights", [{"date": n.date.isoformat(), "uniform": n.uniform,
                                  "sessions": programme._night_summary(n)} for n in nights], limit=40),
            "turnout_by_kind": programme.programme_stats(db, days=max(7, (today - start).days))["categories"],
        }

    wanted = args.activity.casefold()
    flights_of = defaultdict(list)
    for c in db.query(Cadet).all():
        flights_of[(c.flight or "").strip().upper()].append(c)
    rows = []
    for n in nights:
        ran = [x for x in n.sessions if wanted in x.activity.casefold()]
        if not ran or n.date > today:
            continue
        on = {r.cadet_id: attendance_state(r.status) for r in db.query(CadetAttendance).filter(
            CadetAttendance.date >= datetime.combine(n.date, datetime.min.time()),
            CadetAttendance.date < datetime.combine(n.date + timedelta(days=1), datetime.min.time()))}
        flights = sorted({x.flight for x in ran})
        cadets = [c for f in flights for c in flights_of[f]]
        rows.append({
            "date": n.date.isoformat(), "activity": ran[0].activity, "flights": flights,
            # Split sessions: the flight was divided, so being away is certain but
            # being there doesn't mean they did this half.
            "split": any(x.split for x in ran),
            "missed": [{**_ref(c), "absence": on[c.cin]} for c in cadets if on.get(c.cin) in (ABSENT, AUTHORISED)],
            "there": len([c for c in cadets if on.get(c.cin) == PRESENT]),
            "no_register": len([c for c in cadets if c.cin not in on]),
        })
    return _capped("nights", rows, limit=20)


# ─── aggregate ───────────────────────────────────────────────────────────────

class AggregateArgs(dashboard_query.QuerySpec):
    group_by: list[dashboard_query.GroupBy] = Field(
        default_factory=list, max_length=2,
        description='e.g. [{"field": "date", "grain": "month"}, {"field": "flight"}]')
    filters: list[dashboard_query.Filter] = Field(
        default_factory=list, max_length=10,
        description='e.g. [{"field": "flight", "op": "eq", "value": "B"}]; dates as YYYY-MM-DD')
    sort: Literal["label", "value_desc", "value_asc"] = Field("label", description="label, value_desc or value_asc")
    range_days: int | None = Field(None, ge=1, le=3650,
                                   description="only the last N days of the dataset's own date")


def aggregate(db: Session, args: AggregateArgs, personal: bool = False) -> dict:
    """Counts, turnout and totals over whole datasets: the OC dashboard's
    query builder, so it can only read what that allowlist names."""
    spec = dashboard_query.QuerySpec(**args.model_dump(exclude={"range_days"}))
    try:
        result = dashboard_query.run(db, spec, range_days=args.range_days)
    except (dashboard_query.QueryError, ValidationError) as e:
        return {"error": str(e)}
    return {"group_by": [g.field for g in spec.group_by], "measure": spec.measure, **result}


def _datasets_help() -> str:
    lines = []
    for key, ds in dashboard_query.DATASETS.items():
        fields = ", ".join(f"{k} ({f.kind})" for k, f in ds.fields.items())
        lines.append(f"      {key}: {ds.description} Fields: {fields}. Measures: {', '.join(ds.measures)}.")
    return "\n".join(lines)


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("squadron_overview", "Cadet numbers by flight, rank and classification, and today's date.",
         NoArgs, squadron_overview),
    Tool("find_cadets", "Look cadets up by name, flight, rank, classification or age. Returns CINs, "
         "and dates of birth, ages and emails unless personal data is hidden.",
         FindCadetsArgs, find_cadets),
    Tool("cadet_record", "One cadet's badges, qualifications, WHTs, recent attendance, flying and open "
         "stores orders.", CinArgs, cadet_record),
    Tool("expiring", "Qualifications (first aid etc.) and weapon handling tests expiring soon, soonest first.",
         ExpiringArgs, expiring),
    Tool("attendance", "Parade attendance per cadet over the last N weeks, worst first.",
         AttendanceArgs, attendance),
    Tool("badge_holders", "Who holds (or doesn't hold) a badge, with the level held.", BadgeArgs, badge_holders),
    Tool("stores_stock", "Uniform stock on the shelves by item and size.", StockArgs, stores_stock),
    Tool("open_orders", "Uniform items still owed to cadets, and whether each is in stock.", NoArgs, open_orders),
    Tool("upcoming_events", "The squadron's upcoming events.", EventsArgs, upcoming_events),
    Tool("rafac_docs", "Ask the RAFAC controlled documents (ACPs, JSPs, policy) a policy question; returns "
         "the answer and the documents it quotes. Slow (10s+): only for policy questions.", DocsArgs, rafac_docs),
    Tool("cadet_events", "Events cadets have been on: one cadet's (cin), who went on one (event), or every "
         "event with how many went.", CadetEventsArgs, cadet_events),
    Tool("theory_progress", "Theory lessons done towards badges and classifications: one cadet's (cin), "
         "who has finished or part-done a lesson (lesson), or a summary of every lesson.",
         TheoryArgs, theory_progress),
    Tool("badge_orders", "Badges ordered for cadets and not yet handed over.", NoArgs, badge_orders),
    Tool("nco_appraisals", "Every NCO's last appraisal, next review and flags; one NCO's appraisals in full "
         "with cin.", AppraisalArgs, nco_appraisals),
    Tool("nco_comments", "Notes NCOs and staff have left about cadets or the squadron.", CommentsArgs,
         nco_comments),
    Tool("session_plans", "NCOs' session plans and where each is in review.", PlansArgs, session_plans),
    Tool("leaving", "Cadets in the leaving process (parents told) and whether their two weeks are up.",
         NoArgs, leaving),
    Tool("stats_history", "Squadron strength over time (total, by flight, rank, classification) from "
         "weekly snapshots: for trends.", HistoryArgs, stats_history),
    Tool("staff_list", "Adult staff by name and rank, with recent attendance.", StaffArgs, staff_list),
    Tool("programme", "The parade-night programme: what each flight did each night (with turnout by kind of "
         "night), who missed an activity (activity), or one cadet's nights against it (cin). Flights: C is "
         "probationary, then A and B.", ProgrammeArgs, programme_tool),
    Tool("aggregate",
         "Count, average or total over a whole dataset, grouped by up to two fields (dates bucket by "
         "grain: day, week, month, year) and filtered (ops: eq, neq, in, not_in, gt, gte, lt, lte, empty, "
         "not_empty). Use it for trends and breakdowns to chart. Datasets:\n" + _datasets_help(),
         AggregateArgs, aggregate),
)}


def describe_tools() -> str:
    """The tool list as the system prompt shows it: name, purpose, arguments."""
    lines = []
    for t in TOOLS.values():
        params = []
        for name, f in t.args.model_fields.items():
            kind = getattr(f.annotation, "__name__", None) or str(f.annotation).replace("typing.", "")
            note = f" — {f.description}" if f.description else ""
            default = "" if f.is_required() else f" (default {f.get_default(call_default_factory=True)!r})"
            params.append(f"    {name}: {kind}{default}{note}")
        lines.append(f"- {t.name}: {t.description}" + ("\n" + "\n".join(params) if params else ""))
    return "\n".join(lines)
