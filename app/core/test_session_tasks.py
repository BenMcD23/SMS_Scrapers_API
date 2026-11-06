"""Matching leadership sheets to the task bank, and who has led what."""

import pytest

from core.session_tasks import Lead, TaskKey, leads_by_cadet, match_task

BANK = [
    TaskKey(1, "1", "Bridge"),
    TaskKey(2, "2", "Spider's Web"),
    TaskKey(3, None, "Minefield"),
    TaskKey(4, "10", "Toxic Barrel"),
]


@pytest.mark.parametrize("fields,task_id", [
    ({"exercise_no": "2", "exercise_name": "anything"}, 2),
    ({"exercise_no": "02", "exercise_name": ""}, 2),             # leading zeros
    ({"exercise_no": " 10 ", "exercise_name": ""}, 4),
    ({"exercise_no": "", "exercise_name": "spiders web"}, 2),    # case and punctuation
    ({"exercise_no": "", "exercise_name": "  MINEFIELD "}, 3),   # a task with no number
    ({"exercise_no": "99", "exercise_name": "Bridge"}, 1),       # unknown number falls back to the name
    ({"exercise_no": "", "exercise_name": "Swamp"}, None),
    ({}, None),
    (None, None),
])
def test_an_old_sheet_is_placed_on_its_number_then_its_name(fields, task_id):
    assert match_task(fields, BANK) == task_id


def test_a_stored_task_id_wins_over_the_text():
    # The assessor picked task 3 from the bank; the text is whatever it was then.
    assert match_task({"task_id": 3, "exercise_no": "1", "exercise_name": "Bridge"}, BANK) == 3


def test_a_stored_task_id_is_kept_even_if_the_task_is_gone():
    assert match_task({"task_id": 77}, BANK) == 77


def test_a_junk_task_id_matches_nothing():
    assert match_task({"task_id": "x", "exercise_name": "Bridge"}, BANK) is None


def test_two_tasks_with_the_same_name_are_never_guessed_between():
    bank = [TaskKey(1, None, "Bridge"), TaskKey(2, None, "bridge")]
    assert match_task({"exercise_name": "Bridge"}, bank) is None


def test_non_ascii_names_still_match():
    bank = [TaskKey(5, None, "Pont d'Été")]
    assert match_task({"exercise_name": "PONT D’ÉTÉ"}, bank) == 5
    assert match_task({"exercise_name": "pont d ete"}, bank) is None  # an accent is a different letter


def test_an_empty_bank_matches_nothing():
    assert match_task({"exercise_no": "1", "exercise_name": "Bridge"}, []) is None


def test_a_failed_sheet_still_counts_as_having_led_the_task():
    sheets = [
        (10, 100, {"exercise_no": "1", "passed": True, "date_iso": "2026-01-05"}),
        (11, 100, {"exercise_no": "2", "passed": False, "date_iso": "2026-02-05"}),
        (12, 200, {"exercise_name": "Swamp", "passed": True}),  # not in the bank
    ]
    leads = leads_by_cadet(sheets, BANK)
    assert leads[100] == [Lead(10, 1, True, "2026-01-05"), Lead(11, 2, False, "2026-02-05")]
    assert 200 not in leads


def test_no_sheets_means_no_leads():
    assert leads_by_cadet([], BANK) == {}
