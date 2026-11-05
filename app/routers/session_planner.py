"""Session planner (staff only): the bank of tasks a session can run and the kit
list the squadron owns.

Tasks are the stands a session rotates groups through: leadership command
tasks, radio exercises, anything else. Each says how long it takes, what group
size it suits and what kit it needs, so the planner can tell which tasks fit
side by side in one round. Leadership assessments are written against a task
(``fields.task_id``), so the bank also knows who has led what; the rules for
older sheets with only free text are in core/session_tasks.py.

Nothing here refuses a task for needing more kit than there is: a radio breaks
and the stock goes down, and that shouldn't stop anyone editing the task. A
task that can't run with what's in stock says so in ``kit_short`` instead.

The leadership task picker (``/assessments/leadership/tasks``) is also open to
NCOs, since they write leadership assessments.
"""

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from core import session_tasks as st
from core.db import get_db
from core.security import require_staff, require_staff_or_nco
from database.models import AssessmentSheet, Cadet, SessionKit, SessionTask, SessionTaskKit

router = APIRouter()

MAX_GROUP = 50
MAX_DURATION = 240  # minutes; longer than a parade night is a typo
MAX_KIT = 999


def _clean(text: str) -> str:
    return " ".join(text.split())


# ── bodies ────────────────────────────────────────────────────────────────────

class KitBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: int = Field(ge=0, le=MAX_KIT)  # 0: we own it, but it's all broken or out on loan
    notes: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("Give the kit a name")
        return v


class TaskKitBody(BaseModel):
    kit_id: int
    quantity: int = Field(default=1, ge=1, le=MAX_KIT)


class TaskBody(BaseModel):
    kind: str
    number: str | None = Field(default=None, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=4000)
    duration_minutes: int = Field(ge=1, le=MAX_DURATION)
    min_group: int | None = Field(default=None, ge=1, le=MAX_GROUP)
    max_group: int | None = Field(default=None, ge=1, le=MAX_GROUP)
    active: bool = True
    kit: list[TaskKitBody] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("Give the task a name")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in st.SESSION_KINDS:
            raise ValueError(f"Kind must be one of: {', '.join(st.SESSION_KINDS)}")
        return v

    @field_validator("number")
    @classmethod
    def _number(cls, v: str | None) -> str | None:
        return _clean(v) or None if v is not None else None

    @model_validator(mode="after")
    def _groups(self):
        if self.min_group and self.max_group and self.min_group > self.max_group:
            raise ValueError("The smallest group can't be bigger than the largest")
        ids = [k.kit_id for k in self.kit]
        if len(ids) != len(set(ids)):
            raise ValueError("Each piece of kit only once: put the total in its quantity")
        return self


class LinkBody(BaseModel):
    task_id: int


# ── shared reads ──────────────────────────────────────────────────────────────

def _task_keys(db: Session) -> list[st.TaskKey]:
    # Retired tasks too: an old sheet for a retired task is still a lead.
    return [
        st.TaskKey(t.id, t.number, t.name)
        for t in db.query(SessionTask).filter(SessionTask.kind == st.LEADERSHIP)
    ]


def _leadership_sheets(db: Session):
    return db.query(AssessmentSheet.id, AssessmentSheet.cadet_id, AssessmentSheet.fields).filter(
        AssessmentSheet.assessment_type == st.LEADERSHIP_ASSESSMENT
    )


def _leads(db: Session) -> dict[int, list[st.Lead]]:
    return st.leads_by_cadet(_leadership_sheets(db), _task_keys(db))


def _kit_out(k: SessionKit) -> dict:
    return {
        "id": k.id, "name": k.name, "quantity": k.quantity, "notes": k.notes,
        "used_by": sorted(
            ({"task_id": u.task.id, "name": u.task.name, "quantity": u.quantity} for u in k.uses),
            key=lambda u: u["name"].lower(),
        ),
    }


def _task_out(t: SessionTask, leads_per_task: dict[int, list[tuple[int, st.Lead]]]) -> dict:
    mine = leads_per_task.get(t.id, [])
    return {
        "id": t.id, "kind": t.kind, "number": t.number, "name": t.name, "brief": t.brief,
        "duration_minutes": t.duration_minutes, "min_group": t.min_group, "max_group": t.max_group,
        "active": t.active,
        "kit": [{"kit_id": u.kit_id, "name": u.kit.name, "quantity": u.quantity, "in_stock": u.kit.quantity}
                for u in t.kit],
        # Kit this task needs more of than the squadron has: it can't run at all.
        "kit_short": [u.kit.name for u in t.kit if u.quantity > u.kit.quantity],
        "led_count": len(mine),
        "passed_count": sum(1 for _, lead in mine if lead.passed),
        "cadets_led": len({cin for cin, _ in mine}),
    }


def _per_task(leads: dict[int, list[st.Lead]]) -> dict[int, list[tuple[int, st.Lead]]]:
    """``(cin, lead)`` pairs per task, from the per-cadet leads."""
    out: dict[int, list[tuple[int, st.Lead]]] = defaultdict(list)
    for cin, mine in leads.items():
        for lead in mine:
            out[lead.task_id].append((cin, lead))
    return out


# ── kit ───────────────────────────────────────────────────────────────────────

def _kit_or_404(db: Session, kit_id: int) -> SessionKit:
    kit = db.get(SessionKit, kit_id)
    if not kit:
        raise HTTPException(status_code=404, detail="That kit isn't on the list")
    return kit


def _unique_kit_name(db: Session, name: str, not_id: int | None = None) -> None:
    q = db.query(SessionKit).filter(func.lower(SessionKit.name) == name.lower())
    if not_id is not None:
        q = q.filter(SessionKit.id != not_id)
    if q.first():
        raise HTTPException(status_code=409, detail=f"“{name}” is already on the kit list")


@router.get("/session-planner/kit")
def list_kit(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    kit = db.query(SessionKit).options(selectinload(SessionKit.uses).selectinload(SessionTaskKit.task)).all()
    return [_kit_out(k) for k in sorted(kit, key=lambda k: k.name.lower())]


@router.post("/session-planner/kit", status_code=201)
def add_kit(body: KitBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    _unique_kit_name(db, body.name)
    now = datetime.now()
    kit = SessionKit(name=body.name, quantity=body.quantity, notes=body.notes.strip(),
                     created_at=now, updated_at=now)
    db.add(kit)
    db.commit()
    db.refresh(kit)
    return _kit_out(kit)


@router.put("/session-planner/kit/{kit_id}")
def edit_kit(kit_id: int, body: KitBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    kit = _kit_or_404(db, kit_id)
    _unique_kit_name(db, body.name, not_id=kit_id)
    kit.name, kit.quantity, kit.notes = body.name, body.quantity, body.notes.strip()
    kit.updated_at = datetime.now()
    db.commit()
    db.refresh(kit)
    return _kit_out(kit)


@router.delete("/session-planner/kit/{kit_id}", status_code=204)
def delete_kit(kit_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    kit = _kit_or_404(db, kit_id)
    if kit.uses:
        names = ", ".join(sorted(u.task.name for u in kit.uses))
        raise HTTPException(status_code=409, detail=f"Still needed by {names}. Take it off those tasks first.")
    db.delete(kit)
    db.commit()


# ── tasks ─────────────────────────────────────────────────────────────────────

def _task_or_404(db: Session, task_id: int) -> SessionTask:
    task = db.get(SessionTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="That task isn't in the bank")
    return task


def _check_task(db: Session, body: TaskBody, not_id: int | None = None) -> None:
    """Numbers and names are unique within a kind, retired tasks included,
    because old leadership sheets are matched on them."""
    same_kind = db.query(SessionTask).filter(SessionTask.kind == body.kind)
    if not_id is not None:
        same_kind = same_kind.filter(SessionTask.id != not_id)
    for t in same_kind:
        if body.number and t.number and st._norm(t.number) == st._norm(body.number):
            raise HTTPException(status_code=409, detail=f"Task {body.number} is already “{t.name}”")
        if st._norm(t.name) == st._norm(body.name):
            raise HTTPException(status_code=409, detail=f"There's already a task called “{t.name}”")
    wanted = {k.kit_id for k in body.kit}
    if wanted:
        found = {k for (k,) in db.query(SessionKit.id).filter(SessionKit.id.in_(wanted))}
        if wanted - found:
            raise HTTPException(status_code=400, detail="Some of that kit isn't on the kit list")


def _apply(task: SessionTask, body: TaskBody) -> None:
    task.kind, task.number, task.name = body.kind, body.number, body.name
    task.brief = body.brief.strip()
    task.duration_minutes = body.duration_minutes
    task.min_group, task.max_group = body.min_group, body.max_group
    task.active = body.active
    task.kit = [SessionTaskKit(kit_id=k.kit_id, quantity=k.quantity) for k in body.kit]
    task.updated_at = datetime.now()


def _load_tasks(db: Session):
    return db.query(SessionTask).options(selectinload(SessionTask.kit).selectinload(SessionTaskKit.kit))


@router.get("/session-planner/tasks")
def list_tasks(
    kind: str | None = Query(None),
    include_retired: bool = Query(False),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    if kind is not None and kind not in st.SESSION_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown kind of session: {kind}")
    q = _load_tasks(db)
    if kind:
        q = q.filter(SessionTask.kind == kind)
    if not include_retired:
        q = q.filter(SessionTask.active.is_(True))
    per_task = _per_task(_leads(db))
    return [_task_out(t, per_task) for t in sorted(q, key=_task_order)]


def _task_order(t: SessionTask):
    # Numbered tasks in number order (2 before 10), then the rest by name.
    n = t.number or ""
    return (t.kind, not n, int(n) if n.isdigit() else float("inf"), n.lower(), t.name.lower())


@router.post("/session-planner/tasks", status_code=201)
def add_task(body: TaskBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    _check_task(db, body)
    now = datetime.now()
    task = SessionTask(created_at=now)
    _apply(task, body)
    db.add(task)
    db.commit()
    return _task_out(_load_tasks(db).filter(SessionTask.id == task.id).one(), _per_task(_leads(db)))


@router.put("/session-planner/tasks/{task_id}")
def edit_task(task_id: int, body: TaskBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    task = _task_or_404(db, task_id)
    _check_task(db, body, not_id=task_id)
    per_task = _per_task(_leads(db))
    if body.kind != task.kind and per_task.get(task_id):
        raise HTTPException(
            status_code=409,
            detail="Cadets have been assessed on this task, so it has to stay a leadership task",
        )
    _apply(task, body)
    db.commit()
    return _task_out(_load_tasks(db).filter(SessionTask.id == task.id).one(), _per_task(_leads(db)))


@router.delete("/session-planner/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    task = _task_or_404(db, task_id)
    if _per_task(_leads(db)).get(task_id):
        # Deleting it would turn those cadets' leads back into "never led".
        raise HTTPException(status_code=409, detail="Cadets have been assessed on this task. Retire it instead.")
    db.delete(task)
    db.commit()


# ── leadership sheets the bank can't place ────────────────────────────────────

@router.get("/session-planner/unlinked-assessments")
def unlinked_assessments(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Leadership sheets that match no task (or more than one), newest first,
    for staff to link by hand."""
    keys = _task_keys(db)
    rows = (
        db.query(AssessmentSheet.id, AssessmentSheet.fields, AssessmentSheet.created_at,
                 Cadet.cin, Cadet.first_name, Cadet.last_name, Cadet.rank)
        .join(Cadet, Cadet.cin == AssessmentSheet.cadet_id)
        .filter(AssessmentSheet.assessment_type == st.LEADERSHIP_ASSESSMENT)
        .order_by(AssessmentSheet.created_at.desc())
    )
    out = []
    for sid, fields, created, cin, first, last, rank in rows:
        if st.match_task(fields, keys) is not None:
            continue
        fields = fields or {}
        out.append({
            "assessment_id": sid, "cin": cin, "first_name": first, "last_name": last, "rank": rank,
            "exercise_no": fields.get("exercise_no") or "", "exercise_name": fields.get("exercise_name") or "",
            "date": fields.get("date_iso") or created.date().isoformat(),
            "passed": bool(fields.get("passed")),
        })
    return out


@router.post("/session-planner/assessments/{assessment_id}/link")
def link_assessment(
    assessment_id: int, body: LinkBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    sheet = db.get(AssessmentSheet, assessment_id)
    if not sheet or sheet.assessment_type != st.LEADERSHIP_ASSESSMENT:
        raise HTTPException(status_code=404, detail="Leadership assessment not found")
    task = _task_or_404(db, body.task_id)
    if task.kind != st.LEADERSHIP:
        raise HTTPException(status_code=400, detail="Only a leadership task can be linked to a leadership assessment")
    # A new dict: SQLAlchemy doesn't see changes made inside a JSON column.
    sheet.fields = {**(sheet.fields or {}), "task_id": task.id}
    db.commit()
    return {"assessment_id": sheet.id, "task_id": task.id}


# ── the assessment form's picker ──────────────────────────────────────────────

@router.get("/assessments/leadership/tasks")
def leadership_task_picker(
    cin: int | None = Query(None, description="Mark the tasks this cadet has already led"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff_or_nco),
):
    """The active leadership tasks, for the assessment form. With ``cin``, each
    says whether that cadet has led it before and how it went, so the assessor
    can pick one they haven't."""
    mine: dict[int, list[st.Lead]] = defaultdict(list)
    if cin is not None:
        for lead in _leads(db).get(cin, []):
            mine[lead.task_id].append(lead)
    tasks = _load_tasks(db).filter(SessionTask.kind == st.LEADERSHIP, SessionTask.active.is_(True))
    out = []
    for t in sorted(tasks, key=_task_order):
        leads = mine.get(t.id, [])
        led = None
        if leads:
            led = "passed" if any(lead.passed for lead in leads) else "failed"
        out.append({"id": t.id, "number": t.number, "name": t.name,
                    "duration_minutes": t.duration_minutes, "led": led})
    return out
