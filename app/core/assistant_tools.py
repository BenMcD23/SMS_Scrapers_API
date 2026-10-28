"""The read-only tools the squadron assistant can call.

Each tool is one question the database can answer (who's in B flight, whose
first aid lapses this term, what's on the shelf) and returns plain JSON for the
model to reason over. They are deliberately narrow, rather than a general query
language, so the model can only ever see what is listed here:

* **Read-only.** Nothing here writes, so a confused or manipulated model can
  give a wrong answer but can't change a record.
* **No personal data.** Names, ranks, CINs, flights and the squadron's own
  records only. Email, phone, parents, date of birth, addresses, medical and
  dietary never leave the database through here: the answers go to a
  third-party model, and the site's own rule is that those are hidden on
  request and never sent to anyone who doesn't need them.
* **Bounded.** Lists are capped (MAX_ROWS) and flagged when cut, so one broad
  question can't ship the whole roster into a prompt.

The agent loop that calls them is core/assistant.py.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.attendance import ABSENT, AUTHORISED, PRESENT, attendance_state
from core.qualifications import BADGE_TYPE_BY_KEY, BADGE_TYPES, held_level, qual_names_with_flights
from database.models import (
    Cadet,
    CadetAbsence,
    CadetAttendance,
    CadetFlight,
    CadetQualification,
    CadetWht,
    Event317,
    StoresItem,
    StoresOrder,
)

MAX_ROWS = 80


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[Session, BaseModel], dict]


def _name(c: Cadet) -> str:
    return f"{c.first_name or ''} {c.last_name or ''}".strip()


def _ref(c: Cadet) -> dict:
    """How every tool names a cadet, so the model can carry a CIN from one
    answer into the next tool call."""
    return {"cin": c.cin, "name": _name(c), "rank": c.rank, "flight": c.flight}


def _capped(key: str, rows: list, **extra) -> dict:
    out = {**extra, "count": len(rows), key: rows[:MAX_ROWS]}
    if len(rows) > MAX_ROWS:
        out["truncated"] = f"showing the first {MAX_ROWS} of {len(rows)}; narrow the question to see the rest"
    return out


def _today() -> date:
    return datetime.now().date()


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


def squadron_overview(db: Session, _args: NoArgs) -> dict:
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


def find_cadets(db: Session, args: FindCadetsArgs) -> dict:
    words = (args.name or "").casefold().split()
    rows = []
    for c in db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name):
        full = _name(c).casefold()
        if words and not all(w in full for w in words):
            continue
        if not (_matches(c.flight, args.flight) and _matches(c.rank, args.rank)
                and _matches(c.classification, args.classification)):
            continue
        rows.append({**_ref(c), "classification": c.classification, "joined_on": _iso(c.joined_on)})
    return _capped("cadets", rows)


# ─── cadet_record ────────────────────────────────────────────────────────────

class CinArgs(BaseModel):
    cin: int = Field(description="the cadet's CIN, from find_cadets")


def cadet_record(db: Session, args: CinArgs) -> dict:
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


def expiring(db: Session, args: ExpiringArgs) -> dict:
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


def attendance(db: Session, args: AttendanceArgs) -> dict:
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


def badge_holders(db: Session, args: BadgeArgs) -> dict:
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


def stores_stock(db: Session, args: StockArgs) -> dict:
    wanted = (args.item_type or "").casefold()
    rows = [
        {"item": i.item_type, "size": i.size, "gender": i.gender, "quantity": i.quantity,
         "box": i.box.label if i.box else None}
        for i in db.query(StoresItem).order_by(StoresItem.item_type, StoresItem.size)
        if wanted in i.item_type.casefold()
    ]
    return _capped("stock", rows)


def open_orders(db: Session, _args: NoArgs) -> dict:
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


def upcoming_events(db: Session, args: EventsArgs) -> dict:
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


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("squadron_overview", "Cadet numbers by flight, rank and classification, and today's date.",
         NoArgs, squadron_overview),
    Tool("find_cadets", "Look cadets up by name, flight, rank or classification. Returns CINs.",
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
)}


def describe_tools() -> str:
    """The tool list as the system prompt shows it: name, purpose, arguments."""
    lines = []
    for t in TOOLS.values():
        params = []
        for name, f in t.args.model_fields.items():
            kind = getattr(f.annotation, "__name__", None) or str(f.annotation).replace("typing.", "")
            note = f" — {f.description}" if f.description else ""
            default = "" if f.is_required() else f" (default {f.default!r})"
            params.append(f"    {name}: {kind}{default}{note}")
        lines.append(f"- {t.name}: {t.description}" + ("\n" + "\n".join(params) if params else ""))
    return "\n".join(lines)
