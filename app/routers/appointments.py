"""Staff cadet and uniformed adult staff applications (staff only).

Each is a checklist staff tick off as the applicant goes through the Wing's
process; the steps and their print-off form links live in ``core.appointments``.

Staff cadets: the page lists every cadet 17½ or over who has no application
yet, with their 18th birthday (the deadline: unfinished by then means
suspended), and staff start one when they choose. Adult applicants aren't on
SMS until they're appointed, so staff add them by name and route (SNCO / CFC).

An application is complete once every step is ticked, or when staff mark it
complete: one that finished before SMS tracked it can go straight on the
Complete list, and the Wing sometimes waives a step.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy.orm import Session

from core.appointments import (
    APPOINTED_STEP,
    CIC_MONTHS,
    STAFF_CADET,
    UNIFORMED,
    add_months,
    all_step_keys,
    eighteenth_birthday,
    is_nearing_18,
    progress,
    serialise_stages,
    step_keys,
)
from core.db import get_db
from core.recruit_form import london_now
from core.security import require_staff
from database.models import AppointmentApplication, Cadet

router = APIRouter()

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
Route = Literal["snco", "cfc"]


class StartStaffCadet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cin: int
    complete: bool = False


class StartUniformed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    route: Route
    complete: bool = False


class UpdateApplication(BaseModel):
    """Any of these may be sent; ``name`` and ``route`` are adult-only."""
    model_config = ConfigDict(extra="forbid")
    notes: Notes | None = None
    name: Name | None = None
    route: Route | None = None
    complete: bool | None = None


class TickStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool


def _is_staff_cadet_rank(rank: str | None) -> bool:
    """Already a staff cadet: nothing to apply for."""
    return "staff" in (rank or "").lower()


def _dob(cadet: Cadet) -> date | None:
    return cadet.date_of_birth.date() if cadet.date_of_birth else None


def _name(cadet: Cadet) -> str:
    return f"{cadet.first_name} {cadet.last_name}".strip()


def _cadet_fields(cadet: Cadet, today: date) -> dict:
    dob = _dob(cadet)
    turns_18 = eighteenth_birthday(dob) if dob else None
    return {
        "cin": cadet.cin,
        "name": _name(cadet),
        "rank": cadet.rank,
        "flight": cadet.flight,
        "dateOfBirth": dob.isoformat() if dob else None,
        "turns18On": turns_18.isoformat() if turns_18 else None,
        # Negative once they've turned 18: the deadline has passed.
        "daysTo18": (turns_18 - today).days if turns_18 else None,
    }


def _mark_complete(app: AppointmentApplication, done: bool, by: str | None) -> None:
    """Marking an already-marked application again keeps the first mark, as a
    second tick of a step does."""
    if not done:
        app.completed_at = app.completed_by = None
    elif app.completed_at is None:
        app.completed_at, app.completed_by = london_now(), by


def _application(app: AppointmentApplication, today: date) -> dict:
    steps = app.steps or {}
    marked = ({"at": app.completed_at.isoformat(timespec="seconds"), "by": app.completed_by}
              if app.completed_at else None)
    body = {
        "id": app.id,
        "kind": app.kind,
        "notes": app.notes,
        "steps": steps,
        "startedAt": app.started_at.isoformat(),
        "startedBy": app.started_by,
        **progress(app.kind, app.route, steps),
        # Done and nextStep still describe the ticks, so the checklist shows
        # what was skipped; complete is what puts it on the Complete list.
        "markedComplete": marked,
    }
    body["complete"] = body["complete"] or marked is not None
    if app.kind == STAFF_CADET:
        body["cadet"] = _cadet_fields(app.cadet, today)
    else:
        appointed = steps.get(APPOINTED_STEP)
        cic_due = add_months(datetime.fromisoformat(appointed["at"]).date(), CIC_MONTHS) if appointed else None
        body.update({
            "name": app.applicant_name,
            "route": app.route,
            "cicDueBy": cic_due.isoformat() if cic_due else None,
        })
    return body


def _get(db: Session, app_id: int) -> AppointmentApplication:
    app = db.get(AppointmentApplication, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="No application with that ID.")
    return app


def _by_deadline(cadet: dict) -> tuple:
    """Soonest 18th birthday first; an unknown date of birth last."""
    days = cadet["daysTo18"]
    return (days is None, days or 0)


# ── Staff cadets ─────────────────────────────────────────────────────────────

@router.get("/applications/staff-cadets")
def list_staff_cadet_applications(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """The checklist, open applications and the cadets old enough to start one."""
    today = london_now().date()
    apps = db.query(AppointmentApplication).filter(AppointmentApplication.kind == STAFF_CADET).all()
    started = {a.cadet_id for a in apps}
    nearing = [
        _cadet_fields(c, today)
        for c in db.query(Cadet).filter(Cadet.missing_since.is_(None)).all()
        if c.cin not in started and not _is_staff_cadet_rank(c.rank) and is_nearing_18(_dob(c), today)
    ]
    return {
        "stages": serialise_stages(STAFF_CADET),
        "applications": sorted((_application(a, today) for a in apps), key=lambda a: _by_deadline(a["cadet"])),
        "nearing18": sorted(nearing, key=_by_deadline),
    }


@router.post("/applications/staff-cadets", status_code=201)
def start_staff_cadet_application(
    body: StartStaffCadet, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    """Start tracking a cadet. Any age is allowed: 17½ is when the Wing says to
    start, not a rule, and a late start is still better than none. With
    ``complete`` it goes straight on the Complete list (done before SMS tracked it)."""
    cadet = db.get(Cadet, body.cin)
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {body.cin} not found.")
    if cadet.appointment_application:
        raise HTTPException(status_code=409, detail=f"{_name(cadet)} already has an application in progress.")
    app = AppointmentApplication(kind=STAFF_CADET, cadet_id=cadet.cin, steps={},
                                 started_at=london_now(), started_by=idinfo.get("email"))
    _mark_complete(app, body.complete, idinfo.get("email"))
    db.add(app)
    db.commit()
    return _application(app, london_now().date())


# ── Uniformed adult staff ────────────────────────────────────────────────────

@router.get("/applications/uniformed")
def list_uniformed_applications(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    today = london_now().date()
    apps = (db.query(AppointmentApplication)
            .filter(AppointmentApplication.kind == UNIFORMED)
            .order_by(AppointmentApplication.started_at).all())
    return {"stages": serialise_stages(UNIFORMED), "applications": [_application(a, today) for a in apps]}


@router.post("/applications/uniformed", status_code=201)
def start_uniformed_application(
    body: StartUniformed, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    app = AppointmentApplication(kind=UNIFORMED, applicant_name=body.name, route=body.route, steps={},
                                 started_at=london_now(), started_by=idinfo.get("email"))
    _mark_complete(app, body.complete, idinfo.get("email"))
    db.add(app)
    db.commit()
    return _application(app, london_now().date())


# ── Either kind ──────────────────────────────────────────────────────────────

@router.patch("/applications/{app_id}")
def update_application(
    app_id: int, body: UpdateApplication, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    """Notes on either kind; the name and route of an adult applicant; marking
    it complete (or back in progress) whatever is ticked. A route change keeps
    the Region ticks, so switching back loses nothing. Unmarking leaves an
    application whose every step is ticked complete: that comes from the ticks."""
    app = _get(db, app_id)
    sent = body.model_fields_set
    if sent & {"name", "route"} and app.kind != UNIFORMED:
        raise HTTPException(status_code=400, detail="A staff cadet application has no name or route to change.")
    if "notes" in sent:
        app.notes = body.notes or None
    if body.name is not None:
        app.applicant_name = body.name
    if body.route is not None:
        app.route = body.route
    if body.complete is not None:
        _mark_complete(app, body.complete, idinfo.get("email"))
    db.commit()
    return _application(app, london_now().date())


@router.put("/applications/{app_id}/steps/{key}")
def tick_step(
    app_id: int, key: str, body: TickStep, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff),
):
    """Tick a step done, or untick it. Ticking an already-ticked step keeps the
    first tick's time and person, so a double tap doesn't rewrite history."""
    app = _get(db, app_id)
    if key not in all_step_keys(app.kind):
        raise HTTPException(status_code=404, detail=f"No step {key!r} in this process.")
    if key not in step_keys(app.kind, app.route):
        raise HTTPException(status_code=409, detail="That step isn't part of this applicant's route.")
    # A new dict, not an in-place edit: SQLAlchemy only notices a JSON column
    # change when the value is replaced.
    steps = dict(app.steps or {})
    if body.done:
        steps.setdefault(key, {"at": london_now().isoformat(timespec="seconds"), "by": idinfo.get("email")})
    else:
        steps.pop(key, None)
    app.steps = steps
    db.commit()
    return _application(app, london_now().date())


@router.delete("/applications/{app_id}", status_code=204)
def delete_application(app_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Withdrawn, not recommended, or added by mistake. A staff cadet goes back
    on the nearing-18 list."""
    db.delete(_get(db, app_id))
    db.commit()
