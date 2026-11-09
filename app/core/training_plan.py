"""The rules for a training night: who goes in which session, how each session
splits into groups, and where each cadet is with its lesson.

Kept free of the database so the rules can be tested on their own; the router
(routers/training.py) gathers the inputs.

A night is a list of sessions staff set up, each with its own inputs: the
lesson it teaches (a subject and lesson number from the theory catalogue, or
none for drill, sport and the like), the flights it's for, who to plan with,
whether it takes everyone or only those who still need the lesson, and how to
group them.

**Who's in the pool.** ``checked_in`` is the cadets who've scanned in at the
door tonight (routers/check_ins.py). ``expected`` is everyone in the flights
without a booked absence, for planning ahead. ``auto`` is checked-in once
anyone has, expected before that, so the same plan works the week before and
on the night.

**One cadet, one session per period.** Sessions in the same period share
their cadets out: those that take only who needs the lesson pick first, in the
order they're listed, then the rest take whoever is left. So "Map Reading 2
for those who haven't had it, Drill for everyone else" splits a flight by
itself. A session with no period stands alone and takes everyone it matches.

**Where a cadet is with the lesson.** ``had`` (they've a record of this
lesson), ``needs`` (they haven't, and have had every lesson before it), or
``behind`` (they're missing an earlier lesson of the subject). Records come
from the lesson QR codes, so a cadet who scans tonight moves to ``had`` the
next time the page loads, and stays in the session they scanned in to even if
it takes only those who need it.

**Grouping.** ``one`` group; a group per ``flight``; by ``need`` (needs it,
behind, had it: so whoever is behind can be given the earlier lesson and
whoever has had it can help or do something else); or ``mixed`` into a set
number of groups, dealt most senior first so each gets a share of the NCOs
and flights. Any group over the maximum size is dealt into as many smaller
groups as it takes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.session_plan import seniority

POOLS = ("auto", "checked_in", "expected")
INCLUDES = ("all", "need")
GROUPINGS = ("one", "flight", "need", "mixed")

HAD, NEEDS, BEHIND = "had", "needs", "behind"
# The order of the groups "by need" makes, and their names.
NEED_GROUPS: tuple[tuple[str, str], ...] = (
    (NEEDS, "Needs this lesson"),
    (BEHIND, "Missing an earlier lesson"),
    (HAD, "Already had it"),
)


@dataclass(frozen=True)
class NightCadet:
    cin: int
    name: str
    rank: str | None
    flight: str | None
    checked_in: bool
    absent: bool = False  # a booked absence for the night
    # {subject key: lesson numbers had}
    lessons: dict[str, frozenset[int]] = field(default_factory=dict)
    # (subject key, lesson) recorded on this night: they had it here, tonight.
    tonight: frozenset[tuple[str, int]] = frozenset()


@dataclass(frozen=True)
class NightSession:
    id: int
    period: int | None
    subject_key: str | None
    part: int | None
    flights: tuple[str, ...]  # upper-case letters; empty is every flight
    pool: str = "auto"
    include: str = "all"
    grouping: str = "one"
    groups: int | None = None
    max_group: int | None = None


@dataclass
class Group:
    label: str
    cins: list[int]


@dataclass
class SessionPlan:
    session_id: int
    cins: list[int]                 # everyone in the session
    statuses: dict[int, str]        # cin -> had / needs / behind (lesson sessions only)
    groups: list[Group]
    warnings: list[str]


def flight_of(cadet: NightCadet) -> str:
    return (cadet.flight or "").strip().upper()


def lesson_status(cadet: NightCadet, subject_key: str, part: int) -> str:
    had = cadet.lessons.get(subject_key, frozenset())
    if part in had:
        return HAD
    if any(p not in had for p in range(1, part)):
        return BEHIND
    return NEEDS


def use_checked_in(pool: str, anyone_checked_in: bool) -> bool:
    return pool == "checked_in" or (pool == "auto" and anyone_checked_in)


def _in_pool(cadet: NightCadet, session: NightSession, checked_in_only: bool) -> bool:
    if session.flights and flight_of(cadet) not in session.flights:
        return False
    return cadet.checked_in if checked_in_only else not cadet.absent


def _order(cadets: list[NightCadet]) -> list[NightCadet]:
    return sorted(cadets, key=lambda c: (-seniority(c.rank), flight_of(c), c.name.lower(), c.cin))


def deal(cadets: list[NightCadet], count: int) -> list[list[int]]:
    """Deal into ``count`` groups, most senior first and back and forth, so
    every group gets a share of the NCOs and of each flight."""
    count = max(1, min(count, len(cadets)))
    groups: list[list[int]] = [[] for _ in range(count)]
    for i, c in enumerate(_order(cadets)):
        lap, pos = divmod(i, count)
        groups[pos if lap % 2 == 0 else count - 1 - pos].append(c.cin)
    return groups


def _cap(label: str, cadets: list[NightCadet], max_group: int | None) -> list[Group]:
    """One group, or as few as keep each within ``max_group``."""
    if not cadets:
        return []
    if not max_group or len(cadets) <= max_group:
        return [Group(label, [c.cin for c in _order(cadets)])]
    count = -(-len(cadets) // max_group)
    return [Group(f"{label} ({i + 1})", g) for i, g in enumerate(deal(cadets, count))]


def make_groups(session: NightSession, cadets: list[NightCadet], statuses: dict[int, str]) -> list[Group]:
    if not cadets:
        return []
    if session.grouping == "flight":
        flights = sorted({flight_of(c) for c in cadets}, key=lambda f: (f == "", f != "NCO", f))
        out: list[Group] = []
        for f in flights:
            label = f"{f} Flight" if len(f) == 1 else (f or "No flight")
            out += _cap(label, [c for c in cadets if flight_of(c) == f], session.max_group)
        return out
    if session.grouping == "need" and statuses:
        out = []
        for status, label in NEED_GROUPS:
            out += _cap(label, [c for c in cadets if statuses.get(c.cin) == status], session.max_group)
        return out
    if session.grouping == "mixed":
        count = session.groups or 2
        if session.max_group:
            count = max(count, -(-len(cadets) // session.max_group))
        return [Group(f"Group {i + 1}", g) for i, g in enumerate(deal(cadets, count))]
    return _cap("Everyone", cadets, session.max_group)


def plan_night(sessions: list[NightSession], cadets: list[NightCadet]) -> list[SessionPlan]:
    """A plan for each session, in the order given."""
    anyone_in = any(c.checked_in for c in cadets)
    taken: dict[int, set[int]] = {}  # period -> cins already in a session
    plans: dict[int, SessionPlan] = {}

    # Need-only sessions pick first within their period; otherwise listed order.
    order = sorted(range(len(sessions)), key=lambda i: (sessions[i].include != "need", i))
    for i in order:
        s = sessions[i]
        checked_only = use_checked_in(s.pool, anyone_in)
        lesson = s.subject_key is not None and s.part is not None
        used = taken.setdefault(s.period, set()) if s.period is not None else set()
        matching = [c for c in cadets if _in_pool(c, s, checked_only)]
        pool = [c for c in matching if c.cin not in used]
        statuses = {c.cin: lesson_status(c, s.subject_key, s.part) for c in pool} if lesson else {}
        if lesson and s.include == "need":
            # A cadet who scanned in during this lesson has had it now, but
            # it's still the session they're in: they mustn't jump to the
            # other one on the next refresh.
            pool = [c for c in pool if statuses[c.cin] != HAD or (s.subject_key, s.part) in c.tonight]
            statuses = {c.cin: statuses[c.cin] for c in pool}
        if s.period is not None:
            used.update(c.cin for c in pool)

        warnings: list[str] = []
        if not pool:
            # Say which "nobody": staff fix each differently.
            if any(c.cin in used for c in matching):
                warnings.append("Everyone it's for is in another session this period.")
            elif matching and lesson and s.include == "need":
                warnings.append("Everyone it's for has had this lesson.")
            elif checked_only:
                warnings.append("Nobody it's for has checked in yet.")
            else:
                warnings.append("Nobody it's for is expected.")
        if s.grouping == "need" and not lesson:
            warnings.append("Grouping by need takes a lesson: pick one, or group another way.")
        plans[i] = SessionPlan(
            session_id=s.id,
            cins=[c.cin for c in _order(pool)],
            statuses=statuses,
            groups=make_groups(s, pool, statuses),
            warnings=warnings,
        )
    return [plans[i] for i in range(len(sessions))]


# ── linking programme activities to lessons ───────────────────────────────────

# "Map Reading 2", "IET Pt 1", "First Aid part 3", "Radio (2)".
_NUMBERED = re.compile(r"^(?P<base>.*?)[\s:-]*(?:\(|\b(?:pt|part|lesson)\.?\s*)?(?<!\d)(?P<part>\d{1,2})\)?$", re.IGNORECASE)
# Categories whose activities are taught as lessons; the rest (drill, sport,
# socials, admin) are things a flight does, not lessons a cadet can miss.
LESSON_CATEGORIES = frozenset({"lessons", "fieldcraft", "first_aid", "stem", "classifications"})


def split_part(name: str) -> tuple[str, int]:
    """("Map Reading", 2) from "Map Reading 2"; a name without a number is lesson 1."""
    m = _NUMBERED.match(" ".join(name.split()))
    if m and m.group("base").strip():
        part = int(m.group("part"))
        if 1 <= part <= 10:
            return m.group("base").strip(" -:"), part
    return " ".join(name.split()), 1


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass(frozen=True)
class LinkSuggestion:
    subject_key: str | None   # an existing subject
    new_subject: str | None   # a subject to add
    part: int | None


def suggest_by_rules(activity: str, category: str, subjects: dict[str, tuple[str, int]]) -> LinkSuggestion:
    """A guess without a model. ``subjects`` is {key: (name, parts)}.

    An activity whose name (less its number) matches a subject's is that
    subject's lesson; a lesson-like one that matches nothing is a new subject;
    anything else isn't a lesson."""
    base, part = split_part(activity)
    want = _norm(base)
    for key, (name, parts) in subjects.items():
        if _norm(name) == want and part <= max(parts, 1):
            return LinkSuggestion(key, None, part)
    if category in LESSON_CATEGORIES and category != "classifications":
        return LinkSuggestion(None, base, part)
    return LinkSuggestion(None, None, None)
