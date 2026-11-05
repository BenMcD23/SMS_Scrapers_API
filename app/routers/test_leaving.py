"""Self-checks for the leaving-process date rules and the parent-address parser."""
from datetime import datetime, timedelta

import pytest

from core.leaving import (
    FLAGGED,
    LAPSE_BELOW_PCT,
    LAPSE_DAYS,
    RESPONSE_DAYS,
    START_LEAVING,
    WAITING,
    attendance_in_window,
    attendance_pct,
    gap_days,
    is_lapsed,
    leaving_status,
    parse_emails,
)

PARADE = datetime(2026, 8, 6)


def _nights(n):
    """n weekly parade nights ending on PARADE, oldest first."""
    return [PARADE - timedelta(weeks=i) for i in reversed(range(n))]


def test_attendance_only_counts_the_eight_weeks_to_the_current_parade():
    nights = _nights(10)
    everything = set(nights)
    first = nights[0]
    # Ten nights held, but only the latest eight are in the window: the night
    # exactly 56 days back is out, the one 49 days back is in.
    assert attendance_in_window(nights, everything, first, PARADE) == (8, 8)
    assert attendance_in_window(nights, {PARADE - timedelta(days=LAPSE_DAYS)}, first, PARADE) == (0, 8)
    assert attendance_in_window(nights, {PARADE - timedelta(days=49)}, first, PARADE) == (1, 8)


def test_nights_before_a_cadets_first_register_are_not_held_against_them():
    nights = _nights(10)
    joined = nights[-3]
    assert attendance_in_window(nights, set(nights[-3:]), joined, PARADE) == (3, 3)


def test_attendance_with_nothing_to_measure_is_zero_of_zero():
    nights = _nights(8)
    assert attendance_in_window(nights, set(), None, PARADE) == (0, 0)
    assert attendance_in_window(nights, set(nights), nights[0], None) == (0, 0)
    assert attendance_in_window([], set(), PARADE, PARADE) == (0, 0)


def test_attendance_pct():
    assert attendance_pct(3, 8) == 38
    assert attendance_pct(0, 8) == 0
    assert attendance_pct(0, 0) is None


def test_is_lapsed_is_under_forty_percent():
    assert LAPSE_BELOW_PCT == 40
    # Exactly 40% is fine; one night fewer is lapsed.
    assert not is_lapsed(4, 10)
    assert is_lapsed(3, 10)
    assert is_lapsed(3, 8)       # 37.5%
    assert not is_lapsed(4, 8)   # 50%
    assert is_lapsed(0, 8)
    assert not is_lapsed(8, 8)

    # Nothing to measure is never lapsed, so a brand new cadet with no scraped
    # register isn't flagged the moment they join.
    assert not is_lapsed(0, 0)


def test_gap_days():
    assert gap_days(PARADE - timedelta(days=35), PARADE) == 35
    assert gap_days(PARADE, PARADE) == 0
    assert gap_days(None, PARADE) is None
    # A register dated after the last parade night (a camp, say) reads as 0, not
    # a negative gap.
    assert gap_days(PARADE + timedelta(days=3), PARADE) == 0


def test_leaving_status():
    now = datetime(2026, 8, 20, 15, 30)

    assert leaving_status(None, now) == (FLAGGED, None)

    # Just sent — the full two weeks are left.
    assert leaving_status(now, now) == (WAITING, RESPONSE_DAYS)

    # Part way through.
    status, days_left = leaving_status(now - timedelta(days=5), now)
    assert (status, days_left) == (WAITING, RESPONSE_DAYS - 5)

    # The last day of the window is still waiting; the boundary tips the day the
    # fourteenth day completes.
    assert leaving_status(now - timedelta(days=RESPONSE_DAYS - 1), now) == (WAITING, 1)
    assert leaving_status(now - timedelta(days=RESPONSE_DAYS), now) == (START_LEAVING, 0)
    assert leaving_status(now - timedelta(days=365), now) == (START_LEAVING, 0)


def test_the_window_is_eight_weeks():
    assert LAPSE_DAYS == 56


def test_parse_emails_splits_on_anything_and_dedupes_case_insensitively():
    raw = "a@x.co; B@y.co,\n a@X.co  c@z.co;;"
    # Siblings share a parent, so the second a@x.co (any case) is dropped.
    assert parse_emails(raw) == ["a@x.co", "B@y.co", "c@z.co"]


@pytest.mark.parametrize("raw", [None, "", "  ;, \n"])
def test_parse_emails_empty_is_an_empty_list(raw):
    assert parse_emails(raw) == []


@pytest.mark.parametrize("raw", ["not-an-email", "a@x.co; oops", "a@@x.co"])
def test_parse_emails_rejects_a_bad_address_by_name(raw):
    with pytest.raises(ValueError, match="is not an email address"):
        parse_emails(raw)
