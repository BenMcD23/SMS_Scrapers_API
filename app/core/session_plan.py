"""Plan one session: split the cadets into groups, rotate the groups through
the tasks in rounds, and (for leadership) pick who leads each run.

Kept free of the database so the rules can be tested on their own; the router
(routers/session_planner.py) gathers the inputs.

**Groups.** One group per stand the staff can run at once, never more than the
cadets can fill (a group needs at least the smallest task's minimum, or two
cadets). Cadets are dealt out like cards, most senior first and alternating
direction each pass, so every group gets a share of the NCOs and of each
flight.

**Rounds.** A round runs as long as the longest task in it, then the changeover,
and rounds keep going while the next one fits before the end time. In each
round every group gets a task it hasn't done tonight. Tasks in the same round
are all different (a task is one stand) and their kit added together must fit
in what the squadron has. A task that needs more kit than there is never runs.
When a group can't be given anything it sits the round out, and a warning
says why.

**Leaders** (leadership only). Each run has one leader from the group. Never
someone who has led that task before, tonight or on an earlier sheet (a failed
sheet counts: the form says to re-assess on another exercise). Among the rest:
whoever has led least tonight, then who holds Blue Leadership least (they still
need it), then fewest passes so far, then fewest leads ever. So everyone leads
once before anyone leads twice, and spare slots go to the cadets furthest from
the badge. If nobody in the group can lead that task, the run has no leader
and a warning says so.

When picking a task for a group, the planner prefers the one with the most
members who could still lead it, so leaders don't run out later in the night.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Most senior first. Ranks are stored the way the scrapers find them, so both
# the short and long forms are here.
_SENIORITY = {
    "cwo": 5, "cadet warrant officer": 5,
    "fs": 4, "flight sergeant": 4, "f/sgt": 4,
    "sgt": 3, "sergeant": 3,
    "cpl": 2, "corporal": 2,
    "lcpl": 1, "lance corporal": 1,
}


def seniority(rank: str | None) -> int:
    return _SENIORITY.get((rank or "").strip().lower(), 0)


@dataclass(frozen=True)
class PlanCadet:
    cin: int
    name: str
    rank: str | None
    flight: str | None
    # Leadership history, from the assessment sheets.
    led_tasks: frozenset[int] = frozenset()
    passes: int = 0
    has_blue: bool = False


@dataclass(frozen=True)
class PlanTask:
    id: int
    name: str
    number: str | None
    minutes: int
    min_group: int | None = None
    max_group: int | None = None
    kit: tuple[tuple[int, int], ...] = ()  # (kit_id, quantity)


@dataclass
class Run:
    group: int          # 1-based
    task_id: int
    leader: int | None  # cin


@dataclass
class Round:
    number: int
    start: datetime
    end: datetime
    runs: list[Run] = field(default_factory=list)
    idle: list[int] = field(default_factory=list)  # groups sitting it out


@dataclass
class Plan:
    groups: list[list[int]]   # cins per group
    rounds: list[Round]
    warnings: list[str]
    leads: dict[int, int]     # cin -> runs led tonight


def _listed(nums: list) -> str:
    """1, 2 and 4"""
    words = [str(n) for n in nums]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def make_groups(cadets: list[PlanCadet], stands: int, smallest: int) -> list[list[int]]:
    """Deal the cadets into at most ``stands`` groups of at least ``smallest``."""
    if not cadets:
        return []
    count = max(1, min(stands, len(cadets) // max(smallest, 1)))
    order = sorted(cadets, key=lambda c: (-seniority(c.rank), c.flight or "", c.name.lower(), c.cin))
    groups: list[list[int]] = [[] for _ in range(count)]
    for i, c in enumerate(order):
        lap, pos = divmod(i, count)
        groups[pos if lap % 2 == 0 else count - 1 - pos].append(c.cin)
    return groups


def _kit_fits(tasks: list[PlanTask], stock: dict[int, int]) -> bool:
    need: Counter[int] = Counter()
    for t in tasks:
        for kit_id, qty in t.kit:
            need[kit_id] += qty
    return all(qty <= stock.get(kit_id, 0) for kit_id, qty in need.items())


def plan_session(
    cadets: list[PlanCadet],
    tasks: list[PlanTask],
    stock: dict[int, int],
    start: datetime,
    end: datetime,
    stands: int,
    changeover: int = 0,
    leadership: bool = True,
) -> Plan:
    warnings: list[str] = []
    by_cin = {c.cin: c for c in cadets}

    runnable = [t for t in tasks if _kit_fits([t], stock)]
    for t in tasks:
        if t not in runnable:
            warnings.append(f"{t.name} needs more kit than the list has, so it isn't in the plan.")
    if not cadets:
        return Plan([], [], warnings + ["No cadets picked."], {})
    if not runnable:
        return Plan([], [], warnings + ["No tasks that can run."], {})
    if stands < 1:
        return Plan([], [], warnings + ["At least one stand is needed."], {})

    smallest = min((t.min_group or 2) for t in runnable)
    groups = make_groups(cadets, min(stands, len(runnable)), smallest)
    if len(groups) < stands:
        warnings.append(
            f"Only {len(groups)} group{'s' if len(groups) != 1 else ''} at once: "
            + ("there aren't enough tasks." if len(runnable) < stands else "there aren't enough cadets.")
        )

    done: dict[int, set[int]] = {g: set() for g in range(len(groups))}   # group -> tasks done tonight
    led_tonight: Counter[int] = Counter()
    led_ever: dict[int, set[int]] = {c.cin: set(c.led_tasks) for c in cadets}

    def could_lead(cin: int, task_id: int) -> bool:
        return task_id not in led_ever[cin]

    def leader_for(members: list[int], task_id: int) -> int | None:
        options = [m for m in members if could_lead(m, task_id)]
        if not options:
            return None
        return min(options, key=lambda m: (
            led_tonight[m], by_cin[m].has_blue, by_cin[m].passes, len(by_cin[m].led_tasks),
            by_cin[m].name.lower(), m,
        ))

    rounds: list[Round] = []
    clock = start
    used_tonight: Counter[int] = Counter()
    while True:
        chosen: list[tuple[int, PlanTask]] = []
        idle: list[int] = []
        # Groups with the fewest options choose first, so a group that can
        # only do one task isn't beaten to it.
        def options(g: int) -> list[PlanTask]:
            return [t for t in runnable if t.id not in done[g]]
        for g in sorted(range(len(groups)), key=lambda g: (len(options(g)), g)):
            taken = [t for _, t in chosen]
            fits = [t for t in options(g) if t not in taken and _kit_fits(taken + [t], stock)]
            if not fits:
                idle.append(g)
                continue
            members = groups[g]
            best = max(fits, key=lambda t: (
                sum(could_lead(m, t.id) for m in members) if leadership else 0,
                -used_tonight[t.id],
                -runnable.index(t),
            ))
            chosen.append((g, best))
        if not chosen:
            break
        length = max(t.minutes for _, t in chosen)
        finish = clock + timedelta(minutes=length)
        if finish > end:
            break
        rnd = Round(len(rounds) + 1, clock, finish, idle=sorted(g + 1 for g in idle))
        for g, t in sorted(chosen, key=lambda x: x[0]):
            done[g].add(t.id)
            used_tonight[t.id] += 1
            leader = None
            if leadership:
                leader = leader_for(groups[g], t.id)
                if leader is None:
                    warnings.append(
                        f"Round {rnd.number}, group {g + 1}: everyone in the group has led {t.name}, so nobody leads it."
                    )
                else:
                    led_tonight[leader] += 1
                    led_ever[leader].add(t.id)
            rnd.runs.append(Run(g + 1, t.id, leader))
        rounds.append(rnd)
        clock = finish + timedelta(minutes=changeover)

    if not rounds:
        warnings.append("Not enough time for a single round.")

    # One message per problem, not one per round.
    sat_out: dict[int, list[int]] = {}
    for rnd in rounds:
        for g in rnd.idle:
            sat_out.setdefault(g, []).append(rnd.number)
    for g, nums in sat_out.items():
        warnings.append(
            f"Group {g} sits out round{'s' if len(nums) > 1 else ''} {_listed(nums)}: "
            "nothing it hasn't done is free, or the kit's in use."
        )

    # Group size: one message for groups too big for the tasks they run, one
    # for too small, naming the tasks.
    sizes = [len(g) for g in groups]
    big, small = [], []
    for t in runnable:
        ran = {r.group for rnd in rounds for r in rnd.runs if r.task_id == t.id}
        if t.max_group and any(sizes[g - 1] > t.max_group for g in ran):
            big.append(t)
        if t.min_group and any(sizes[g - 1] < t.min_group for g in ran):
            small.append(t)
    span = f"{min(sizes)}" if min(sizes) == max(sizes) else f"{min(sizes)}–{max(sizes)}"
    if big:
        warnings.append(
            f"Groups of {span} are bigger than {_listed([t.name for t in big])} "
            f"suit{'s' if len(big) == 1 else ''} (up to {min(t.max_group for t in big)}). "
            "Add a stand or tick fewer cadets."
        )
    if small:
        warnings.append(
            f"Groups of {span} are smaller than {_listed([t.name for t in small])} "
            f"need{'s' if len(small) == 1 else ''} (at least {max(t.min_group for t in small)})."
        )

    if leadership and rounds:
        idle_leaders = [c for c in cadets if not led_tonight[c.cin]]
        if idle_leaders:
            warnings.append(
                f"{len(idle_leaders)} cadet{'s' if len(idle_leaders) != 1 else ''} won't lead tonight: "
                "add a round, a stand or a task."
            )

    return Plan(groups, rounds, warnings, dict(led_tonight))
