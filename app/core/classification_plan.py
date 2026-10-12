"""Who does what on a classification day: each cadet's next theory lesson or the
exam they're ready for, and how likely they are to be in on the night.

Kept free of the database so the rules can be tested on their own; the router
(routers/classification_day.py) gathers the inputs.

Two sources decide where a cadet is:

* **Lessons taught** — theory marks staff recorded on the Theory Progress page
  (``Cadet_Theory_Progress`` rows *not* written by the portal sync).
* **Exams passed** — the Volunteer Portal exam results, plus everything below
  the classification they already hold (a Leading Cadet has passed all three
  Leading exams whether or not the portal sync has seen them).

A lesson taught whose exam isn't passed means the cadet is ready to sit it,
which comes before starting anything new.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.attendance import PRESENT, attendance_state
from core.theory_lessons import (
    CLASSIFICATION_ORDER,
    LEADING,
    SENIOR_MASTER,
    THEORY_LESSONS,
    TheoryLesson,
)

LEADING_SUBJECTS: tuple[TheoryLesson, ...] = tuple(s for s in THEORY_LESSONS if s.category == LEADING)
SENIOR_SUBJECTS: tuple[TheoryLesson, ...] = tuple(s for s in THEORY_LESSONS if s.category == SENIOR_MASTER)
CLASSIFICATION_KEYS = frozenset(s.key for s in LEADING_SUBJECTS + SENIOR_SUBJECTS)


def _rank(classification: str | None) -> int:
    try:
        return CLASSIFICATION_ORDER.index(classification)
    except ValueError:
        return -1


FIRST_CLASS = _rank("First Class Cadet")
LEADING_RANK = _rank("Leading Cadet")
MASTER_RANK = _rank("Master Air Cadet")

# Plan kinds the page groups by.
EXAM = "exam"                  # lesson taught, exam not yet passed
LESSON = "lesson"              # next lesson to teach
NOT_OFFERED = "not_offered"    # still has lessons to do, but none of today's
FIRST_CLASS_TRAINING = "first_class"  # below First Class: not on the theory subjects yet
COMPLETE = "complete"          # every subject passed


def passed_subjects(classification: str | None, portal_passed) -> set[str]:
    """Subjects the cadet has passed: the portal's passes plus every subject of a
    classification they already hold. Senior Cadet is reached partway through
    the Senior/Master subjects, so only Master implies all of them."""
    passed = set(portal_passed) & CLASSIFICATION_KEYS
    rank = _rank(classification)
    if rank >= LEADING_RANK:
        passed |= {s.key for s in LEADING_SUBJECTS}
    if rank >= MASTER_RANK:
        passed |= {s.key for s in SENIOR_SUBJECTS}
    return passed


def plan_cadet(
    classification: str | None,
    taught,
    portal_passed,
    offered: list[str] | None = None,
) -> dict:
    """The cadet's assignment for the day.

    ``taught`` is the lesson keys staff have marked as taught, ``portal_passed``
    the subjects the portal shows as passed, ``offered`` the lessons running
    today (None = any). Returns {kind, lesson_key, ready_exams, awaiting}, where
    ``awaiting`` names a classification whose subjects are all passed but which
    the cadet doesn't hold yet — worth chasing on the portal.
    """
    rank = _rank(classification)
    passed = passed_subjects(classification, portal_passed)
    taught = set(taught)

    ready = [s.key for s in LEADING_SUBJECTS + SENIOR_SUBJECTS if s.key in taught and s.key not in passed]

    awaiting = None
    if rank < FIRST_CLASS:
        subjects: tuple[TheoryLesson, ...] = ()
    elif rank < LEADING_RANK:
        subjects = LEADING_SUBJECTS
        if all(s.key in passed for s in LEADING_SUBJECTS):
            # Exams done but the classification isn't on record yet; they can
            # carry on with the Senior subjects meanwhile.
            awaiting, subjects = "Leading Cadet", SENIOR_SUBJECTS
    elif rank < MASTER_RANK:
        subjects = SENIOR_SUBJECTS
        if all(s.key in passed for s in SENIOR_SUBJECTS):
            awaiting = "Master Air Cadet"
    else:
        subjects = ()

    todo = [s.key for s in subjects if s.key not in passed and s.key not in taught]

    if ready:
        kind, key = EXAM, ready[0]
    elif rank < FIRST_CLASS:
        kind, key = FIRST_CLASS_TRAINING, None
    elif not todo:
        kind, key = COMPLETE, None
    else:
        wanted = todo if offered is None else [k for k in todo if k in set(offered)]
        kind, key = (LESSON, wanted[0]) if wanted else (NOT_OFFERED, todo[0])

    return {"kind": kind, "lesson_key": key, "ready_exams": ready, "awaiting": awaiting}


# ── Attendance outlook ────────────────────────────────────────────────────────
# Half a year of parade nights: long enough to see a weekday habit (about 25 of
# each night), short enough that a cadet who has changed their habits isn't
# judged on last year.
LOOKBACK = timedelta(weeks=26)
# Below half is "likely not in". The planner still assigns them; it only flags.
LOW_RATE = 0.5
# A weekday habit needs a few nights behind it, and to stand out from how often
# they come overall — otherwise it just repeats the low-attendance flag.
MIN_WEEKDAY_NIGHTS = 3
WEEKDAY_GAP = 0.15
# Matches the leaving process's idea of someone who has stopped coming.
NOT_SEEN = timedelta(weeks=6)

WEEKDAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


def _pct(n: int, total: int) -> str:
    return f"{n} of {total} ({round(100 * n / total)}%)"


def attendance_outlook(records, day: date, absence_booked: bool = False) -> dict:
    """How likely the cadet is to be in on ``day``, from their parade-night
    register (``records`` is (date, status) pairs; the caller picks parade
    nights). Only nights before ``day`` in the lookback count, so planning a
    past date doesn't peek at what happened.

    Returns the counts, a ``likelihood`` (their rate on that weekday when there
    are enough of those nights, else overall; None with no history) and the
    ``flags`` to show."""
    since = day - LOOKBACK
    window = [(d, attendance_state(s) == PRESENT) for d, s in records if since <= d < day]
    nights = len(window)
    present = sum(p for _, p in window)
    same_day = [p for d, p in window if d.weekday() == day.weekday()]
    wd_nights, wd_present = len(same_day), sum(same_day)
    attended = [d for d, p in window if p]
    last_present = max(attended) if attended else None

    rate = present / nights if nights else None
    wd_rate = wd_present / wd_nights if wd_nights else None
    likelihood = wd_rate if wd_nights >= MIN_WEEKDAY_NIGHTS else rate

    flags = []
    if absence_booked:
        flags.append({"code": "absence_booked", "message": "Absence booked for this date"})
    if not nights:
        flags.append({"code": "no_history", "message": "No parade nights on record in the last 6 months"})
    else:
        if rate < LOW_RATE:
            flags.append({"code": "low_attendance",
                          "message": f"Low attendance: in {_pct(present, nights)} parade nights"})
        if wd_nights >= MIN_WEEKDAY_NIGHTS and wd_rate < LOW_RATE and wd_rate <= rate - WEEKDAY_GAP:
            flags.append({"code": "weekday",
                          "message": f"Rarely in on {WEEKDAYS[day.weekday()]}: {_pct(wd_present, wd_nights)}"})
        if last_present is None or day - last_present > NOT_SEEN:
            message = (f"Not in since {last_present.day} {last_present:%b}" if last_present
                       else "Not in for 6 months")
            flags.append({"code": "not_seen", "message": message})

    return {
        "nights": nights,
        "present": present,
        "weekday_nights": wd_nights,
        "weekday_present": wd_present,
        "last_present": last_present.isoformat() if last_present else None,
        "likelihood": round(likelihood, 2) if likelihood is not None else None,
        "flags": flags,
    }
