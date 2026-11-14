"""The progressive drill syllabus: where each cadet is, and who is ready to be
tested.

Kept free of the database so the rules can be tested on their own; the router
(routers/drill.py) gathers the inputs.

A cadet works through the stages in order. Each lesson is *taught*, then
*passed* (they can do it on their own). Once every lesson of a stage is passed
they are ready for the stage's test (a 5x5 drill test by default); passing that
completes the stage. A stage with no test is complete once its lessons are all
passed.
"""

from __future__ import annotations

from dataclasses import dataclass

# The list a new database starts with, from ACP 19 (Drill and Ceremonial).
# Staff rename, reorder and add to it on the Drill page, so this is only a
# starting point; the migration writes the same list.
STARTER_TEST = "5x5 drill test"
STARTER_MAX_SCORE = 25
STARTER_SYLLABUS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Stage 1: At the halt", (
        "Attention, stand at ease and stand easy",
        "Turns at the halt",
        "Saluting at the halt",
        "Dismissing and falling out",
    )),
    ("Stage 2: Squad drill at the halt", (
        "Forming up in three ranks",
        "Dressing and numbering",
        "Open and close order",
        "Sizing a squad",
    )),
    ("Stage 3: On the march", (
        "Quick march and halt",
        "Turns on the march",
        "Wheeling",
        "Saluting on the march",
        "Eyes right and eyes left",
    )),
    ("Stage 4: Advanced", (
        "Changing step",
        "Marking time",
        "Words of command",
        "Taking a squad",
    )),
)

NOT_STARTED = "not_started"
IN_PROGRESS = "in_progress"
READY = "ready"            # every lesson passed, test still to pass
COMPLETE = "complete"


@dataclass(frozen=True)
class Stage:
    id: int
    lesson_ids: tuple[int, ...]
    has_test: bool


def attempt_passed(score: int | None, passed: bool | None, max_score: int | None,
                   pass_mark: int | None) -> bool:
    """Whether a test attempt passed. With a pass mark the score decides, so a
    tester can't record 10/25 as a pass; without one the tester says. Raises
    ValueError with a message for the person entering it."""
    if score is not None:
        if score < 0 or (max_score is not None and score > max_score):
            raise ValueError(f"Score must be between 0 and {max_score}" if max_score is not None
                             else "Score can't be negative")
    if pass_mark is not None:
        if score is None:
            raise ValueError(f"Enter a score: the pass mark is {pass_mark}")
        return score >= pass_mark
    if passed is None:
        raise ValueError("Say whether they passed")
    return passed


def stage_status(stage: Stage, taught: set[int], passed: set[int], test_passed: bool) -> str:
    lessons = set(stage.lesson_ids)
    if not lessons and not stage.has_test:
        # Nothing to do yet (staff added the stage before its lessons); it
        # mustn't stop anyone finishing the syllabus.
        return COMPLETE
    if (test_passed if stage.has_test else bool(lessons) and lessons <= passed):
        return COMPLETE
    if lessons and lessons <= passed:
        return READY
    if lessons & (taught | passed):
        return IN_PROGRESS
    return NOT_STARTED


def cadet_progress(stages: list[Stage], taught: set[int], passed: set[int],
                   tests_passed: set[int]) -> dict:
    """{stages: {stage id: status}, current: the first stage not complete
    (None once all are), ready_for_test: stage ids they could be tested on}.

    A test passed counts even if the lessons were never ticked: a cadet who
    joined from another squadron can be tested straight in."""
    statuses = {s.id: stage_status(s, taught, passed, s.id in tests_passed) for s in stages}
    current = next((s.id for s in stages if statuses[s.id] != COMPLETE), None)
    return {
        "stages": statuses,
        "current": current,
        "ready_for_test": [s.id for s in stages if statuses[s.id] == READY and s.has_test],
    }
