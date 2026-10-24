"""The appointment checklists and the date arithmetic behind "nearing 18"."""

from datetime import date

import pytest

from core.appointments import (
    CFC,
    SNCO,
    STAFF_CADET,
    UNIFORMED,
    add_months,
    all_step_keys,
    eighteenth_birthday,
    is_nearing_18,
    progress,
    serialise_stages,
    step_keys,
)


@pytest.mark.parametrize("kind", [STAFF_CADET, UNIFORMED])
def test_step_keys_are_unique_within_a_process(kind):
    # Ticks are stored by key, so two steps sharing one would tick together.
    keys = [s["key"] for stage in serialise_stages(kind) for s in stage["steps"]]
    assert len(keys) == len(set(keys)) == len(all_step_keys(kind))


def test_only_officer_applicants_sit_the_region_board():
    snco, cfc = step_keys(UNIFORMED, SNCO), step_keys(UNIFORMED, CFC)
    assert "region_board" in cfc and "region_board" not in snco
    assert set(snco) < set(cfc)
    assert step_keys(UNIFORMED, SNCO)[-1] == step_keys(UNIFORMED, CFC)[-1] == "cic"


def test_every_form_link_is_a_web_or_mail_link():
    for kind in (STAFF_CADET, UNIFORMED):
        for stage in serialise_stages(kind):
            for step in stage["steps"]:
                for link in step["links"]:
                    assert link["url"].startswith(("https://", "mailto:")), link


def test_progress_counts_only_the_steps_on_the_current_route():
    cfc_ticks = {k: {"at": "2026-01-01T00:00:00", "by": "x"} for k in step_keys(UNIFORMED, CFC)}
    assert progress(UNIFORMED, CFC, cfc_ticks)["complete"] is True
    # Switched to SNCO: the Region ticks are kept but no longer counted.
    snco = progress(UNIFORMED, SNCO, cfc_ticks)
    assert snco["done"] == snco["total"] == len(step_keys(UNIFORMED, SNCO))


def test_progress_of_a_fresh_application_points_at_the_first_step():
    p = progress(STAFF_CADET, None, None)
    assert (p["done"], p["complete"], p["nextStep"]) == (0, False, "bpss")
    p = progress(STAFF_CADET, None, {"bpss": {}})
    assert p["nextStep"] == "dbs_requested"


def test_a_leap_day_birthday_turns_18_on_1_march():
    assert eighteenth_birthday(date(2008, 2, 29)) == date(2026, 3, 1)
    assert eighteenth_birthday(date(2008, 7, 14)) == date(2026, 7, 14)


def test_add_months_clamps_to_the_end_of_a_short_month():
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)


@pytest.mark.parametrize("dob, nearing", [
    (date(2009, 4, 24), True),    # exactly 17½ today
    (date(2009, 4, 25), False),   # a day short
    (date(2007, 1, 1), True),     # already 18: still listed, it's overdue
    (date(2012, 1, 1), False),
    (None, False),
])
def test_nearing_18_means_17_and_a_half_or_over(dob, nearing):
    assert is_nearing_18(dob, date(2026, 10, 24)) is nearing
