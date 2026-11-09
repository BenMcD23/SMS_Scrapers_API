"""The training night rules: who is in which session, how they're grouped,
where each cadet is with the lesson, and reading lesson numbers off programme
activity names."""

import pytest

from core import training_plan as tp
from core.training_plan import BEHIND, HAD, NEEDS, NightCadet, NightSession


def cadet(cin, flight="A", *, rank="Cadet", here=True, absent=False, had=None, name=None):
    return NightCadet(cin=cin, name=name or f"Cadet {cin:03d}", rank=rank, flight=flight, checked_in=here,
                      absent=absent, lessons={"nav": frozenset(had or ())})


def session(id=1, *, period=1, key="nav", part=2, flights=(), **kw):
    return NightSession(id=id, period=period, subject_key=key, part=part, flights=tuple(flights), **kw)


def statuses(plan):
    return plan.statuses


# ── where a cadet is with a lesson ────────────────────────────────────────────

@pytest.mark.parametrize("had, expected", [
    ({1, 2}, HAD),
    ({2}, HAD),        # had it, even if an earlier one is missing
    ({1}, NEEDS),
    (set(), BEHIND),   # lesson 2 without lesson 1
])
def test_a_cadet_has_had_needs_or_is_behind_on_a_lesson(had, expected):
    assert tp.lesson_status(cadet(1, had=had), "nav", 2) == expected


def test_the_first_lesson_of_a_subject_is_never_behind():
    assert tp.lesson_status(cadet(1), "nav", 1) == NEEDS
    assert tp.lesson_status(cadet(1), "a_subject_never_seen", 1) == NEEDS


# ── who is in a session ───────────────────────────────────────────────────────

def test_once_anyone_has_checked_in_auto_plans_with_the_door():
    cadets = [cadet(1), cadet(2, here=False)]
    [plan] = tp.plan_night([session(key=None, part=None)], cadets)
    assert plan.cins == [1]


def test_before_anyone_is_in_auto_plans_with_everyone_expected_but_the_booked_absent():
    cadets = [cadet(1, here=False), cadet(2, here=False, absent=True), cadet(3, here=False)]
    [plan] = tp.plan_night([session(key=None, part=None)], cadets)
    assert plan.cins == [1, 3]


def test_checked_in_only_with_nobody_in_says_so():
    [plan] = tp.plan_night([session(key=None, part=None, pool="checked_in")], [cadet(1, here=False)])
    assert plan.cins == [] and plan.groups == []
    assert plan.warnings == ["Nobody it's for has checked in yet."]


def test_expected_plans_with_the_roster_even_once_cadets_are_in():
    # Planning next period's cadets while some are still arriving.
    cadets = [cadet(1), cadet(2, here=False)]
    [plan] = tp.plan_night([session(key=None, part=None, pool="expected")], cadets)
    assert plan.cins == [1, 2]


def test_a_session_takes_only_its_flights_and_ignores_case_and_spaces():
    cadets = [cadet(1, "A"), cadet(2, " b "), cadet(3, "C"), cadet(4, None)]
    [plan] = tp.plan_night([session(key=None, part=None, flights=("A", "B"))], cadets)
    assert sorted(plan.cins) == [1, 2]


def test_no_flights_means_everyone_including_cadets_without_one():
    cadets = [cadet(1, "A"), cadet(4, None)]
    [plan] = tp.plan_night([session(key=None, part=None)], cadets)
    assert sorted(plan.cins) == [1, 4]


def test_only_who_needs_it_leaves_out_those_who_have_had_it_but_keeps_those_behind():
    cadets = [cadet(1, had={1}), cadet(2, had={1, 2}), cadet(3)]
    [plan] = tp.plan_night([session(include="need")], cadets)
    assert sorted(plan.cins) == [1, 3]
    assert plan.statuses == {1: NEEDS, 3: BEHIND}


def test_a_cadet_who_scanned_in_tonight_stays_in_the_need_only_session():
    # Their scan makes them "had", which would otherwise drop them out of the
    # lesson they're sitting in and into the other session in the period.
    scanned = NightCadet(cin=1, name="A", rank="Cadet", flight="A", checked_in=True,
                         lessons={"nav": frozenset({1, 2})}, tonight=frozenset({("nav", 2)}))
    earlier = NightCadet(cin=2, name="B", rank="Cadet", flight="A", checked_in=True,
                         lessons={"nav": frozenset({1, 2})}, tonight=frozenset({("nav", 1)}))
    drill, nav = tp.plan_night([session(1, key=None, part=None), session(2, include="need")], [scanned, earlier])
    assert nav.cins == [1] and nav.statuses == {1: HAD}
    assert drill.cins == [2]


def test_sessions_in_a_period_share_cadets_with_need_only_ones_picking_first():
    # The lesson is listed second but takes its cadets first, so "Map Reading
    # for those who need it, Drill for the rest" splits the flight by itself.
    cadets = [cadet(1, had={1}), cadet(2, had={1, 2}), cadet(3, had={1})]
    drill = session(1, key=None, part=None)
    nav = session(2, include="need")
    drill_plan, nav_plan = tp.plan_night([drill, nav], cadets)
    assert sorted(nav_plan.cins) == [1, 3]
    assert drill_plan.cins == [2]


def test_without_need_only_the_first_listed_session_in_a_period_takes_everyone():
    cadets = [cadet(1), cadet(2)]
    first, second = tp.plan_night([session(1, key=None, part=None), session(2, key=None, part=None)], cadets)
    assert sorted(first.cins) == [1, 2] and second.cins == []
    assert second.warnings  # nobody left for it says so rather than showing an empty box


def test_different_periods_and_free_standing_sessions_each_get_everyone():
    cadets = [cadet(1), cadet(2)]
    plans = tp.plan_night([
        session(1, period=1, key=None, part=None),
        session(2, period=2, key=None, part=None),
        session(3, period=None, key=None, part=None),
        session(4, period=None, key=None, part=None),
    ], cadets)
    assert [sorted(p.cins) for p in plans] == [[1, 2]] * 4


def test_sessions_come_back_in_the_order_given():
    plans = tp.plan_night([session(7, key=None, part=None), session(3, include="need")], [cadet(1)])
    assert [p.session_id for p in plans] == [7, 3]


def test_a_session_without_a_lesson_has_no_statuses():
    [plan] = tp.plan_night([session(key=None, part=None)], [cadet(1)])
    assert plan.statuses == {}


def test_an_empty_roster_plans_nothing_without_failing():
    [plan] = tp.plan_night([session()], [])
    assert plan.cins == [] and plan.groups == [] and plan.warnings


# ── grouping ──────────────────────────────────────────────────────────────────

def labels(plan):
    return [(g.label, len(g.cins)) for g in plan.groups]


def test_one_group_holds_everyone_most_senior_first():
    cadets = [cadet(1, rank="Cadet"), cadet(2, rank="Sgt"), cadet(3, rank="Cpl")]
    [plan] = tp.plan_night([session(key=None, part=None)], cadets)
    assert [(g.label, g.cins) for g in plan.groups] == [("Everyone", [2, 3, 1])]


def test_by_flight_makes_a_group_per_flight_with_no_flight_last():
    cadets = [cadet(1, "B"), cadet(2, "A"), cadet(3, None), cadet(4, "NCO"), cadet(5, "a")]
    [plan] = tp.plan_night([session(key=None, part=None, grouping="flight")], cadets)
    assert labels(plan) == [("NCO", 1), ("A Flight", 2), ("B Flight", 1), ("No flight", 1)]


def test_by_need_groups_needs_then_behind_then_had_and_skips_empty_ones():
    cadets = [cadet(1, had={1}), cadet(2, had={1, 2}), cadet(3, had={1})]
    [plan] = tp.plan_night([session(grouping="need")], cadets)
    assert labels(plan) == [("Needs this lesson", 2), ("Already had it", 1)]


def test_by_need_without_a_lesson_falls_back_to_one_group_and_warns():
    [plan] = tp.plan_night([session(key=None, part=None, grouping="need")], [cadet(1), cadet(2)])
    assert labels(plan) == [("Everyone", 2)]
    assert any("takes a lesson" in w for w in plan.warnings)


def test_mixed_deals_seniors_and_flights_across_the_groups():
    cadets = ([cadet(i, "A") for i in range(1, 5)] + [cadet(i, "B") for i in range(5, 9)]
              + [cadet(9, "A", rank="Sgt"), cadet(10, "B", rank="Cpl")])
    [plan] = tp.plan_night([session(key=None, part=None, grouping="mixed", groups=2)], cadets)
    assert labels(plan) == [("Group 1", 5), ("Group 2", 5)]
    first, second = (set(g.cins) for g in plan.groups)
    assert 9 in first and 10 in second  # one NCO each
    for g in (first, second):
        assert {c.flight for c in cadets if c.cin in g} == {"A", "B"}


def test_mixed_defaults_to_two_groups_and_never_makes_more_groups_than_cadets():
    [plan] = tp.plan_night([session(key=None, part=None, grouping="mixed")], [cadet(1), cadet(2), cadet(3)])
    assert len(plan.groups) == 2
    [plan] = tp.plan_night([session(key=None, part=None, grouping="mixed", groups=5)], [cadet(1), cadet(2)])
    assert len(plan.groups) == 2


def test_mixed_makes_more_groups_when_the_maximum_size_needs_them():
    cadets = [cadet(i) for i in range(1, 11)]
    [plan] = tp.plan_night([session(key=None, part=None, grouping="mixed", groups=2, max_group=3)], cadets)
    assert len(plan.groups) == 4 and max(len(g.cins) for g in plan.groups) <= 3


def test_a_group_over_the_maximum_is_split_evenly_and_numbered():
    cadets = [cadet(i, had={1}) for i in range(1, 10)]
    [plan] = tp.plan_night([session(grouping="need", max_group=4)], cadets)
    assert labels(plan) == [("Needs this lesson (1)", 3), ("Needs this lesson (2)", 3),
                            ("Needs this lesson (3)", 3)]


def test_a_flight_over_the_maximum_is_split_within_the_flight():
    cadets = [cadet(i, "A") for i in range(1, 6)] + [cadet(9, "B")]
    [plan] = tp.plan_night([session(key=None, part=None, grouping="flight", max_group=3)], cadets)
    assert labels(plan) == [("A Flight (1)", 3), ("A Flight (2)", 2), ("B Flight", 1)]


def test_non_ascii_names_sort_without_trouble():
    cadets = [cadet(1, name="Zoë Ørsted"), cadet(2, name="Ánna Ünal")]
    [plan] = tp.plan_night([session(key=None, part=None)], cadets)
    assert sorted(plan.cins) == [1, 2]


# ── programme names ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("Map Reading 2", ("Map Reading", 2)),
    ("IET Pt 1", ("IET", 1)),
    ("IET pt.3", ("IET", 3)),
    ("First Aid part 3", ("First Aid", 3)),
    ("Radio (2)", ("Radio", 2)),
    ("Flying Ops - 4", ("Flying Ops", 4)),
    ("History of the RAF", ("History of the RAF", 1)),
    ("No.2 SD Inspection", ("No.2 SD Inspection", 1)),
    ("Exercise 2026", ("Exercise 2026", 1)),  # a year isn't a lesson number...
    ("Ex 2009", ("Ex 2009", 1)),              # ...not even its last two digits
    ("  Map   Reading 2 ", ("Map Reading", 2)),
    ("2", ("2", 1)),                            # nothing left once the number's gone
])
def test_the_lesson_number_is_read_off_the_end_of_a_name(name, expected):
    assert tp.split_part(name) == expected


SUBJECTS = {"nav": ("Basic Navigation", 3), "custom_map_reading": ("Map Reading", 2), "pof": ("Principles of Flight", 3)}


def test_rules_link_an_activity_to_the_subject_of_the_same_name_ignoring_case_and_punctuation():
    s = tp.suggest_by_rules("map-reading 2", "fieldcraft", SUBJECTS)
    assert (s.subject_key, s.new_subject, s.part) == ("custom_map_reading", None, 2)


def test_rules_suggest_a_new_subject_for_a_lesson_like_activity_that_matches_nothing():
    s = tp.suggest_by_rules("Flying Ops 1", "lessons", SUBJECTS)
    assert (s.subject_key, s.new_subject, s.part) == (None, "Flying Ops", 1)


def test_rules_dont_link_a_lesson_number_the_subject_hasnt_got():
    s = tp.suggest_by_rules("Map Reading 3", "fieldcraft", SUBJECTS)
    assert s.subject_key is None and s.new_subject == "Map Reading"


@pytest.mark.parametrize("name, category", [
    ("Drill", "drill"), ("Volleyball", "sport"), ("Halloween Extravaganza", "social"),
    ("Flight Time", "admin"), ("Classifications (POF)", "classifications"),
])
def test_rules_call_drill_sport_socials_admin_and_classification_nights_not_lessons(name, category):
    assert tp.suggest_by_rules(name, category, SUBJECTS) == tp.LinkSuggestion(None, None, None)
