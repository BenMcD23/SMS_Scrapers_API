"""Classification day rules: which lesson or exam each cadet gets, and when their
attendance earns a flag."""

from datetime import date, timedelta

import pytest

from core import classification_plan as plan

NAV, POF, AIRMANSHIP = "acp_32_2", "acp_33_2", "acp_34_2"
LEADING = [NAV, POF, AIRMANSHIP]
SENIOR = [s.key for s in plan.SENIOR_SUBJECTS]
JEP = "jet_engine_propulsion"

# A Wednesday, as parade nights are.
WED = date(2026, 10, 14)


# ── Assignment ────────────────────────────────────────────────────────────────

def test_a_first_class_cadet_starts_on_the_first_leading_subject():
    p = plan.plan_cadet("First Class Cadet", taught=set(), portal_passed=set())
    assert (p["kind"], p["lesson_key"]) == (plan.LESSON, NAV)


def test_a_lesson_taught_but_not_examined_puts_the_cadet_on_its_exam():
    p = plan.plan_cadet("First Class Cadet", taught={NAV}, portal_passed=set())
    assert (p["kind"], p["lesson_key"], p["ready_exams"]) == (plan.EXAM, NAV, [NAV])


def test_an_exam_comes_before_a_new_lesson_even_when_its_subject_is_not_running():
    p = plan.plan_cadet("First Class Cadet", taught={POF}, portal_passed=set(), offered=[JEP])
    assert (p["kind"], p["lesson_key"]) == (plan.EXAM, POF)


def test_a_passed_exam_moves_the_cadet_to_the_next_subject():
    p = plan.plan_cadet("First Class Cadet", taught={NAV}, portal_passed={NAV})
    assert (p["kind"], p["lesson_key"], p["ready_exams"]) == (plan.LESSON, POF, [])


def test_a_lesson_taught_is_not_taught_again_while_its_exam_is_outstanding():
    # Two taught, one examined: sit the other exam rather than restart anything.
    p = plan.plan_cadet("First Class Cadet", taught={NAV, POF}, portal_passed={NAV})
    assert (p["kind"], p["lesson_key"]) == (plan.EXAM, POF)


def test_a_leading_cadet_has_passed_the_leading_exams_without_the_portal_saying_so():
    p = plan.plan_cadet("Leading Cadet", taught={NAV}, portal_passed=set())
    assert (p["kind"], p["lesson_key"]) == (plan.LESSON, SENIOR[0])
    assert p["ready_exams"] == []


def test_senior_cadets_carry_on_with_the_senior_master_subjects():
    p = plan.plan_cadet("Senior Cadet", taught=set(), portal_passed=SENIOR[:5])
    assert (p["kind"], p["lesson_key"]) == (plan.LESSON, SENIOR[5])


@pytest.mark.parametrize("classification", [None, "", "Second Class Cadet", "Something new"])
def test_cadets_below_first_class_are_not_given_a_theory_subject(classification):
    p = plan.plan_cadet(classification, taught=set(), portal_passed=set())
    assert (p["kind"], p["lesson_key"]) == (plan.FIRST_CLASS_TRAINING, None)


def test_a_master_air_cadet_is_complete():
    p = plan.plan_cadet("Master Air Cadet", taught=set(), portal_passed=set())
    assert (p["kind"], p["lesson_key"], p["awaiting"]) == (plan.COMPLETE, None, None)


def test_all_leading_exams_passed_without_the_classification_flags_it_and_moves_on():
    p = plan.plan_cadet("First Class Cadet", taught=set(), portal_passed=set(LEADING))
    assert p["awaiting"] == "Leading Cadet"
    assert (p["kind"], p["lesson_key"]) == (plan.LESSON, SENIOR[0])


def test_every_senior_subject_passed_awaits_master():
    p = plan.plan_cadet("Senior Cadet", taught=set(), portal_passed=set(SENIOR))
    assert (p["kind"], p["awaiting"]) == (plan.COMPLETE, "Master Air Cadet")


def test_only_offered_lessons_are_assigned():
    p = plan.plan_cadet("Leading Cadet", taught=set(), portal_passed=set(), offered=[JEP])
    assert (p["kind"], p["lesson_key"]) == (plan.LESSON, JEP)


def test_a_cadet_whose_remaining_subjects_are_not_running_is_left_unassigned():
    # First Class with nothing done: JEP is a Senior subject, so it isn't theirs.
    p = plan.plan_cadet("First Class Cadet", taught=set(), portal_passed=set(), offered=[JEP])
    assert (p["kind"], p["lesson_key"]) == (plan.NOT_OFFERED, NAV)


def test_an_offered_lesson_already_passed_is_skipped():
    p = plan.plan_cadet("Leading Cadet", taught=set(), portal_passed={JEP}, offered=[JEP, "rocketry"])
    assert p["lesson_key"] == "rocketry"


def test_badge_theory_marks_do_not_count_as_classification_lessons():
    p = plan.plan_cadet("First Class Cadet", taught={"blue_radio"}, portal_passed={"blue_radio"})
    assert (p["kind"], p["lesson_key"], p["ready_exams"]) == (plan.LESSON, NAV, [])


# ── Attendance ────────────────────────────────────────────────────────────────

IN, OUT = "Present Correctly Dressed", "Absent"


def _nights(pattern: dict[int, list[str]], weeks: int = 10, before: date = WED):
    """Parade nights each week before ``before``: {days_before_wednesday: statuses}."""
    out = []
    for w in range(1, weeks + 1):
        wed = before - timedelta(weeks=w)
        for offset, statuses in pattern.items():
            out.append((wed + timedelta(days=offset), statuses[(w - 1) % len(statuses)]))
    return out


def _codes(outlook):
    return [f["code"] for f in outlook["flags"]]


def test_a_regular_cadet_gets_no_flags():
    o = plan.attendance_outlook(_nights({0: [IN], 2: [IN]}), WED)
    assert (o["nights"], o["present"], o["likelihood"], o["flags"]) == (20, 20, 1.0, [])


def test_a_cadet_who_skips_wednesdays_is_flagged_for_a_wednesday_only():
    records = _nights({0: [OUT], 2: [IN]})   # Fridays only
    wed = plan.attendance_outlook(records, WED)
    assert _codes(wed) == ["weekday"]
    assert wed["flags"][0]["message"] == "Rarely in on Wednesdays: 0 of 10 (0%)"
    assert wed["likelihood"] == 0.0

    fri = plan.attendance_outlook(records, WED + timedelta(days=2))
    assert "weekday" not in _codes(fri)
    assert fri["likelihood"] == 1.0


def test_low_attendance_on_every_night_is_one_flag_not_two():
    o = plan.attendance_outlook(_nights({0: [IN, OUT, OUT], 2: [IN, OUT, OUT]}), WED)
    assert _codes(o) == ["low_attendance"]
    assert o["flags"][0]["message"].startswith("Low attendance: in 8 of 20 (40%)")


def test_authorised_absence_counts_as_not_in():
    o = plan.attendance_outlook(_nights({0: ["Authorised Absence"], 2: [IN]}), WED)
    assert o["present"] == 10 and "weekday" in _codes(o)


def test_no_history_is_flagged_and_has_no_likelihood():
    o = plan.attendance_outlook([], WED)
    assert (_codes(o), o["likelihood"], o["last_present"]) == (["no_history"], None, None)


def test_nights_on_or_after_the_planned_date_and_before_the_lookback_are_ignored():
    records = [(WED, OUT), (WED + timedelta(days=7), OUT), (WED - timedelta(weeks=30), OUT),
               (WED - timedelta(weeks=1), IN)]
    o = plan.attendance_outlook(records, WED)
    assert (o["nights"], o["present"]) == (1, 1)


def test_a_cadet_not_in_for_weeks_is_flagged_with_when_they_were_last_in():
    records = [(WED - timedelta(weeks=8), IN)] + [(WED - timedelta(weeks=w), OUT) for w in range(1, 8)]
    o = plan.attendance_outlook(records, WED)
    assert "not_seen" in _codes(o)
    assert next(f for f in o["flags"] if f["code"] == "not_seen")["message"] == "Not in since 19 Aug"


def test_never_present_in_the_window_reads_six_months():
    o = plan.attendance_outlook([(WED - timedelta(weeks=1), OUT)], WED)
    assert {"code": "not_seen", "message": "Not in for 6 months"} in o["flags"]


def test_a_booked_absence_is_flagged_first():
    o = plan.attendance_outlook(_nights({0: [IN]}), WED, absence_booked=True)
    assert _codes(o) == ["absence_booked"]


def test_too_few_weekday_nights_fall_back_to_the_overall_rate():
    records = _nights({2: [IN]}) + [(WED - timedelta(weeks=1), OUT), (WED - timedelta(weeks=2), OUT)]
    o = plan.attendance_outlook(records, WED)
    assert "weekday" not in _codes(o)
    assert o["likelihood"] == round(10 / 12, 2)


def test_unknown_statuses_count_as_not_in():
    o = plan.attendance_outlook([(WED - timedelta(weeks=1), None), (WED - timedelta(weeks=2), "??")], WED)
    assert o["present"] == 0
