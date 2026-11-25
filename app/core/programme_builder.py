"""The rules behind the programme builder (routers/programme_builder.py): who
can be put on a session, what clashes, what to put in a gap, and how a built
month becomes the programme record stats and training read.

Everything here works on the builder's own rows (ProgrammeDraft and friends)
plus the record of what the squadron actually ran (Programme_Sessions, from
past publishes and the old Google Doc imports), so suggestions get better the
more history there is and still work with none.

People are keys rather than foreign keys: "staff:3001" for an adult,
"cadet:1234" for a cadet (an NCO is a cadet with an NCO rank). Staff and
cadets live in different tables with different CIN ranges, and a key reads the
same in a slot's people, a comment's mentions and an assignee.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from core.attendance import PRESENT, attendance_state
from core.programme import CATEGORIES
from core.ranks import is_nco_rank
from database.models import (
    Cadet,
    CadetAttendance,
    NcoHoliday,
    ProgrammeBankItem,
    ProgrammeDraft,
    ProgrammeDraftNight,
    ProgrammeLessonLink,
    ProgrammeNight,
    ProgrammeRoom,
    ProgrammeSession,
    ProgrammeSlot,
    Staff,
    User,
)

# A parade night's two halves; a slot with no period runs all night.
PERIODS: dict[int, str] = {1: "1st half", 2: "2nd half"}

# The programme doc's column order: probationary (C) first, then A and B.
DEFAULT_FLIGHTS = ["C", "A", "B"]
FLIGHT_LABELS = {"C": "Probationary"}

# Turnout to plan rooms on when there's no register to go by.
DEFAULT_TURNOUT = 0.75

# @[Sgt Sarah Hughes](staff:3001) in a comment body: the name as typed, and who.
MENTION_RE = re.compile(r"@\[([^\]\n]{1,80})\]\(((?:staff|cadet):\d{1,12})\)")
KEY_RE = re.compile(r"^(staff|cadet):(\d{1,12})$")


# ── flights and nights ──────────────────────────────────────────────────────

def flights(db: Session) -> list[str]:
    """The squadron's cadet flights, C first as in the doc. Flights come from
    Bader, so a fourth one appears here as soon as a cadet is in it."""
    seen = {(c.flight or "").strip().upper() for c in db.query(Cadet.flight).filter(Cadet.banned.is_(False))}
    lettered = {f for f in seen if len(f) == 1 and f.isalpha()}
    if not lettered:
        return list(DEFAULT_FLIGHTS)
    return sorted(lettered, key=lambda f: (f != "C", f))


def flight_label(flight: str) -> str:
    return f"{flight} Flight ({FLIGHT_LABELS[flight]})" if flight in FLIGHT_LABELS else f"{flight} Flight"


def who_label(slot_flights: list[str], all_flights: list[str]) -> str:
    """"All", "A Flight" or "A and B Flights": who a session is for."""
    if not slot_flights or set(all_flights) <= set(slot_flights):
        return "All"
    ordered = [f for f in all_flights if f in slot_flights] + sorted(set(slot_flights) - set(all_flights))
    if len(ordered) == 1:
        return flight_label(ordered[0])
    return f"{', '.join(ordered[:-1])} and {ordered[-1]} Flights"


def parade_weekdays(db: Session, today: date | None = None) -> list[int]:
    """The weekdays the squadron parades on (0 is Monday), from the last six
    months of the programme record, or the register when there's no record.
    A weekday counts if it had at least a third as many nights as the busiest,
    so a one-off Saturday doesn't become a parade night."""
    today = today or date.today()
    since = today - timedelta(days=183)
    days = [n.date for n in db.query(ProgrammeNight.date).filter(ProgrammeNight.date >= since)]
    if not days:
        days = [r.date.date() for r in db.query(CadetAttendance.date)
                .filter(CadetAttendance.date >= datetime.combine(since, datetime.min.time()))
                .distinct()]
    counts = Counter(d.weekday() for d in days)
    if not counts:
        return [0]
    busiest = max(counts.values())
    return sorted(day for day, n in counts.items() if n * 3 >= busiest)


def month_bounds(month: str) -> tuple[date, date]:
    """First day of "YYYY-MM" and the first day after it. ValueError if it isn't one."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", month or "")
    if not match:
        raise ValueError(f"{month!r} isn't a month (use YYYY-MM)")
    year, mon = int(match[1]), int(match[2])
    first = date(year, mon, 1)
    return first, date(year + mon // 12, mon % 12 + 1, 1)


def month_dates(month: str, weekdays: list[int]) -> list[date]:
    first, after = month_bounds(month)
    return [first + timedelta(days=i) for i in range((after - first).days)
            if (first + timedelta(days=i)).weekday() in weekdays]


def night_label(d: date) -> str:
    """"Mon 7 Dec": short enough for a quote or a list."""
    return f"{d:%a} {d.day} {d:%b}"


def period_label(period: int | None) -> str:
    return PERIODS.get(period, "Whole night") if period else "Whole night"


def slot_periods(slot) -> tuple[int, ...]:
    return tuple(PERIODS) if slot.period is None else (slot.period,)


def slot_flights(slot, all_flights: list[str]) -> list[str]:
    return list(slot.flights) if slot.flights else list(all_flights)


def slot_quote(slot: ProgrammeSlot, all_flights: list[str]) -> str:
    """What a comment on a session quotes: "Mon 7 Dec · 1st half · A Flight: Map Reading 2"."""
    return (f"{night_label(slot.night.date)} · {period_label(slot.period)} · "
            f"{who_label(slot.flights or [], all_flights)}: {slot.title}")


# ── people ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Person:
    key: str
    kind: str          # "staff", "nco" or "cadet"
    first_name: str
    last_name: str
    rank: str | None
    flight: str | None
    email: str | None

    @property
    def name(self) -> str:
        return " ".join(p for p in (self.rank, self.first_name, self.last_name) if p)

    @property
    def mentionable(self) -> bool:
        """Staff and NCOs can be @'d: they can open the programme to answer."""
        return self.kind in ("staff", "nco") and bool(self.email)


def directory(db: Session) -> dict[str, Person]:
    """Everyone who can be put on a session or mentioned, by key. Banned
    cadets are left out: they can't be on parade."""
    people: dict[str, Person] = {}
    for s in db.query(Staff).order_by(Staff.last_name, Staff.first_name):
        key = f"staff:{s.cin}"
        people[key] = Person(key, "staff", s.first_name, s.last_name, s.rank, None, (s.email or None))
    for c in db.query(Cadet).filter(Cadet.banned.is_(False)).order_by(Cadet.last_name, Cadet.first_name):
        key = f"cadet:{c.cin}"
        kind = "nco" if is_nco_rank(c.rank) else "cadet"
        people[key] = Person(key, kind, c.first_name, c.last_name, c.rank,
                             (c.flight or "").strip().upper() or None, (c.email or None))
    return people


def person_for_email(people: dict[str, Person], email: str | None) -> Person | None:
    """The roster entry for a signed-in account: staff first, as an adult who
    is also on the cadet roster from years ago is staff now."""
    wanted = (email or "").strip().lower()
    if not wanted:
        return None
    matches = [p for p in people.values() if (p.email or "").lower() == wanted]
    return next((p for p in matches if p.kind == "staff"), matches[0] if matches else None)


def holidays(db: Session, people: dict[str, Person], start: date, end: date) -> dict[str, list[tuple[date, date]]]:
    """NCO holidays overlapping start..end, by person key. Holidays are booked
    by account, so they're matched to the roster by email."""
    by_email: dict[str, str] = {}
    for p in people.values():
        if p.email:
            by_email.setdefault(p.email.lower(), p.key)
    away: dict[str, list[tuple[date, date]]] = defaultdict(list)
    rows = (db.query(NcoHoliday, User.email).join(User, NcoHoliday.user_id == User.id)
            .filter(NcoHoliday.cancelled_at.is_(None),
                    NcoHoliday.date_from < datetime.combine(end + timedelta(days=1), datetime.min.time()),
                    NcoHoliday.date_to >= datetime.combine(start, datetime.min.time())))
    for holiday, email in rows:
        key = by_email.get((email or "").lower())
        if key:
            away[key].append((holiday.date_from.date(), holiday.date_to.date()))
    return away


def is_away(away: dict[str, list[tuple[date, date]]], key: str, day: date) -> bool:
    return any(start <= day <= end for start, end in away.get(key, []))


def parse_mentions(body: str, people: dict[str, Person]) -> list[str]:
    """The keys @'d in a comment that are real, mentionable people, in order,
    once each. A forged or stale token is left as text and mentions no one."""
    keys: list[str] = []
    for _, key in MENTION_RE.findall(body or ""):
        person = people.get(key)
        if person and person.mentionable and key not in keys:
            keys.append(key)
    return keys


def plain_mentions(body: str) -> str:
    """A comment as people read it: "@[Sgt Sarah Hughes](staff:3001)" → "@Sgt Sarah Hughes"."""
    return MENTION_RE.sub(lambda m: f"@{m[1]}", body or "")


# ── numbers ─────────────────────────────────────────────────────────────────

def expected_numbers(db: Session, all_flights: list[str], today: date | None = None) -> dict[str, int]:
    """How many cadets each flight is likely to bring on a parade night: its
    roll times the squadron's turnout over the last twelve weeks. Rooms are
    planned on this, so it rounds up."""
    today = today or date.today()
    roll = Counter((c.flight or "").strip().upper() for c in db.query(Cadet.flight).filter(Cadet.banned.is_(False)))
    since = datetime.combine(today - timedelta(weeks=12), datetime.min.time())
    states = [attendance_state(r.status) for r in db.query(CadetAttendance.status).filter(CadetAttendance.date >= since)]
    rate = (sum(s == PRESENT for s in states) / len(states)) if states else DEFAULT_TURNOUT
    return {f: math.ceil(roll.get(f, 0) * rate) for f in all_flights}


def slot_headcount(slot, expected: dict[str, int], all_flights: list[str]) -> int:
    """Cadets from its flights plus everyone named on it."""
    return sum(expected.get(f, 0) for f in slot_flights(slot, all_flights)) + len(slot.people or [])


# ── checks ──────────────────────────────────────────────────────────────────

@dataclass
class Issue:
    severity: str            # "clash", "warning" or "info"
    code: str
    message: str
    night_id: int | None = None
    slot_ids: list[int] = field(default_factory=list)
    # A one-click fix the page can apply: {"slot_id": 5, "room_id": 2, "label": "Use Classroom 1"}.
    fix: dict | None = None

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message,
                "night_id": self.night_id, "slot_ids": self.slot_ids, "fix": self.fix}


SEVERITY_ORDER = {"clash": 0, "warning": 1, "info": 2}


@dataclass
class Context:
    """What the checks and suggestions need besides the month itself, read
    once per request."""
    flights: list[str]
    people: dict[str, Person]
    away: dict[str, list[tuple[date, date]]]
    rooms: dict[int, ProgrammeRoom]
    expected: dict[str, int]
    # (flight, activity casefold) -> dates it ran, from the record outside this month
    history: dict[tuple[str, str], list[date]]
    # (flight, subject_key) -> {part: first date}, from confirmed lesson links in the record
    lessons: dict[tuple[str, str], dict[int, date]]


def build_context(db: Session, draft: ProgrammeDraft, today: date | None = None) -> Context:
    first, after = month_bounds(draft.month)
    all_flights = flights(db)
    people = directory(db)
    rooms = {r.id: r for r in db.query(ProgrammeRoom).all()}

    history: dict[tuple[str, str], list[date]] = defaultdict(list)
    lessons: dict[tuple[str, str], dict[int, date]] = defaultdict(dict)
    links = {link.activity.casefold(): link for link in
             db.query(ProgrammeLessonLink).filter(ProgrammeLessonLink.confirmed.is_(True),
                                                  ProgrammeLessonLink.subject_key.isnot(None))}
    since = first - timedelta(days=730)
    rows = (db.query(ProgrammeNight.date, ProgrammeSession.flight, ProgrammeSession.activity)
            .join(ProgrammeSession).filter(ProgrammeNight.date >= since)
            .filter((ProgrammeNight.date < first) | (ProgrammeNight.date >= after)))
    for day, flight, activity in rows:
        history[(flight, activity.casefold())].append(day)
        link = links.get(activity.casefold())
        if link and link.part:
            seen = lessons[(flight, link.subject_key)]
            if link.part not in seen or day < seen[link.part]:
                seen[link.part] = day
    return Context(all_flights, people, holidays(db, people, first, after), rooms,
                   expected_numbers(db, all_flights, today), history, lessons)


def _cells(draft: ProgrammeDraft, ctx: Context):
    """(night, period, flight) -> slots covering it."""
    cells: dict[tuple[int, int, str], list[ProgrammeSlot]] = defaultdict(list)
    for night in draft.nights:
        for slot in night.slots:
            for period in slot_periods(slot):
                for flight in slot_flights(slot, ctx.flights):
                    cells[(night.id, period, flight)].append(slot)
    return cells


def _free_room(ctx: Context, busy: set[int], need: int, prefer: int | None = None) -> ProgrammeRoom | None:
    """A room not in use that fits `need`: the preferred one if it can, else
    the smallest that fits, so the hall stays free for what needs it."""
    candidates = [r for r in ctx.rooms.values() if r.active and r.id not in busy
                  and (r.capacity is None or r.capacity >= need)]
    if prefer is not None and any(r.id == prefer for r in candidates):
        return ctx.rooms[prefer]
    return min(candidates, key=lambda r: (r.capacity is None, r.capacity or 0, r.position, r.name), default=None)


def check(draft: ProgrammeDraft, ctx: Context) -> list[dict]:
    """Everything worth a second look before publishing, worst first.

    clash   – can't happen as planned: someone or a room in two places, someone
              on holiday, a room too small.
    warning – probably a mistake: a flight with nothing on, a lesson before
              the one it follows, a flight split without saying where.
    info    – worth knowing: no room, no one running it, no duty NCO."""
    issues: list[Issue] = []
    cells = _cells(draft, ctx)
    lesson_order = _lesson_dates(draft, ctx)

    for night in draft.nights:
        when = night_label(night.date)
        by_period: dict[int, list[ProgrammeSlot]] = defaultdict(list)
        for slot in night.slots:
            for period in slot_periods(slot):
                by_period[period].append(slot)

        for period, slots in sorted(by_period.items()):
            half = PERIODS[period]
            # People in two places at once.
            where: dict[str, list[ProgrammeSlot]] = defaultdict(list)
            for slot in slots:
                for key in slot.people or []:
                    where[key].append(slot)
            for key, clashing in where.items():
                if len(clashing) > 1:
                    name = ctx.people[key].name if key in ctx.people else "Someone no longer on the roster"
                    titles = " and ".join(s.title for s in clashing)
                    issues.append(Issue("clash", "person_double_booked",
                                        f"{name} is on {titles} in the {half} on {when}",
                                        night.id, [s.id for s in clashing]))
            # Rooms in use twice.
            rooms: dict[int, list[ProgrammeSlot]] = defaultdict(list)
            for slot in slots:
                if slot.room_id:
                    rooms[slot.room_id].append(slot)
            busy = set(rooms)
            for room_id, clashing in rooms.items():
                if len(clashing) > 1:
                    room = ctx.rooms.get(room_id)
                    other = clashing[-1]
                    spare = _free_room(ctx, busy, slot_headcount(other, ctx.expected, ctx.flights))
                    issues.append(Issue(
                        "clash", "room_double_booked",
                        f"{room.name if room else 'A room'} has {' and '.join(s.title for s in clashing)} "
                        f"in the {half} on {when}",
                        night.id, [s.id for s in clashing],
                        {"slot_id": other.id, "room_id": spare.id, "label": f"Move {other.title} to {spare.name}"}
                        if spare else None))
            # Flights with nothing on, and flights split without a room each.
            for flight in ctx.flights:
                covering = cells.get((night.id, period, flight), [])
                if not covering:
                    issues.append(Issue("warning", "flight_gap",
                                        f"{flight_label(flight)} has nothing in the {half} on {when}", night.id))

        # Whole-night gaps are reported per half above; a night with nothing
        # at all says so once instead of six times.
        if not night.slots:
            issues = [i for i in issues if not (i.night_id == night.id and i.code == "flight_gap")]
            issues.append(Issue("warning", "empty_night", f"Nothing planned on {when} yet", night.id))

        for slot in night.slots:
            label = f"{slot.title} on {when}"
            for key in slot.people or []:
                person = ctx.people.get(key)
                if person is None:
                    issues.append(Issue("warning", "unknown_person",
                                        f"{label} has someone who's no longer on the roster", night.id, [slot.id]))
                elif is_away(ctx.away, key, night.date):
                    issues.append(Issue("clash", "person_away", f"{person.name} is on holiday for {label}",
                                        night.id, [slot.id]))
            room = ctx.rooms.get(slot.room_id) if slot.room_id else None
            need = slot_headcount(slot, ctx.expected, ctx.flights)
            if room and room.capacity and need > room.capacity:
                busy = {s.room_id for s in night.slots if s.room_id and s.id != slot.id
                        and set(slot_periods(s)) & set(slot_periods(slot))}
                bigger = _free_room(ctx, busy | {room.id}, need)
                issues.append(Issue(
                    "clash", "room_too_small",
                    f"{label} expects about {need} but {room.name} holds {room.capacity}", night.id, [slot.id],
                    {"slot_id": slot.id, "room_id": bigger.id, "label": f"Use {bigger.name}"} if bigger else None))
            if not room and ctx.rooms:
                busy = {s.room_id for s in night.slots if s.room_id and s.id != slot.id
                        and set(slot_periods(s)) & set(slot_periods(slot))}
                spare = _free_room(ctx, busy, need)
                issues.append(Issue("info", "no_room", f"{label} has no room", night.id, [slot.id],
                                    {"slot_id": slot.id, "room_id": spare.id, "label": f"Use {spare.name}"}
                                    if spare else None))
            if not slot.people and not (slot.instructor or "").strip():
                issues.append(Issue("info", "no_instructor", f"No one is running {label}", night.id, [slot.id]))
            if slot.subject_key and slot.part and slot.part > 1:
                for flight in slot_flights(slot, ctx.flights):
                    before = lesson_order.get((flight, slot.subject_key), {}).get(slot.part - 1)
                    if before is None or before > night.date:
                        issues.append(Issue(
                            "warning", "lesson_order",
                            f"{flight_label(flight)} has {slot.title} on {when} before lesson {slot.part - 1}",
                            night.id, [slot.id]))
                        break

        if not (night.duty_nco or "").strip():
            issues.append(Issue("info", "no_duty_nco", f"No duty NCO on {when}", night.id))
        if not (night.uniform or "").strip():
            issues.append(Issue("info", "no_uniform", f"No uniform set for {when}", night.id))

    # The same thing twice in a month for one flight is usually a slip.
    seen: dict[tuple[str, str], list[tuple[ProgrammeDraftNight, ProgrammeSlot]]] = defaultdict(list)
    for night in draft.nights:
        for slot in night.slots:
            for flight in slot_flights(slot, ctx.flights):
                seen[(flight, slot.title.casefold())].append((night, slot))
    for (flight, _), uses in seen.items():
        nights = {n.id for n, _ in uses}
        if len(nights) > 1:
            first_night, first_slot = uses[0]
            issues.append(Issue("info", "repeated",
                                f"{flight_label(flight)} has {first_slot.title} {len(nights)} times this month",
                                first_night.id, sorted({s.id for _, s in uses})))

    issues.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.night_id or 0, i.code))
    return [i.as_dict() for i in issues]


def _lesson_dates(draft: ProgrammeDraft, ctx: Context) -> dict[tuple[str, str], dict[int, date]]:
    """When each flight first has each lesson part: the record, then this month."""
    order: dict[tuple[str, str], dict[int, date]] = {k: dict(v) for k, v in ctx.lessons.items()}
    for night in draft.nights:
        for slot in night.slots:
            if not (slot.subject_key and slot.part):
                continue
            for flight in slot_flights(slot, ctx.flights):
                seen = order.setdefault((flight, slot.subject_key), {})
                if slot.part not in seen or night.date < seen[slot.part]:
                    seen[slot.part] = night.date
    return order


# ── suggestions ─────────────────────────────────────────────────────────────

def _weeks(a: date, b: date) -> int:
    return abs((a - b).days) // 7


def suggest(draft: ProgrammeDraft, ctx: Context, bank: list[ProgrammeBankItem], *, per_cell: int = 3,
            only: tuple[int, int, str] | None = None) -> list[dict]:
    """What to put in each empty half for each flight, best first, each with
    the reason in words.

    A bank session scores for how long the flight has gone without it, for
    filling a kind of night the flight hasn't had this month, and most of all
    for being the next lesson in a subject the flight has started; it loses
    for already being in the month or coming before a lesson it follows.
    Joining another flight's session in the same half is offered too, which
    frees an instructor and a room."""
    cells = _cells(draft, ctx)
    lesson_order = _lesson_dates(draft, ctx)
    month_categories: dict[str, Counter] = defaultdict(Counter)
    month_titles: dict[str, set[str]] = defaultdict(set)
    for night in draft.nights:
        for slot in night.slots:
            for flight in slot_flights(slot, ctx.flights):
                month_categories[flight][slot.category] += 1
                month_titles[flight].add(slot.title.casefold())

    out = []
    for night in draft.nights:
        for period in PERIODS:
            for flight in ctx.flights:
                if only and only != (night.id, period, flight):
                    continue
                if cells.get((night.id, period, flight)):
                    continue
                busy_rooms = {s.room_id for s in night.slots if s.room_id and period in slot_periods(s)}
                options = []
                for item in bank:
                    if not item.active or item.whole_night:
                        continue
                    option = _score(item, flight, night.date, ctx, lesson_order, month_categories[flight],
                                    month_titles[flight])
                    if option is None:
                        continue
                    room = _free_room(ctx, busy_rooms, ctx.expected.get(flight, 0) + 1, item.room_id)
                    option.update({"kind": "bank", "bank_item_id": item.id, "title": item.title,
                                   "category": item.category, "room_id": room.id if room else None})
                    options.append(option)
                for slot in night.slots:
                    if period not in slot_periods(slot) or not slot.flights:
                        continue
                    room = ctx.rooms.get(slot.room_id) if slot.room_id else None
                    need = slot_headcount(slot, ctx.expected, ctx.flights) + ctx.expected.get(flight, 0)
                    if room and room.capacity and need > room.capacity:
                        continue
                    score = 6 + min(_since(ctx, flight, slot.title, night.date, draft), 26) / 2
                    if slot.title.casefold() in month_titles[flight]:
                        score -= 20
                    options.append({"kind": "join", "slot_id": slot.id, "title": slot.title,
                                    "category": slot.category, "room_id": slot.room_id, "score": round(score, 1),
                                    "reason": f"Join {who_label(slot.flights, ctx.flights)} for it: "
                                              "one instructor, one room"})
                options.sort(key=lambda o: (-o["score"], o["title"]))
                if options:
                    out.append({"night_id": night.id, "date": night.date.isoformat(), "period": period,
                                "flight": flight, "options": options[:per_cell]})
    return out


def _since(ctx: Context, flight: str, title: str, day: date, draft: ProgrammeDraft) -> int:
    """Weeks since the flight last did it before `day`; 26 if never or long ago."""
    dates = [d for d in ctx.history.get((flight, title.casefold()), []) if d < day]
    return min(_weeks(day, max(dates)), 26) if dates else 26


def _score(item: ProgrammeBankItem, flight: str, day: date, ctx: Context,
           lesson_order: dict, categories: Counter, titles: set[str]) -> dict | None:
    reasons: list[tuple[float, str]] = []
    score = 0.0
    key = item.title.casefold()
    if key in titles:
        # Drill most weeks is normal, so a repeat ranks low rather than never.
        score -= 30
        reasons.append((30, f"Already on {flight_label(flight)}'s programme this month"))
    dates = [d for d in ctx.history.get((flight, key), []) if d < day]
    if dates:
        weeks = _weeks(day, max(dates))
        score += min(weeks, 26)
        if weeks >= 8:
            reasons.append((min(weeks, 26), f"{flight_label(flight)} last did it {weeks} weeks ago"))
    else:
        score += 12
        reasons.append((5, f"Not on {flight_label(flight)}'s record yet"))
    if not categories.get(item.category):
        score += 8
        reasons.append((8, f"No {CATEGORIES.get(item.category, item.category).lower()} for "
                           f"{flight_label(flight)} this month yet"))
    elif categories[item.category] >= 2:
        score -= 4 * (categories[item.category] - 1)
    if item.subject_key and item.part:
        had = lesson_order.get((flight, item.subject_key), {})
        if item.part in had and had[item.part] <= day:
            return None
        if item.part > 1:
            before = had.get(item.part - 1)
            if before is None or before >= day:
                return None  # would come before the lesson it follows
            score += 25
            reasons.append((25, f"Follows lesson {item.part - 1} on {night_label(before)}"))
        else:
            score += 4
    reasons.sort(key=lambda r: -r[0])
    return {"score": round(score, 1), "reason": "; ".join(r for _, r in reasons[:2]) or "A good fit"}


# ── the record ──────────────────────────────────────────────────────────────

def record_nights(draft: ProgrammeDraft, ctx: Context) -> list[dict]:
    """The month as Programme_Nights rows: one session per flight per half, as
    the doc import writes them, so stats, training and check-in read a built
    month exactly as they read an imported one."""
    nights = []
    for night in draft.nights:
        cover: Counter = Counter()
        for slot in night.slots:
            for period in slot_periods(slot):
                for flight in slot_flights(slot, ctx.flights):
                    cover[(period, flight)] += 1
        sessions = []
        for slot in night.slots:
            names = [ctx.people[k].name for k in slot.people or []
                     if k in ctx.people and ctx.people[k].kind in ("staff", "nco")]
            instructor = (slot.instructor or "").strip() or ", ".join(names) or None
            for period in slot_periods(slot):
                for flight in slot_flights(slot, ctx.flights):
                    sessions.append({"period": period, "flight": flight, "activity": slot.title,
                                     "category": slot.category, "instructor": instructor,
                                     "split": cover[(period, flight)] > 1, "raw": f"builder:{slot.id}"})
        nights.append({"date": night.date, "uniform": night.uniform, "duty_nco": night.duty_nco,
                       "sessions": sessions})
    return nights
