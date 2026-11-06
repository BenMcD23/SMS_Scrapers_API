"""The theory catalogue as the QR codes and progress pages see it: the built-in
syllabus (core/theory_lessons.py, codes in core/lesson_codes.py) plus the
sections and subjects staff have added (Theory_Sections, Theory_Subjects,
Theory_Lesson_Codes).

The built-in half never changes at run time: its codes are on slides already,
and test_lesson_codes.py pins them. Staff-added subjects get codes from
``new_code`` when they're created or given another lesson, and keep them.

Staff can add subjects to Badges or to a section of their own, never to
Leading or Senior/Master: those are the classification syllabus, and the
classification planner, stats and the Volunteer Portal sync read them from
source.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.lesson_codes import LESSON_BY_CODE, LESSON_CODE_ALPHABET, LESSON_CODES
from core.theory_lessons import BADGES, LEADING, SENIOR_MASTER, THEORY_LESSONS, TheoryLesson
from database.models import TheoryLessonCode, TheorySection, TheorySubject

# A staff-added subject leads to no qualification the API can check.
NO_QUAL = ("none", "")

BUILT_IN_SECTIONS: tuple[str, ...] = tuple(dict.fromkeys(lesson.category for lesson in THEORY_LESSONS))
# The classification syllabus: nothing can be added to these.
SYLLABUS_SECTIONS = frozenset({LEADING, SENIOR_MASTER})

MAX_PARTS = 10


@dataclass(frozen=True)
class Catalogue:
    lessons: tuple[TheoryLesson, ...]           # active subjects, in display order
    by_key: dict[str, TheoryLesson]             # active subjects
    codes: dict[tuple[str, int], str]           # (key, part) -> code, active subjects
    by_code: dict[str, tuple[str, int]]         # code -> (key, part), active subjects
    sections: tuple[str, ...]                   # built-in, then staff-added
    custom: frozenset[str]                      # keys of staff-added subjects


def catalogue(db: Session) -> Catalogue:
    """Everything a cadet can be checked in to right now. A retired subject's
    codes stop working, as a built-in subject's would if it left the syllabus."""
    added = db.query(TheorySubject).filter(TheorySubject.active.is_(True)).order_by(TheorySubject.id).all()
    custom_lessons = [TheoryLesson(s.key, s.name, s.category, NO_QUAL, s.parts) for s in added]
    sections = BUILT_IN_SECTIONS + tuple(
        name for (name,) in db.query(TheorySection.name).order_by(TheorySection.id)
    )
    order = {name: i for i, name in enumerate(sections)}
    # Built-ins keep their place; staff-added subjects follow the built-ins in
    # their section, oldest first.
    lessons = sorted(
        [*THEORY_LESSONS, *custom_lessons],
        key=lambda lesson: order.get(lesson.category, len(order)),
    )
    keys = {s.key for s in added}
    codes = dict(LESSON_CODES)
    for key, part, code in db.query(TheoryLessonCode.subject_key, TheoryLessonCode.part, TheoryLessonCode.code):
        if key in keys:
            codes[(key, part)] = code
    by_key = {lesson.key: lesson for lesson in lessons}
    codes = {(k, p): c for (k, p), c in codes.items() if k in by_key and p <= by_key[k].parts}
    return Catalogue(
        lessons=tuple(lessons),
        by_key=by_key,
        codes=codes,
        by_code={c: kp for kp, c in codes.items()},
        sections=sections,
        custom=frozenset(keys),
    )


def new_code(db: Session) -> str:
    """A code no lesson has ever had, built-in or added (retired ones count:
    their codes may still be on a slide somewhere)."""
    while True:
        code = "".join(secrets.choice(LESSON_CODE_ALPHABET) for _ in range(8))
        if code not in LESSON_BY_CODE and not db.query(TheoryLessonCode.id).filter_by(code=code).first():
            return code


def new_key(db: Session, name: str) -> str:
    """A key for a new subject from its name, unlike any built-in or added one,
    or any deleted one whose codes are still kept (reusing its key would hand
    the new subject those printed codes). Prefixed so it can never meet a
    syllabus key the API adds later."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "subject"
    taken = (
        {lesson.key for lesson in THEORY_LESSONS}
        | {k for (k,) in db.query(TheorySubject.key)}
        | {k for (k,) in db.query(TheoryLessonCode.subject_key).distinct()}
    )
    key, n = f"custom_{slug}", 2
    while key in taken:
        key, n = f"custom_{slug}_{n}", n + 1
    return key


def can_add_to(section: str) -> bool:
    return section not in SYLLABUS_SECTIONS


__all__ = ["BADGES", "Catalogue", "catalogue", "new_code", "new_key", "can_add_to", "MAX_PARTS"]
