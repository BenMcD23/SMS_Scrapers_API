"""The week and finishing rules behind a DofE activity log."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from core.activity_log import add_months, progress, week_start


def _log(start: date, months: int = 3, entries=()):
    return SimpleNamespace(start_date=start, duration_months=months,
                           entries=[SimpleNamespace(date=d, minutes=m) for d, m in entries])


MON = date(2026, 7, 27)
TODAY = date(2026, 10, 21)  # a Wednesday in week 13 of a log started on MON


@pytest.mark.parametrize("day", [MON, MON + timedelta(days=3), MON + timedelta(days=6)])
def test_weeks_run_monday_to_sunday(day):
    assert week_start(day) == MON


@pytest.mark.parametrize("start,months,end", [
    (date(2026, 6, 20), 3, date(2026, 9, 20)),
    (date(2026, 8, 31), 6, date(2027, 2, 28)),   # no 31 Feb: the end of the month
    (date(2027, 8, 31), 6, date(2028, 2, 29)),   # leap year
    (date(2026, 11, 15), 18, date(2028, 5, 15)),
])
def test_end_date_is_whole_calendar_months_later(start, months, end):
    assert add_months(start, months) == end


def test_a_week_counts_once_its_entries_reach_an_hour():
    # Two half hours in one week make an hour; 59 minutes doesn't.
    log = _log(MON, entries=[(MON, 30), (MON + timedelta(days=2), 30), (MON + timedelta(days=7), 59)])
    p = progress(log, TODAY)
    assert p["weeks_done"] == 1
    assert [w["minutes"] for w in p["weeks"][:2]] == [60, 59]
    assert p["weeks"][0]["done"] and not p["weeks"][1]["done"]


def test_sunday_and_the_next_monday_are_different_weeks():
    log = _log(MON, entries=[(MON + timedelta(days=6), 30), (MON + timedelta(days=7), 30)])
    assert progress(log, TODAY)["weeks_done"] == 0


def test_twelve_good_weeks_finish_a_three_month_section():
    log = _log(MON, 3, [(MON + timedelta(weeks=i), 60) for i in range(12)])
    p = progress(log, TODAY)
    assert (p["weeks_done"], p["weeks_needed"], p["complete"]) == (12, 12, True)


@pytest.mark.parametrize("months,weeks", [(3, 12), (6, 24), (12, 48), (18, 72)])
def test_a_month_is_four_weeks(months, weeks):
    assert progress(_log(MON, months), TODAY)["weeks_needed"] == weeks


def test_missed_weeks_are_past_weeks_under_an_hour_but_not_this_one():
    # This week isn't "missed" yet: there are days left to do it.
    log = _log(MON, entries=[(MON, 60)])
    p = progress(log, TODAY)
    assert len(p["weeks"]) == 13
    assert p["missed"][0] == (MON + timedelta(weeks=1)).isoformat()
    assert len(p["missed"]) == 11
    assert week_start(TODAY).isoformat() not in p["missed"]


def test_a_start_date_in_the_future_has_nothing_due():
    p = progress(_log(TODAY + timedelta(days=10)), TODAY)
    assert p == {**p, "started": False, "weeks": [], "missed": [], "this_week_minutes": 0, "weeks_done": 0}


def test_an_empty_log_is_all_gaps_and_not_complete():
    p = progress(_log(MON), TODAY)
    assert p["weeks_done"] == 0 and not p["complete"] and p["this_week_minutes"] == 0
    assert p["end_date"] == "2026-10-27"


def test_this_weeks_minutes_are_only_this_weeks():
    log = _log(MON, entries=[(TODAY, 20), (TODAY - timedelta(days=2), 25), (TODAY - timedelta(days=3), 99)])
    assert progress(log, TODAY)["this_week_minutes"] == 45
