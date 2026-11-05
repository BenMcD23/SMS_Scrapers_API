"""The session planner's rules: groups, rounds, kit, and who leads."""

from collections import Counter
from datetime import datetime

from core.session_plan import PlanCadet, PlanTask, make_groups, plan_session, seniority

START = datetime(2026, 11, 9, 19, 0)


def at(h, m=0):
    return datetime(2026, 11, 9, h, m)


def cadet(cin, rank="Cadet", flight="A", **over):
    return PlanCadet(cin=cin, name=f"Cadet {cin:02d}", rank=rank, flight=flight, **over)


def task(tid, minutes=15, kit=(), **over):
    return PlanTask(id=tid, name=f"Task {tid}", number=str(tid), minutes=minutes, kit=tuple(kit), **over)


def every_run(plan):
    return [(rnd.number, run) for rnd in plan.rounds for run in rnd.runs]


# ── groups ────────────────────────────────────────────────────────────────────

def test_ranks_are_read_in_either_spelling():
    assert seniority("Sgt") == seniority("sergeant") == seniority(" SERGEANT ") == 3
    assert seniority("CWO") > seniority("FS") > seniority("Cpl") > seniority("Cadet") == seniority(None) == 0


def test_ncos_and_flights_are_spread_across_the_groups():
    cadets = [cadet(1, "Sgt", "A"), cadet(2, "Sgt", "B"), cadet(3, "Cpl", "A")] + [
        cadet(10 + i, "Cadet", "ABC"[i % 3]) for i in range(9)
    ]
    groups = make_groups(cadets, stands=3, smallest=2)
    assert sorted(len(g) for g in groups) == [4, 4, 4]
    # The three NCOs are dealt one per group.
    assert [sum(cin in (1, 2, 3) for cin in g) for g in groups] == [1, 1, 1]
    # Every cadet is in exactly one group.
    assert sorted(cin for g in groups for cin in g) == sorted(c.cin for c in cadets)


def test_there_are_never_more_groups_than_the_cadets_can_fill():
    assert len(make_groups([cadet(i) for i in range(5)], stands=4, smallest=2)) == 2
    assert len(make_groups([cadet(1)], stands=4, smallest=2)) == 1
    assert make_groups([], stands=3, smallest=2) == []


# ── rounds ────────────────────────────────────────────────────────────────────

def test_rounds_fill_the_time_and_no_group_repeats_a_task():
    cadets = [cadet(i) for i in range(1, 13)]
    tasks = [task(t) for t in range(1, 5)]
    plan = plan_session(cadets, tasks, {}, START, at(20, 30), stands=3, changeover=5)

    # 15 min + 5 changeover: 19:00, 19:20, 19:40, 20:00 — 20:20 would end at 20:35.
    assert [(r.start, r.end) for r in plan.rounds] == [
        (at(19), at(19, 15)), (at(19, 20), at(19, 35)), (at(19, 40), at(19, 55)), (at(20), at(20, 15)),
    ]
    for g in (1, 2, 3):
        mine = [run.task_id for _, run in every_run(plan) if run.group == g]
        assert len(mine) == len(set(mine))
    for rnd in plan.rounds:
        ids = [run.task_id for run in rnd.runs]
        assert len(ids) == len(set(ids)), "one stand per task in a round"


def test_a_round_lasts_as_long_as_its_longest_task():
    plan = plan_session([cadet(i) for i in range(4)], [task(1, 10), task(2, 25)], {}, START, at(19, 30), stands=2)
    assert plan.rounds[0].end == at(19, 25)
    assert len(plan.rounds) == 1


def test_tasks_only_run_side_by_side_when_their_kit_fits():
    # Two tasks both need the one cargo net: never in the same round.
    tasks = [task(1, kit=[(7, 1)]), task(2, kit=[(7, 1)]), task(3)]
    plan = plan_session([cadet(i) for i in range(6)], tasks, {7: 1}, START, at(21), stands=3)
    for rnd in plan.rounds:
        assert not {1, 2} <= {run.task_id for run in rnd.runs}
    assert {run.task_id for _, run in every_run(plan)} == {1, 2, 3}


def test_kit_is_added_up_across_the_round():
    # 4 ropes: two tasks needing 2 each can share a round, a third can't join.
    tasks = [task(t, kit=[(5, 2)]) for t in (1, 2, 3)]
    plan = plan_session([cadet(i) for i in range(6)], tasks, {5: 4}, START, at(19, 15), stands=3)
    assert len(plan.rounds[0].runs) == 2 and plan.rounds[0].idle == [3]
    assert any("Group 3 sits out round 1" in w for w in plan.warnings)


def test_a_task_needing_more_kit_than_there_is_never_runs():
    tasks = [task(1, kit=[(9, 2)]), task(2)]
    plan = plan_session([cadet(i) for i in range(4)], tasks, {9: 1}, START, at(21), stands=2)
    assert {run.task_id for _, run in every_run(plan)} == {2}
    assert "Task 1 needs more kit than the list has, so it isn't in the plan." in plan.warnings


def test_no_time_for_a_round_says_so():
    plan = plan_session([cadet(i) for i in range(4)], [task(1, 30)], {}, START, at(19, 20), stands=1)
    assert plan.rounds == [] and "Not enough time for a single round." in plan.warnings


def test_nothing_to_plan_with_says_why():
    assert plan_session([], [task(1)], {}, START, at(21), stands=1).warnings == ["No cadets picked."]
    assert plan_session([cadet(1)], [], {}, START, at(21), stands=1).warnings == ["No tasks that can run."]


def test_fewer_tasks_than_stands_means_fewer_groups():
    plan = plan_session([cadet(i) for i in range(12)], [task(1), task(2)], {}, START, at(21), stands=4)
    assert len(plan.groups) == 2
    assert "Only 2 groups at once: there aren't enough tasks." in plan.warnings


def test_groups_bigger_than_a_task_suits_get_one_warning():
    tasks = [task(1, max_group=4), task(2, max_group=5)]
    plan = plan_session([cadet(i) for i in range(14)], tasks, {}, START, at(21), stands=2)
    assert [w for w in plan.warnings if "bigger" in w] == [
        "Groups of 7 are bigger than Task 1 and Task 2 suit (up to 4). Add a stand or tick fewer cadets."
    ]


# ── leaders ───────────────────────────────────────────────────────────────────

def test_nobody_leads_a_task_they_have_led_before():
    # Cadet 1 has led task 1 (even if they failed it); cadet 2 has led task 2.
    cadets = [cadet(1, led_tasks=frozenset({1})), cadet(2, led_tasks=frozenset({2}))]
    plan = plan_session(cadets, [task(1), task(2)], {}, START, at(21), stands=1)
    led = {(run.task_id, run.leader) for _, run in every_run(plan)}
    assert led == {(1, 2), (2, 1)}


def test_everyone_leads_once_before_anyone_leads_twice():
    cadets = [cadet(i) for i in range(1, 5)]
    plan = plan_session(cadets, [task(t) for t in range(1, 7)], {}, START, at(21), stands=1)
    leaders = [run.leader for _, run in every_run(plan)]
    assert len(leaders) == 6
    assert set(leaders[:4]) == {1, 2, 3, 4}
    assert max(Counter(leaders).values()) == 2


def test_spare_slots_go_to_cadets_furthest_from_the_badge():
    cadets = [
        cadet(1, has_blue=True),
        cadet(2, passes=1),
        cadet(3, passes=0),
    ]
    plan = plan_session(cadets, [task(1)], {}, START, at(21), stands=1)
    assert plan.rounds[0].runs[0].leader == 3


def test_a_group_where_everyone_has_led_the_task_runs_it_with_no_leader():
    cadets = [cadet(1, led_tasks=frozenset({1})), cadet(2, led_tasks=frozenset({1}))]
    plan = plan_session(cadets, [task(1)], {}, START, at(21), stands=1)
    assert plan.rounds[0].runs[0].leader is None
    assert any("everyone in the group has led Task 1" in w for w in plan.warnings)


def test_a_group_is_given_the_task_its_members_can_still_lead():
    # Both cadets have led task 1; task 2 is new to them, so it comes first.
    cadets = [cadet(1, led_tasks=frozenset({1})), cadet(2, led_tasks=frozenset({1}))]
    plan = plan_session(cadets, [task(1), task(2)], {}, START, at(19, 15), stands=1)
    assert plan.rounds[0].runs[0].task_id == 2


def test_cadets_left_without_a_lead_are_counted():
    plan = plan_session([cadet(i) for i in range(6)], [task(1)], {}, START, at(21), stands=1)
    assert "5 cadets won't lead tonight: add a round, a stand or a task." in plan.warnings
    assert sum(plan.leads.values()) == 1


def test_a_session_that_isnt_leadership_has_no_leaders():
    plan = plan_session([cadet(i) for i in range(4)], [task(1), task(2)], {}, START, at(21),
                        stands=2, leadership=False)
    assert all(run.leader is None for _, run in every_run(plan))
    assert not any("lead" in w for w in plan.warnings)


def test_the_same_inputs_give_the_same_plan():
    args = ([cadet(i, flight="AB"[i % 2]) for i in range(10)], [task(t) for t in range(1, 5)], {}, START, at(21))
    a, b = plan_session(*args, stands=3), plan_session(*args, stands=3)
    assert a.groups == b.groups and [r.runs for r in a.rounds] == [r.runs for r in b.rounds]
