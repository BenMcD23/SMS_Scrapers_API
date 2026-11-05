"""Classification day planner (staff only): every cadet's next theory lesson, or
the exam they're ready to sit, with a flag on anyone unlikely to be in.

The rules live in core/classification_plan.py; this gathers the inputs — theory
marks, Volunteer Portal exam results, the parade-night register and booked
absences — in one query each rather than one per cadet.
"""

from datetime import date as Date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core import classification_plan as plan
from core.db import get_db
from core.night_outlook import night_outlooks
from core.security import require_staff
from core.theory_lessons import THEORY_LESSON_BY_KEY
from core.theory_progress import lessons_had, taught_and_passed
from database.models import Cadet

router = APIRouter()

PARADE_NIGHT = "Parade Night"


@router.get("/classification-day/lessons")
def classification_lessons(idinfo: dict = Depends(require_staff)):
    """The subjects a classification day can run, in syllabus order."""
    return [
        {"key": s.key, "name": s.name, "category": s.category}
        for s in plan.LEADING_SUBJECTS + plan.SENIOR_SUBJECTS
    ]


@router.get("/classification-day")
def classification_day(
    date: Date = Query(..., description="The night being planned"),
    offered: list[str] | None = Query(None, description="Lessons running that night; omit for any"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """One row per cadet with their assignment and attendance outlook.

    ``offered`` narrows lessons to the ones the squadron can run that night, so a
    cadet whose next subject isn't on is given the next one that is. Exams are
    never narrowed: a cadet ready for one is put on it whatever runs.
    """
    if offered is not None:
        unknown = [k for k in offered if k not in plan.CLASSIFICATION_KEYS]
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown lesson: {', '.join(unknown)}")

    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()

    taught, passed = taught_and_passed(db)
    had = lessons_had(db)

    outlooks = night_outlooks(db, date, [c.cin for c in cadets])

    rows = []
    for c in cadets:
        mine = had.get(c.cin, {})
        assignment = plan.plan_cadet(c.classification, taught[c.cin], passed[c.cin], offered, started=mine)
        key = assignment["lesson_key"]
        subject = THEORY_LESSON_BY_KEY[key] if key else None
        # Which of the subject's lessons they're on: the first one they haven't had.
        part = None
        if subject and assignment["kind"] != plan.EXAM:
            part = next((p for p in range(1, subject.parts + 1) if p not in mine.get(key, ())), None)
        rows.append({
            "cin": c.cin,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "rank": c.rank,
            "flight": c.flight,
            "classification": c.classification,
            **assignment,
            "lesson_name": subject.name if subject else None,
            "lesson_part": part,
            "lesson_parts": subject.parts if subject else None,
            "attendance": outlooks[c.cin],
        })
    return {"date": date.isoformat(), "weekday": date.strftime("%A"), "cadets": rows}
