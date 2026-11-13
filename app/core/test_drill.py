"""Where a cadet is on the drill syllabus, and what a test attempt counts as."""

import pytest

from core import drill
from core.drill import COMPLETE, IN_PROGRESS, NOT_STARTED, READY, Stage

ONE = Stage(1, (11, 12), has_test=True)
TWO = Stage(2, (21, 22), has_test=True)
UNTESTED = Stage(3, (31,), has_test=False)
EMPTY = Stage(4, (), has_test=True)


# ── a stage ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("taught, passed, tested, status", [
    (set(), set(), False, NOT_STARTED),
    ({11}, set(), False, IN_PROGRESS),
    ({11, 12}, {11}, False, IN_PROGRESS),
    ({11, 12}, {11, 12}, False, READY),
    ({11, 12}, {11, 12}, True, COMPLETE),
    # Tested straight in (a transfer from another squadron): complete anyway.
    (set(), set(), True, COMPLETE),
])
def test_a_tested_stage_moves_from_taught_to_ready_to_complete(taught, passed, tested, status):
    assert drill.stage_status(ONE, taught, passed, tested) == status


def test_lessons_from_another_stage_dont_start_this_one():
    assert drill.stage_status(ONE, {21}, {21}, False) == NOT_STARTED


def test_a_stage_with_no_test_is_complete_once_its_lessons_are_passed():
    assert drill.stage_status(UNTESTED, {31}, set(), False) == IN_PROGRESS
    assert drill.stage_status(UNTESTED, {31}, {31}, False) == COMPLETE


def test_an_empty_stage_with_no_test_doesnt_block_finishing_the_syllabus():
    empty = Stage(5, (), has_test=False)
    assert drill.stage_status(empty, set(), set(), False) == COMPLETE
    p = drill.cadet_progress([ONE, empty], {11, 12}, {11, 12}, {1})
    assert p["current"] is None


def test_a_stage_with_no_lessons_is_never_ready_only_complete_by_its_test():
    # Staff may add a stage before its lessons; nobody should be "ready" for it.
    assert drill.stage_status(EMPTY, set(), set(), False) == NOT_STARTED
    assert drill.stage_status(EMPTY, set(), set(), True) == COMPLETE


# ── a cadet ───────────────────────────────────────────────────────────────────

def test_the_current_stage_is_the_first_not_complete():
    p = drill.cadet_progress([ONE, TWO], {11, 12, 21}, {11, 12}, {1})
    assert p["current"] == 2
    assert p["stages"] == {1: COMPLETE, 2: IN_PROGRESS}


def test_a_cadet_who_has_done_everything_has_no_current_stage():
    p = drill.cadet_progress([ONE, UNTESTED], {11, 12, 31}, {11, 12, 31}, {1})
    assert p["current"] is None


def test_ready_for_test_lists_only_stages_that_have_a_test():
    p = drill.cadet_progress([ONE, UNTESTED], {11, 12, 31}, {11, 12, 31}, set())
    assert p["ready_for_test"] == [1]


def test_no_syllabus_at_all_is_no_current_stage_not_an_error():
    assert drill.cadet_progress([], set(), set(), set()) == {"stages": {}, "current": None, "ready_for_test": []}


# ── a test attempt ────────────────────────────────────────────────────────────

def test_with_a_pass_mark_the_score_decides_whatever_the_tester_ticked():
    assert drill.attempt_passed(18, False, 25, 15) is True
    assert drill.attempt_passed(10, True, 25, 15) is False
    assert drill.attempt_passed(15, None, 25, 15) is True  # the pass mark itself passes


def test_with_a_pass_mark_a_score_is_required():
    with pytest.raises(ValueError, match="pass mark is 15"):
        drill.attempt_passed(None, True, 25, 15)


def test_without_a_pass_mark_the_tester_says():
    assert drill.attempt_passed(12, True, 25, None) is True
    assert drill.attempt_passed(None, False, None, None) is False
    with pytest.raises(ValueError, match="Say whether"):
        drill.attempt_passed(12, None, 25, None)


@pytest.mark.parametrize("score", [-1, 26])
def test_a_score_outside_the_range_is_refused(score):
    with pytest.raises(ValueError, match="between 0 and 25"):
        drill.attempt_passed(score, True, 25, None)


def test_with_no_maximum_only_a_negative_score_is_refused():
    assert drill.attempt_passed(400, True, None, None) is True
    with pytest.raises(ValueError, match="negative"):
        drill.attempt_passed(-3, True, None, None)
