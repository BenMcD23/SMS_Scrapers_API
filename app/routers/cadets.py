"""Cadet records — search, list, detail, audit, theory progress, and staff edits."""

from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, exists
from sqlalchemy.orm import Session

from database.models import (
    Cadet, CadetMedical, CadetDietary, CadetQualification, CadetEvent,
    CadetTheoryProgress,
)

from core import cache
from core.db import get_db
from core.qualifications import BADGE_TYPES, BADGE_TYPE_BY_KEY, held_level
from core.theory_lessons import THEORY_LESSONS, THEORY_LESSON_BY_KEY
from core.security import require_staff, require_staff_or_nco

router = APIRouter()

CADETS_CACHE_KEY = "cadets:list"
CADETS_CACHE_TTL = 60


def invalidate_cadet_caches():
    """Drop caches derived from the cadet roster. Call from any write that
    changes cadet data (staff edits, scraper imports)."""
    cache.invalidate(CADETS_CACHE_KEY)
    cache.invalidate("stats:current")


class CadetPatch(BaseModel):
    email: Optional[str] = None
    banned: Optional[bool] = None


def _cadet_summary(c: Cadet) -> dict:
    return {
        "cin":            c.cin,
        "first_name":     c.first_name,
        "last_name":      c.last_name,
        "rank":           c.rank,
        "flight":         c.flight,
        "classification": c.classification,
    }


@router.get("/cadets/search")
async def search_cadets(
    q: str = "",
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    if not q or len(q.strip()) < 2:
        return []

    search = f"%{q.strip()}%"
    cadets = db.query(Cadet).filter(
        or_(
            Cadet.first_name.ilike(search),
            Cadet.last_name.ilike(search),
            (Cadet.first_name + " " + Cadet.last_name).ilike(search),
        )
    ).order_by(Cadet.last_name, Cadet.first_name).limit(10).all()

    return [_cadet_summary(c) for c in cadets]


@router.get("/cadets")
async def list_cadets(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cached = cache.get(CADETS_CACHE_KEY)
    if cached is not None:
        return cached
    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    result = [_cadet_summary(c) for c in cadets]
    cache.set(CADETS_CACHE_KEY, result, CADETS_CACHE_TTL)
    return result


# ─── Audit helpers ────────────────────────────────────────────────────────────

def _build_audit_result(cadets, qualifications, include_medical, include_dietary):
    # `qualifications` is a list of badge-type keys from the catalog. Unknown
    # keys are ignored so the frontend can't crash the audit.
    badges = [BADGE_TYPE_BY_KEY[k] for k in qualifications if k in BADGE_TYPE_BY_KEY]
    results = []
    for c in cadets:
        entry = {**_cadet_summary(c)}
        if badges:
            qual_names = [q.qual_type for q in c.qualifications]
            entry["qualifications_check"] = [
                {
                    "qual_type": b.key,
                    "display_name": b.name,
                    "kind": b.kind,
                    "level": (lvl := held_level(b, qual_names)),
                    "has": lvl is not None,
                }
                for b in badges
            ]
        if include_medical:
            entry["allergies"] = [
                {
                    "allergy_name": m.allergy_name,
                    "auto_injector": m.auto_injector,
                    "severity": m.severity,
                    "details": m.details,
                }
                for m in c.medical
            ]
        if include_dietary:
            entry["dietary"] = [
                {"name": d.name, "details": d.details}
                for d in c.dietary
            ]
        results.append(entry)
    return results


# ─── Audit routes (must be before /cadets/{cin}) ──────────────────────────────

@router.get("/cadets/audit/badge-types")
async def audit_badge_types(idinfo: dict = Depends(require_staff)):
    """The qualification catalog — badge types, their kind, and ordered levels
    (highest first) — so the frontend can build the audit UI dynamically."""
    return [
        {
            "key": b.key,
            "name": b.name,
            "kind": b.kind,
            "levels": [lvl.level for lvl in b.levels],
        }
        for b in BADGE_TYPES
    ]


@router.get("/cadets/audit/medical")
async def audit_medical(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadets = (
        db.query(Cadet)
        .filter(
            or_(
                exists().where(CadetMedical.cadet_id == Cadet.cin),
                exists().where(CadetDietary.cadet_id == Cadet.cin),
            )
        )
        .order_by(Cadet.last_name, Cadet.first_name)
        .all()
    )
    return [
        {
            **_cadet_summary(c),
            "allergies": [
                {
                    "id": m.id,
                    "allergy_name": m.allergy_name,
                    "auto_injector": m.auto_injector,
                    "severity": m.severity,
                    "details": m.details,
                }
                for m in c.medical
            ],
            "dietary": [
                {"id": d.id, "name": d.name, "details": d.details}
                for d in c.dietary
            ],
        }
        for c in cadets
    ]


class AuditCheckBody(BaseModel):
    cadet_cins: list[int] = []
    qualifications: list[str] = []
    include_medical: bool = False
    include_dietary: bool = False


@router.post("/cadets/audit/check")
async def audit_check_cadets(
    body: AuditCheckBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    if body.cadet_cins:
        cadets = (
            db.query(Cadet)
            .filter(Cadet.cin.in_(body.cadet_cins))
            .order_by(Cadet.last_name, Cadet.first_name)
            .all()
        )
    else:
        # Empty list means check all cadets
        cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    return _build_audit_result(cadets, body.qualifications, body.include_medical, body.include_dietary)


class EventAuditBody(BaseModel):
    event_id: int
    qualifications: list[str] = []
    include_medical: bool = False
    include_dietary: bool = False


@router.post("/cadets/audit/event-check")
async def audit_event_cadets(
    body: EventAuditBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet_cins = [
        ce.cadet_id
        for ce in db.query(CadetEvent).filter(CadetEvent.event_id == body.event_id).all()
    ]
    cadets = (
        db.query(Cadet)
        .filter(Cadet.cin.in_(cadet_cins))
        .order_by(Cadet.last_name, Cadet.first_name)
        .all()
    )
    return _build_audit_result(cadets, body.qualifications, body.include_medical, body.include_dietary)


# ─── Theory progress routes (must be before /cadets/{cin}) ────────────────────
# A "theory lesson" is training a cadet can complete the theory element of
# before the formal assessment/qualification is finished — tracked here so
# part-finished progress is visible (see core/theory_lessons.py).

@router.get("/cadets/theory/lessons")
async def theory_lessons(idinfo: dict = Depends(require_staff)):
    """The theory-lesson catalog — key, label and grouping category — so the
    frontend can build the record/progress UI dynamically."""
    return [
        {"key": l.key, "name": l.name, "category": l.category}
        for l in THEORY_LESSONS
    ]


class TheoryMarkBody(BaseModel):
    cadet_cins: list[int] = []
    lesson_keys: list[str] = []
    completed: bool = True  # True = mark theory done, False = clear it


@router.post("/cadets/theory/mark")
async def theory_mark(
    body: TheoryMarkBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Mark (or clear) the given theory lessons for the given cadets. Unknown
    lesson keys and CINs that don't exist are ignored."""
    keys = [k for k in body.lesson_keys if k in THEORY_LESSON_BY_KEY]
    if not body.cadet_cins or not keys:
        raise HTTPException(status_code=400, detail="Select at least one cadet and one lesson.")

    cins = [
        c.cin
        for c in db.query(Cadet.cin).filter(Cadet.cin.in_(body.cadet_cins)).all()
    ]
    if not cins:
        raise HTTPException(status_code=400, detail="No matching cadets found.")

    if body.completed:
        existing = {
            (r.cadet_id, r.lesson_key)
            for r in db.query(CadetTheoryProgress.cadet_id, CadetTheoryProgress.lesson_key)
            .filter(
                CadetTheoryProgress.cadet_id.in_(cins),
                CadetTheoryProgress.lesson_key.in_(keys),
            )
            .all()
        }
        now = datetime.now()
        email = idinfo.get("email")
        changed = 0
        for cin in cins:
            for key in keys:
                if (cin, key) in existing:
                    continue
                db.add(CadetTheoryProgress(
                    cadet_id=cin,
                    lesson_key=key,
                    completed_at=now,
                    recorded_by=email,
                ))
                changed += 1
        action = "marked"
    else:
        changed = (
            db.query(CadetTheoryProgress)
            .filter(
                CadetTheoryProgress.cadet_id.in_(cins),
                CadetTheoryProgress.lesson_key.in_(keys),
            )
            .delete(synchronize_session=False)
        )
        action = "cleared"

    db.commit()
    return {"status": "success", "action": action, "changed": changed}


class TheoryCheckBody(BaseModel):
    lesson_keys: list[str] = []
    cadet_cins: list[int] = []  # empty = all cadets


@router.post("/cadets/theory/check")
async def theory_check(
    body: TheoryCheckBody,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """For the selected lessons, return every cadet who has completed the theory
    of at least one of them, with a per-lesson breakdown (mirrors the audit)."""
    keys = [k for k in body.lesson_keys if k in THEORY_LESSON_BY_KEY]
    if not keys:
        return []

    q = db.query(CadetTheoryProgress).filter(CadetTheoryProgress.lesson_key.in_(keys))
    if body.cadet_cins:
        q = q.filter(CadetTheoryProgress.cadet_id.in_(body.cadet_cins))

    progress: dict[int, dict[str, datetime]] = defaultdict(dict)
    for r in q.all():
        progress[r.cadet_id][r.lesson_key] = r.completed_at
    if not progress:
        return []

    cadets = (
        db.query(Cadet)
        .filter(Cadet.cin.in_(progress.keys()))
        .order_by(Cadet.last_name, Cadet.first_name)
        .all()
    )
    results = []
    for c in cadets:
        held = progress.get(c.cin, {})
        results.append({
            **_cadet_summary(c),
            "lessons_check": [
                {
                    "lesson_key": key,
                    "name": THEORY_LESSON_BY_KEY[key].name,
                    "has": key in held,
                    "completed_at": held[key].isoformat() if key in held else None,
                }
                for key in keys
            ],
        })
    return results


# ─── Individual cadet routes ──────────────────────────────────────────────────

@router.get("/cadets/{cin}")
async def get_cadet(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    qualifications = [
        {
            "id": q.id,
            "qualification_name": q.qual_type.replace("_", " ").title(),
            "achieved_date": q.date_achieved.isoformat() if q.date_achieved else None,
            "expires_date": q.date_expires.isoformat() if q.date_expires else None,
            "status": q.status,
        }
        for q in cadet.qualifications
    ]

    events = [
        {
            "id": e.id,
            "event_name": e.event.title if e.event else f"Event {e.event_id}",
            "event_date": None,
            "attended": True,
        }
        for e in cadet.cadet_events
    ]

    assessments = [
        {
            "id": a.id,
            "assessment_type": a.assessment_type,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "passed": a.fields.get("passed") if a.fields else None,
            "total_score": a.fields.get("total_score") if a.fields else None,
            "exercise_name": a.fields.get("exercise_name") if a.fields else None,
            "assessor_name": a.fields.get("assessor_name") if a.fields else None,
        }
        for a in cadet.assessment_sheets
    ]

    return {
        **_cadet_summary(cadet),
        "email": cadet.email,
        "date_of_birth": cadet.date_of_birth.isoformat() if cadet.date_of_birth else None,
        "banned": cadet.banned,
        "qualifications": qualifications,
        "events": events,
        "assessments": assessments,
        "allergies": [
            {
                "id": m.id,
                "allergy_name": m.allergy_name,
                "auto_injector": m.auto_injector,
                "severity": m.severity,
                "details": m.details,
            }
            for m in cadet.medical
        ],
        "dietary": [
            {"id": d.id, "name": d.name, "details": d.details}
            for d in cadet.dietary
        ],
    }


@router.patch("/cadets/{cin}")
async def patch_cadet(
    cin: int,
    data: CadetPatch,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    # Only update fields that were explicitly provided
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(cadet, field, value)

    db.commit()
    db.refresh(cadet)
    invalidate_cadet_caches()

    return {"status": "success", "message": f"Cadet {cin} updated.", "updated_fields": list(update_data.keys())}
