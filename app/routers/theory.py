"""Theory lessons: which of each subject's lessons every cadet has had.

Staff record lessons and see progress on the site's Classifications page.
Cadets check themselves in by scanning the QR code on a lesson's slides, which
opens the cadet portal; the rules both share live in core/theory_progress.py.

Registered before routers/cadets.py so ``/cadets/theory/...`` isn't taken for a
CIN by ``/cadets/{cin}``.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from core import classification_plan as plan
from core.db import get_current_cadet, get_db
from core.qualifications import qual_names_with_flights
from core.security import require_staff
from core.theory_lessons import THEORY_LESSON_BY_KEY, THEORY_LESSONS, lesson_qual_held
from core.theory_progress import (
    QR_CHECK_IN,
    clear_lessons,
    lesson_codes,
    lessons_had,
    record_lessons,
    taught_and_passed,
)
from database.models import Cadet, TheoryLessonCode

router = APIRouter()


def _lesson(lesson) -> dict:
    return {"key": lesson.key, "name": lesson.name, "category": lesson.category, "parts": lesson.parts}


@router.get("/cadets/theory/lessons")
def theory_lessons(idinfo: dict = Depends(require_staff)):
    """The subject catalogue in display order, with how many lessons each has."""
    return [_lesson(lesson) for lesson in THEORY_LESSONS]


@router.get("/cadets/theory/progress")
def theory_progress(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Every cadet with, for each subject they've made a start on, the lessons
    they've had, whether the subject is taught, and whether they've passed it
    (the exam on the portal, a classification that includes it, or the badge).

    Subjects with nothing to show are left out to keep the roster small; the
    page fills them in as not started."""
    cadets = (
        db.query(Cadet)
        .options(selectinload(Cadet.qualifications), selectinload(Cadet.flights))
        .order_by(Cadet.last_name, Cadet.first_name)
        .all()
    )
    taught, portal_passed = taught_and_passed(db)
    had = lessons_had(db)

    rows = []
    for c in cadets:
        passed = plan.passed_subjects(c.classification, portal_passed[c.cin])
        qual_names = qual_names_with_flights([q.qual_type for q in c.qualifications], c.flights)
        subjects = {}
        for lesson in THEORY_LESSONS:
            lessons = sorted(had.get(c.cin, {}).get(lesson.key, ()))
            if lesson.key in plan.CLASSIFICATION_KEYS:
                done = lesson.key in passed
            else:
                done = lesson_qual_held(lesson, qual_names, c.classification)
            is_taught = lesson.key in taught[c.cin]
            if lessons or is_taught or done:
                subjects[lesson.key] = {"lessons": lessons, "taught": is_taught, "passed": done}
        rows.append({
            "cin": c.cin,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "rank": c.rank,
            "flight": c.flight,
            "classification": c.classification,
            "subjects": subjects,
        })
    return rows


class LessonRef(BaseModel):
    key: str
    # One lesson of the subject; left out, every lesson of it.
    part: int | None = Field(None, ge=1)


class TheoryMarkBody(BaseModel):
    cadet_cins: list[int] = []
    lessons: list[LessonRef] = []
    completed: bool = True  # False clears them instead


@router.post("/cadets/theory/mark")
def theory_mark(
    body: TheoryMarkBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Record (or clear) lessons for a set of cadets. A whole subject marked at
    once is every one of its lessons, so it counts as taught. Unknown subjects
    and lesson numbers past the subject's last are refused rather than skipped,
    so a typo can't look like a save."""
    wanted: dict[str, set[int]] = {}
    for ref in body.lessons:
        lesson = THEORY_LESSON_BY_KEY.get(ref.key)
        if lesson is None:
            raise HTTPException(status_code=400, detail=f"Unknown subject: {ref.key}")
        if ref.part is not None and ref.part > lesson.parts:
            raise HTTPException(status_code=400, detail=f"{lesson.name} only has {lesson.parts} lesson(s).")
        parts = {ref.part} if ref.part else set(range(1, lesson.parts + 1))
        wanted.setdefault(ref.key, set()).update(parts)
    if not body.cadet_cins or not wanted:
        raise HTTPException(status_code=400, detail="Select at least one cadet and one lesson.")

    cins = [c for (c,) in db.query(Cadet.cin).filter(Cadet.cin.in_(body.cadet_cins))]
    if not cins:
        raise HTTPException(status_code=400, detail="No matching cadets found.")

    now = datetime.now()
    changed = 0
    for cin in cins:
        for key, parts in wanted.items():
            lesson = THEORY_LESSON_BY_KEY[key]
            if body.completed:
                changed += record_lessons(db, cin, lesson, parts, idinfo.get("email"), now)
            else:
                changed += clear_lessons(db, cin, lesson, parts)
    db.commit()
    return {"status": "success", "action": "marked" if body.completed else "cleared", "changed": changed}


@router.get("/cadets/theory/qr-codes")
def theory_qr_codes(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Every lesson's check-in code, for the QR codes staff put on slides. The
    site turns a code into the portal URL the QR opens."""
    codes = lesson_codes(db)
    return [
        {**_lesson(lesson), "part": part, "code": codes[(lesson.key, part)]}
        for lesson in THEORY_LESSONS
        for part in range(1, lesson.parts + 1)
    ]


@router.post("/cadets/me/lessons/{code}")
def cadet_check_in(
    code: str,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    """The signed-in cadet scanned a lesson's QR code: record that they had it.

    Scanning again is harmless and says so. The cadet comes from the token,
    never the request, so nobody can check someone else in."""
    row = db.query(TheoryLessonCode).filter(TheoryLessonCode.code == code).first()
    # A code for a subject since taken off the catalogue is as unknown as a typo.
    lesson = THEORY_LESSON_BY_KEY.get(row.lesson_key) if row else None
    if lesson is None or row.part > lesson.parts:
        raise HTTPException(status_code=404, detail="That QR code isn't a lesson we know. Ask a member of staff.")

    added = record_lessons(db, cadet.cin, lesson, {row.part}, QR_CHECK_IN, datetime.now())
    db.commit()
    lessons = sorted(lessons_had(db, [cadet.cin]).get(cadet.cin, {}).get(lesson.key, ()))
    return {
        **_lesson(lesson),
        "part": row.part,
        "already": added == 0,
        "lessons": lessons,
        "complete": len(lessons) >= lesson.parts,
    }
