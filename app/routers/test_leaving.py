"""Self-checks for the leaving-process date rules and the parent-address parser."""
from datetime import datetime, timedelta

import pytest

from core.leaving import (
    FLAGGED,
    LAPSE_DAYS,
    RESPONSE_DAYS,
    START_LEAVING,
    WAITING,
    gap_days,
    is_lapsed,
    leaving_status,
    parse_emails,
)

PARADE = datetime(2026, 8, 6)


def test_is_lapsed():
    # Exactly eight weeks is lapsed; a day under is not.
    assert is_lapsed(PARADE - timedelta(days=LAPSE_DAYS), PARADE)
    assert not is_lapsed(PARADE - timedelta(days=LAPSE_DAYS - 1), PARADE)
    assert is_lapsed(PARADE - timedelta(days=90), PARADE)

    # Attended tonight.
    assert not is_lapsed(PARADE, PARADE)

    # Nothing to measure against is never lapsed, so a brand new cadet with no
    # scraped register isn't flagged the moment they join.
    assert not is_lapsed(None, PARADE)
    assert not is_lapsed(PARADE - timedelta(days=90), None)
    assert not is_lapsed(None, None)


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


def test_lapse_is_eight_weeks():
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
