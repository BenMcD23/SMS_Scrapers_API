"""The progressive drill syllabus tracker.

Staff edit the syllabus (stages, their lessons and each stage's test). Staff
and SNCOs record a cadet's lessons (taught, then passed) and test attempts;
NCOs can see everyone's progress, so they know who to teach next, but can't
sign anyone off. The rules for where a cadet is live in core/drill.py.

Lessons and stages with records against them can't be deleted (409): a
renamed lesson keeps its history, a deleted one would lose it.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session, selectinload

from core import drill
from core.db import get_db
from core.security import require_staff, require_staff_or_nco, require_staff_or_snco
from database.models import Cadet, CadetDrillLesson, CadetDrillTest, DrillLesson, DrillStage

router = APIRouter()

MAX_NAME = 120
MAX_SCORE = 1000


def _clean(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Enter a name")
    return value


class StageIn(BaseModel):
    name: str = Field(max_length=MAX_NAME)
    test_name: str | None = Field(default=None, max_length=MAX_NAME)
    max_score: int | None = Field(default=None, ge=1, le=MAX_SCORE)
    pass_mark: int | None = Field(default=None, ge=0, le=MAX_SCORE)

    _name = field_validator("name")(_clean)

    @field_validator("test_name")
    @classmethod
    def _test(cls, v):
        return " ".join(v.split()) or None if v is not None else None

    @model_validator(mode="after")
    def _marks(self):
        if self.test_name is None and (self.max_score is not None or self.pass_mark is not None):
            raise ValueError("A score or pass mark needs a test")
        if self.pass_mark is not None and self.max_score is not None and self.pass_mark > self.max_score:
            raise ValueError("The pass mark can't be more than the maximum score")
        return self


class LessonIn(BaseModel):
    name: str = Field(max_length=MAX_NAME)

    _name = field_validator("name")(_clean)


class Order(BaseModel):
    ids: list[int] = Field(max_length=200)


class LessonMark(BaseModel):
    taught: bool
    passed: bool

    @model_validator(mode="after")
    def _order(self):
        if self.passed and not self.taught:
            raise ValueError("A lesson has to be taught before it's passed")
        return self


class BulkMark(BaseModel):
    cins: list[int] = Field(min_length=1, max_length=200)
    passed: bool = False  # False marks them taught; True marks them taught and passed


class AttemptIn(BaseModel):
    stage_id: int
    tested_on: date
    score: int | None = None
    passed: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)


# ── reading ───────────────────────────────────────────────────────────────────

def _stages(db: Session) -> list[DrillStage]:
    return (
        db.query(DrillStage)
        .options(selectinload(DrillStage.lessons))
        .order_by(DrillStage.position, DrillStage.id)
        .all()
    )


def _stage_dict(s: DrillStage) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "test_name": s.test_name,
        "max_score": s.max_score,
        "pass_mark": s.pass_mark,
        "lessons": [{"id": lesson.id, "name": lesson.name} for lesson in s.lessons],
    }


@router.get("/drill/syllabus")
def get_syllabus(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)):
    return [_stage_dict(s) for s in _stages(db)]


@router.get("/drill/progress")
def get_progress(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)):
    """Every cadet's drill: each lesson's state, their test attempts, and where
    they are (core/drill.py). Names, ranks and flights only; nothing personal."""
    stages = _stages(db)
    plan = [drill.Stage(s.id, tuple(lesson.id for lesson in s.lessons), s.test_name is not None) for s in stages]
    marks: dict[int, list[CadetDrillLesson]] = {}
    for m in db.query(CadetDrillLesson):
        marks.setdefault(m.cadet_id, []).append(m)
    tests: dict[int, list[CadetDrillTest]] = {}
    for t in db.query(CadetDrillTest).order_by(CadetDrillTest.tested_on, CadetDrillTest.id):
        tests.setdefault(t.cadet_id, []).append(t)

    cadets = []
    for c in db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name):
        mine = marks.get(c.cin, [])
        attempts = tests.get(c.cin, [])
        taught = {m.lesson_id for m in mine if m.taught_at}
        passed = {m.lesson_id for m in mine if m.passed_at}
        progress = drill.cadet_progress(plan, taught, passed, {t.stage_id for t in attempts if t.passed})
        cadets.append({
            "cin": c.cin,
            "name": f"{c.first_name} {c.last_name}".strip(),
            "rank": c.rank,
            "flight": c.flight,
            "lessons": {
                str(m.lesson_id): {
                    "taught_at": m.taught_at.isoformat() if m.taught_at else None,
                    "passed_at": m.passed_at.isoformat() if m.passed_at else None,
                    "passed_by": m.passed_by,
                }
                for m in mine
            },
            "tests": [_test_dict(t) for t in attempts],
            **progress,
            "stages": {str(k): v for k, v in progress["stages"].items()},
        })
    return {"stages": [_stage_dict(s) for s in stages], "cadets": cadets}


def _test_dict(t: CadetDrillTest) -> dict:
    return {
        "id": t.id,
        "stage_id": t.stage_id,
        "tested_on": t.tested_on.isoformat(),
        "score": t.score,
        "passed": t.passed,
        "notes": t.notes,
        "tested_by": t.tested_by,
    }


# ── editing the syllabus (staff) ──────────────────────────────────────────────

def _stage_or_404(db: Session, stage_id: int) -> DrillStage:
    stage = db.get(DrillStage, stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")
    return stage


def _lesson_or_404(db: Session, lesson_id: int) -> DrillLesson:
    lesson = db.get(DrillLesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson


def _apply_stage(stage: DrillStage, body: StageIn) -> None:
    stage.name, stage.test_name = body.name, body.test_name
    stage.max_score, stage.pass_mark = body.max_score, body.pass_mark


@router.post("/drill/stages", status_code=201)
def add_stage(body: StageIn, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    last = db.query(DrillStage.position).order_by(DrillStage.position.desc()).first()
    stage = DrillStage(position=(last[0] + 1) if last else 0)
    _apply_stage(stage, body)
    db.add(stage)
    db.commit()
    return _stage_dict(stage)


@router.put("/drill/stages/{stage_id}")
def edit_stage(stage_id: int, body: StageIn, db: Session = Depends(get_db),
               idinfo: dict = Depends(require_staff)):
    stage = _stage_or_404(db, stage_id)
    _apply_stage(stage, body)
    db.commit()
    return _stage_dict(stage)


@router.delete("/drill/stages/{stage_id}", status_code=204)
def delete_stage(stage_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    stage = _stage_or_404(db, stage_id)
    if stage.lessons:
        raise HTTPException(status_code=409, detail="Delete or move the stage's lessons first")
    if db.query(CadetDrillTest).filter(CadetDrillTest.stage_id == stage_id).first():
        raise HTTPException(status_code=409, detail="Cadets have test results for this stage, so it can't be deleted")
    db.delete(stage)
    db.commit()
    return Response(status_code=204)


def _reorder(rows, ids: list[int]) -> None:
    by_id = {r.id: r for r in rows}
    if sorted(ids) != sorted(by_id):
        raise HTTPException(status_code=400, detail="The order must list each one exactly once")
    for position, row_id in enumerate(ids):
        by_id[row_id].position = position


@router.put("/drill/stages-order")
def order_stages(body: Order, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    _reorder(db.query(DrillStage).all(), body.ids)
    db.commit()
    return [_stage_dict(s) for s in _stages(db)]


@router.post("/drill/stages/{stage_id}/lessons", status_code=201)
def add_lesson(stage_id: int, body: LessonIn, db: Session = Depends(get_db),
               idinfo: dict = Depends(require_staff)):
    stage = _stage_or_404(db, stage_id)
    lesson = DrillLesson(stage_id=stage.id, name=body.name,
                         position=max((lesson.position for lesson in stage.lessons), default=-1) + 1)
    db.add(lesson)
    db.commit()
    return {"id": lesson.id, "name": lesson.name}


@router.put("/drill/stages/{stage_id}/lessons-order")
def order_lessons(stage_id: int, body: Order, db: Session = Depends(get_db),
                  idinfo: dict = Depends(require_staff)):
    stage = _stage_or_404(db, stage_id)
    _reorder(stage.lessons, body.ids)
    db.commit()
    db.refresh(stage)
    return _stage_dict(stage)


@router.put("/drill/lessons/{lesson_id}")
def rename_lesson(lesson_id: int, body: LessonIn, db: Session = Depends(get_db),
                  idinfo: dict = Depends(require_staff)):
    lesson = _lesson_or_404(db, lesson_id)
    lesson.name = body.name
    db.commit()
    return {"id": lesson.id, "name": lesson.name}


@router.delete("/drill/lessons/{lesson_id}", status_code=204)
def delete_lesson(lesson_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    lesson = _lesson_or_404(db, lesson_id)
    if db.query(CadetDrillLesson).filter(CadetDrillLesson.lesson_id == lesson_id).first():
        raise HTTPException(status_code=409, detail="Cadets have records for this lesson, so it can't be deleted. "
                                                    "Rename it instead.")
    db.delete(lesson)
    db.commit()
    return Response(status_code=204)


# ── recording (staff and SNCOs) ───────────────────────────────────────────────

def _cadet_or_404(db: Session, cin: int) -> Cadet:
    cadet = db.get(Cadet, cin)
    if cadet is None:
        raise HTTPException(status_code=404, detail="Cadet not found")
    return cadet


def _set_mark(row: CadetDrillLesson, taught: bool, passed: bool, by: str, now: datetime) -> None:
    """Keeps the first date a lesson was taught or passed: ticking it again
    doesn't move it, and unticking clears it."""
    if taught and not row.taught_at:
        row.taught_at, row.taught_by = now, by
    elif not taught:
        row.taught_at = row.taught_by = None
    if passed and not row.passed_at:
        row.passed_at, row.passed_by = now, by
    elif not passed:
        row.passed_at = row.passed_by = None


@router.put("/cadets/{cin}/drill/lessons/{lesson_id}")
def mark_lesson(cin: int, lesson_id: int, body: LessonMark, db: Session = Depends(get_db),
                idinfo: dict = Depends(require_staff_or_snco)):
    _cadet_or_404(db, cin)
    _lesson_or_404(db, lesson_id)
    row = db.query(CadetDrillLesson).filter_by(cadet_id=cin, lesson_id=lesson_id).first()
    if not (body.taught or body.passed):
        if row:
            db.delete(row)
            db.commit()
        return {"taught_at": None, "passed_at": None}
    if row is None:
        row = CadetDrillLesson(cadet_id=cin, lesson_id=lesson_id)
        db.add(row)
    _set_mark(row, body.taught, body.passed, idinfo["email"], datetime.now())
    db.commit()
    return {"taught_at": row.taught_at.isoformat() if row.taught_at else None,
            "passed_at": row.passed_at.isoformat() if row.passed_at else None}


@router.post("/drill/lessons/{lesson_id}/mark")
def mark_many(lesson_id: int, body: BulkMark, db: Session = Depends(get_db),
              idinfo: dict = Depends(require_staff_or_snco)):
    """Tick a lesson for a whole squad at once: the usual case on a parade
    night. Only adds: a cadet already further on keeps what they had. Any CIN
    not on the roster refuses the lot (404), so a typo doesn't half-save."""
    _lesson_or_404(db, lesson_id)
    cins = set(body.cins)
    known = {cin for (cin,) in db.query(Cadet.cin).filter(Cadet.cin.in_(cins))}
    if known != cins:
        raise HTTPException(status_code=404, detail=f"Not on the roster: {', '.join(map(str, sorted(cins - known)))}")
    rows = {r.cadet_id: r for r in db.query(CadetDrillLesson).filter(
        CadetDrillLesson.lesson_id == lesson_id, CadetDrillLesson.cadet_id.in_(cins))}
    now, by = datetime.now(), idinfo["email"]
    changed = 0
    for cin in sorted(cins):
        row = rows.get(cin)
        if row is None:
            row = CadetDrillLesson(cadet_id=cin, lesson_id=lesson_id)
            db.add(row)
        before = (row.taught_at, row.passed_at)
        _set_mark(row, True, body.passed or bool(row.passed_at), by, now)
        changed += (row.taught_at, row.passed_at) != before
    db.commit()
    return {"changed": changed}


@router.post("/cadets/{cin}/drill/tests", status_code=201)
def record_test(cin: int, body: AttemptIn, db: Session = Depends(get_db),
                idinfo: dict = Depends(require_staff_or_snco)):
    _cadet_or_404(db, cin)
    stage = _stage_or_404(db, body.stage_id)
    if stage.test_name is None:
        raise HTTPException(status_code=409, detail="This stage has no test")
    if body.tested_on > date.today():
        raise HTTPException(status_code=422, detail="The test date can't be in the future")
    try:
        passed = drill.attempt_passed(body.score, body.passed, stage.max_score, stage.pass_mark)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    test = CadetDrillTest(cadet_id=cin, stage_id=stage.id, tested_on=body.tested_on, score=body.score,
                          passed=passed, notes=(body.notes or "").strip() or None,
                          tested_by=idinfo["email"], recorded_at=datetime.now())
    db.add(test)
    db.commit()
    return _test_dict(test)


@router.delete("/drill/tests/{test_id}", status_code=204)
def delete_test(test_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_snco)):
    test = db.get(CadetDrillTest, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")
    db.delete(test)
    db.commit()
    return Response(status_code=204)
