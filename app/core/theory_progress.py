"""Recording which lessons of a theory subject a cadet has had.

Shared by the staff Classifications page and the cadet's QR check-in so both
keep the same rule: a subject is *taught* once every one of its lessons is in.
That is written as the subject's ``CadetTheoryProgress`` row, the one the
classification day planner and stats already read, so neither needs to know
about lessons.

The Volunteer Portal sync's rows (``recorded_by == PORTAL``) mean the exam was
passed, not that lessons were had; they are never written or removed here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from core import portal_data
from core.theory_lessons import TheoryLesson
from database.models import CadetPortalData, CadetTheoryLesson, CadetTheoryProgress

# Who wrote a CadetTheoryProgress row: the Volunteer Portal sync (an exam
# passed) or a cadet's QR scan. Staff marks carry the staff member's email.
PORTAL = "Volunteer Portal"
QR_CHECK_IN = "QR check-in"


def taught_and_passed(db: Session) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
    """({cin: subjects taught}, {cin: subjects whose exam the portal shows
    passed}) for every cadet, in two queries.

    Staff and QR marks are subjects taught; the sync's marks are exams passed.
    The raw exam results count too: when staff had already marked a subject,
    the sync keeps their row and writes none of its own, so the pass only
    shows there."""
    taught: dict[int, set[str]] = defaultdict(set)
    passed: dict[int, set[str]] = defaultdict(set)
    for cin, key, by in db.query(
        CadetTheoryProgress.cadet_id, CadetTheoryProgress.lesson_key, CadetTheoryProgress.recorded_by
    ):
        (passed if by == PORTAL else taught)[cin].add(key)
    for cin, raw in db.query(CadetPortalData.cadet_id, CadetPortalData.data).filter(
        CadetPortalData.dataset == "exams"
    ):
        passed[cin] |= set(portal_data.completed_theory(raw))
    return taught, passed


def lessons_had(db: Session, cins=None) -> dict[int, dict[str, set[int]]]:
    """{cin: {subject: lesson numbers had}}, for every cadet or just ``cins``."""
    q = db.query(CadetTheoryLesson.cadet_id, CadetTheoryLesson.lesson_key, CadetTheoryLesson.part)
    if cins is not None:
        q = q.filter(CadetTheoryLesson.cadet_id.in_(list(cins)))
    had: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for cin, key, part in q:
        had[cin][key].add(part)
    return had


def record_lessons(
    db: Session, cin: int, lesson: TheoryLesson, parts, by: str | None, when: datetime
) -> int:
    """Add the lessons in ``parts`` the cadet hasn't had yet, and mark the
    subject taught if that completes it. Returns how many lessons were added.
    The caller commits."""
    have = {
        p for (p,) in db.query(CadetTheoryLesson.part).filter(
            CadetTheoryLesson.cadet_id == cin, CadetTheoryLesson.lesson_key == lesson.key
        )
    }
    new = sorted(set(parts) - have)
    for part in new:
        db.add(CadetTheoryLesson(cadet_id=cin, lesson_key=lesson.key, part=part, attended_at=when, recorded_by=by))

    if have | set(new) >= set(range(1, lesson.parts + 1)):
        taught = db.query(CadetTheoryProgress).filter(
            CadetTheoryProgress.cadet_id == cin, CadetTheoryProgress.lesson_key == lesson.key
        ).first()
        if taught is None:
            db.add(CadetTheoryProgress(cadet_id=cin, lesson_key=lesson.key, completed_at=when, recorded_by=by))
    return len(new)


def clear_lessons(db: Session, cin: int, lesson: TheoryLesson, parts) -> int:
    """Remove the lessons in ``parts``. The subject is no longer taught, so a
    staff or QR "taught" row goes too; a portal exam pass stays. Returns how
    many lessons were removed. The caller commits."""
    removed = (
        db.query(CadetTheoryLesson)
        .filter(
            CadetTheoryLesson.cadet_id == cin,
            CadetTheoryLesson.lesson_key == lesson.key,
            CadetTheoryLesson.part.in_(list(parts)),
        )
        .delete(synchronize_session=False)
    )
    if removed:
        db.query(CadetTheoryProgress).filter(
            CadetTheoryProgress.cadet_id == cin,
            CadetTheoryProgress.lesson_key == lesson.key,
            CadetTheoryProgress.recorded_by.is_distinct_from(PORTAL),
        ).delete(synchronize_session=False)
    return removed
