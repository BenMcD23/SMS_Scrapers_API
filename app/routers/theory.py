"""Theory lessons: which of each subject's lessons every cadet has had.

Staff record lessons and see progress in the site's Training area, and add
sections and QR subjects of their own beside the built-in syllabus
(core/theory_catalogue.py).
Cadets check themselves in by scanning the QR code on a lesson's slides, which
opens the cadet portal; the rules both share live in core/theory_progress.py.

Registered before routers/cadets.py so ``/cadets/theory/...`` isn't taken for a
CIN by ``/cadets/{cin}``.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, selectinload

from core import classification_plan as plan
from core.db import get_current_cadet, get_db
from core.qualifications import qual_names_with_flights
from core.security import require_staff
from core.theory_catalogue import BUILT_IN_SECTIONS, MAX_PARTS, can_add_to, catalogue, new_code, new_key
from core.theory_lessons import THEORY_LESSONS, lesson_qual_held
from core.theory_progress import (
    QR_CHECK_IN,
    clear_lessons,
    lessons_had,
    record_lessons,
    taught_and_passed,
)
from database.models import (
    Cadet,
    CadetTheoryLesson,
    CadetTheoryProgress,
    TheoryLessonCode,
    TheorySection,
    TheorySubject,
)

router = APIRouter()


def _lesson(lesson, custom: frozenset[str] = frozenset()) -> dict:
    return {"key": lesson.key, "name": lesson.name, "category": lesson.category, "parts": lesson.parts,
            "custom": lesson.key in custom}


@router.get("/cadets/theory/lessons")
def theory_lessons(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """The subject catalogue in display order, with how many lessons each has:
    the built-in syllabus and any subjects staff have added."""
    cat = catalogue(db)
    return [_lesson(lesson, cat.custom) for lesson in cat.lessons]


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
    lessons = catalogue(db).lessons

    rows = []
    for c in cadets:
        passed = plan.passed_subjects(c.classification, portal_passed[c.cin])
        qual_names = qual_names_with_flights([q.qual_type for q in c.qualifications], c.flights)
        subjects = {}
        for lesson in lessons:
            parts_had = sorted(had.get(c.cin, {}).get(lesson.key, ()))
            if lesson.key in plan.CLASSIFICATION_KEYS:
                done = lesson.key in passed
            else:
                done = lesson_qual_held(lesson, qual_names, c.classification)
            is_taught = lesson.key in taught[c.cin]
            if parts_had or is_taught or done:
                subjects[lesson.key] = {"lessons": parts_had, "taught": is_taught, "passed": done}
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
    by_key = catalogue(db).by_key
    wanted: dict[str, set[int]] = {}
    for ref in body.lessons:
        lesson = by_key.get(ref.key)
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
            lesson = by_key[key]
            if body.completed:
                changed += record_lessons(db, cin, lesson, parts, idinfo.get("email"), now)
            else:
                changed += clear_lessons(db, cin, lesson, parts)
    db.commit()
    return {"status": "success", "action": "marked" if body.completed else "cleared", "changed": changed}


@router.get("/cadets/theory/qr-codes")
def theory_qr_codes(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Every lesson's check-in code, for the QR codes staff put on slides. The
    site turns a code into the portal URL the QR opens. Built-in codes are
    fixed in core/lesson_codes.py; added subjects' are made once and kept."""
    cat = catalogue(db)
    return [
        {**_lesson(lesson, cat.custom), "part": part, "code": cat.codes[(lesson.key, part)]}
        for lesson in cat.lessons
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
    cat = catalogue(db)
    key, part = cat.by_code.get(code, (None, 0))
    # A code for a subject since taken off the catalogue (or retired) is as
    # unknown as a typo.
    lesson = cat.by_key.get(key)
    if lesson is None or part > lesson.parts:
        raise HTTPException(status_code=404, detail="That QR code isn't a lesson we know. Ask a member of staff.")

    added = record_lessons(db, cadet.cin, lesson, {part}, QR_CHECK_IN, datetime.now())
    db.commit()
    lessons = sorted(lessons_had(db, [cadet.cin]).get(cadet.cin, {}).get(lesson.key, ()))
    return {
        **_lesson(lesson, cat.custom),
        "part": part,
        "already": added == 0,
        "lessons": lessons,
        "complete": len(lessons) >= lesson.parts,
    }


# ── adding sections and QR subjects ───────────────────────────────────────────

def _clean(text: str) -> str:
    return " ".join(text.split())


class SectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("Give the section a name")
        return v


class SubjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    section: str = Field(min_length=1, max_length=60)
    parts: int = Field(default=1, ge=1, le=MAX_PARTS)
    active: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("Give the subject a name")
        return v


def _section_names(db: Session) -> list[str]:
    return list(BUILT_IN_SECTIONS) + [n for (n,) in db.query(TheorySection.name).order_by(TheorySection.id)]


def _check_section(db: Session, name: str) -> str:
    """The section a subject is going into, as it's spelled on the page."""
    match = next((s for s in _section_names(db) if s.lower() == name.strip().lower()), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"There's no section called “{name}”")
    if not can_add_to(match):
        raise HTTPException(
            status_code=400,
            detail=f"{match} is the classification syllabus, so subjects can't be added to it. "
                   "Use Badges or a section of your own.",
        )
    return match


def _subject_out(s: TheorySubject, codes: dict[int, str], used: bool) -> dict:
    return {
        "key": s.key, "name": s.name, "category": s.category, "parts": s.parts, "active": s.active,
        "codes": [{"part": p, "code": codes[p]} for p in sorted(codes)],
        # Lessons recorded against it: it can be retired but not deleted.
        "used": used,
    }


def _used(db: Session, key: str) -> bool:
    return bool(
        db.query(CadetTheoryLesson.id).filter(CadetTheoryLesson.lesson_key == key).first()
        or db.query(CadetTheoryProgress.id).filter(CadetTheoryProgress.lesson_key == key).first()
    )


def _codes_for(db: Session, key: str) -> dict[int, str]:
    return {p: c for p, c in db.query(TheoryLessonCode.part, TheoryLessonCode.code).filter_by(subject_key=key)}


def _subject_or_404(db: Session, key: str) -> TheorySubject:
    s = db.query(TheorySubject).filter_by(key=key).first()
    if s is None:
        if key in {lesson.key for lesson in THEORY_LESSONS}:
            raise HTTPException(status_code=400, detail="That's a built-in subject: its codes are fixed and it can't be changed here.")
        raise HTTPException(status_code=404, detail="That subject isn't in the catalogue")
    return s


@router.get("/cadets/theory/sections")
def theory_sections(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Every section of the QR codes page in order: built-in ones first, then
    staff's. ``can_add`` says whether subjects can be added to it."""
    counts: dict[str, int] = {}
    for lesson in THEORY_LESSONS:
        counts[lesson.category] = counts.get(lesson.category, 0) + 1
    for (cat,) in db.query(TheorySubject.category).filter(TheorySubject.active.is_(True)):
        counts[cat] = counts.get(cat, 0) + 1
    rows = [{"id": None, "name": n, "built_in": True, "can_add": can_add_to(n), "subjects": counts.get(n, 0)}
            for n in BUILT_IN_SECTIONS]
    rows += [{"id": s.id, "name": s.name, "built_in": False, "can_add": True, "subjects": counts.get(s.name, 0)}
             for s in db.query(TheorySection).order_by(TheorySection.id)]
    return rows


@router.post("/cadets/theory/sections", status_code=201)
def add_theory_section(body: SectionBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    if any(n.lower() == body.name.lower() for n in _section_names(db)):
        raise HTTPException(status_code=409, detail=f"There's already a section called “{body.name}”")
    section = TheorySection(name=body.name, created_at=datetime.now())
    db.add(section)
    db.commit()
    return {"id": section.id, "name": section.name, "built_in": False, "can_add": True, "subjects": 0}


@router.put("/cadets/theory/sections/{section_id}")
def rename_theory_section(
    section_id: int, body: SectionBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    section = db.get(TheorySection, section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="That section isn't on the page")
    if any(n.lower() == body.name.lower() for n in _section_names(db) if n != section.name):
        raise HTTPException(status_code=409, detail=f"There's already a section called “{body.name}”")
    # Subjects store the section's name, so they move with it. Their codes don't change.
    db.query(TheorySubject).filter(TheorySubject.category == section.name).update({"category": body.name})
    section.name = body.name
    db.commit()
    return {"id": section.id, "name": section.name}


@router.delete("/cadets/theory/sections/{section_id}", status_code=204)
def delete_theory_section(section_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    section = db.get(TheorySection, section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="That section isn't on the page")
    if db.query(TheorySubject.id).filter(TheorySubject.category == section.name).first():
        raise HTTPException(status_code=409, detail="Move or delete its subjects first (retired ones too).")
    db.delete(section)
    db.commit()


@router.get("/cadets/theory/subjects")
def theory_subjects(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """The subjects staff have added, retired ones too, with their codes."""
    subjects = db.query(TheorySubject).order_by(TheorySubject.id).all()
    codes: dict[str, dict[int, str]] = {}
    for key, part, code in db.query(TheoryLessonCode.subject_key, TheoryLessonCode.part, TheoryLessonCode.code):
        codes.setdefault(key, {})[part] = code
    used = {k for (k,) in db.query(CadetTheoryLesson.lesson_key).distinct()} | {
        k for (k,) in db.query(CadetTheoryProgress.lesson_key).distinct()
    }
    return [_subject_out(s, {p: c for p, c in codes.get(s.key, {}).items() if p <= s.parts}, s.key in used)
            for s in subjects]


def _add_codes(db: Session, key: str, parts: int) -> None:
    """Codes for lessons 1..parts that don't have one yet. A lesson that had one
    (the subject was cut back, then grown again) gets the same code back."""
    have = set(_codes_for(db, key))
    now = datetime.now()
    for part in range(1, parts + 1):
        if part not in have:
            db.add(TheoryLessonCode(subject_key=key, part=part, code=new_code(db), created_at=now))
            db.flush()


@router.post("/cadets/theory/subjects", status_code=201)
def add_theory_subject(body: SubjectBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    section = _check_section(db, body.section)
    if any(lesson.name.lower() == body.name.lower() and lesson.category == section
           for lesson in catalogue(db).lessons):
        raise HTTPException(status_code=409, detail=f"{section} already has a subject called “{body.name}”")
    subject = TheorySubject(
        key=new_key(db, body.name), name=body.name, category=section, parts=body.parts,
        active=body.active, created_at=datetime.now(), created_by=idinfo.get("email"),
    )
    db.add(subject)
    _add_codes(db, subject.key, body.parts)
    db.commit()
    return _subject_out(subject, _codes_for(db, subject.key), used=False)


@router.put("/cadets/theory/subjects/{key}")
def edit_theory_subject(
    key: str, body: SubjectBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    subject = _subject_or_404(db, key)
    section = _check_section(db, body.section)
    if body.parts < subject.parts:
        raise HTTPException(
            status_code=409,
            detail=f"It has {subject.parts} lessons and their codes may be on slides: lessons can be added, not taken away.",
        )
    if any(lesson.name.lower() == body.name.lower() and lesson.category == section and lesson.key != key
           for lesson in catalogue(db).lessons):
        raise HTTPException(status_code=409, detail=f"{section} already has a subject called “{body.name}”")
    subject.name, subject.category, subject.active = body.name, section, body.active
    subject.parts = body.parts
    _add_codes(db, key, body.parts)
    db.commit()
    codes = {p: c for p, c in _codes_for(db, key).items() if p <= subject.parts}
    return _subject_out(subject, codes, _used(db, key))


@router.delete("/cadets/theory/subjects/{key}", status_code=204)
def delete_theory_subject(key: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Delete a subject nobody has been recorded on. Its codes are kept, so one
    already printed can never come back as a different lesson."""
    subject = _subject_or_404(db, key)
    if _used(db, key):
        raise HTTPException(status_code=409, detail="Cadets have lessons recorded on it. Retire it instead.")
    db.delete(subject)
    db.commit()
