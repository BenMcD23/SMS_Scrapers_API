"""Volunteer Portal sync — import what the portal knows that Bader SMS doesn't.

The portal sits behind the RAFAC Microsoft login (MFA), so nothing here can
reach it. A staff member runs the 317 SMS bookmarklet on the portal; it reads
the portal's JSON API with their own session and hands it to the 317 SMS sync
page, which posts it here with their Google token. So this endpoint only ever
receives data — it is staff-only, size-capped, strictly validated, and only
writes for cadets already in our roster.

Each data set is stored raw (Cadet_Portal_Data) because the portal's response
shapes are undocumented; the dashboards built on it parse what they need.
"""

import json
import logging
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator
from sqlalchemy.orm import Session, selectinload

from core import portal_data
from core.db import get_db
from core.security import require_staff
from database.models import Cadet, CadetPortalData, CadetTheoryProgress, ScraperRun

logger = logging.getLogger(__name__)

router = APIRouter()

SCRAPER_ID = "vp-sync"

Dataset = Literal[
    "whts", "shooting_log", "fieldcraft", "classification",
    "exams", "flying", "learning", "unit_history",
]

# A squadron's whole sync is a few MB; these stop a bad or hostile client
# filling the database or the API's memory.
MAX_BODY_BYTES = 20 * 1024 * 1024
MAX_CADETS = 500
MAX_DATASET_BYTES = 512 * 1024


class PortalCadet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cin: int = Field(gt=0)
    # None for a data set means the portal call failed — keep what we have.
    data: dict[Dataset, JsonValue | None] = Field(max_length=8)

    @field_validator("data")
    @classmethod
    def _cap_size(cls, data):
        for name, value in data.items():
            if value is not None and len(json.dumps(value)) > MAX_DATASET_BYTES:
                raise ValueError(f"{name} is larger than {MAX_DATASET_BYTES} bytes")
        return data


class PortalSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadets: list[PortalCadet] = Field(max_length=MAX_CADETS)


async def _read_capped(request: Request) -> bytes:
    """The body, refused with 413 past MAX_BODY_BYTES *while* reading — a
    declared Content-Length can lie and chunked uploads don't send one."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Sync payload too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Sync payload too large")
    return bytes(body)


def _is_empty(value) -> bool:
    return value in ([], {}, "")


def apply_sync(db: Session, payload: PortalSync, now: datetime) -> dict:
    """Upsert each cadet's data sets. Cadets not in our roster are skipped (the
    portal may show people we don't track), a failed call (None) never touches
    the stored copy, and an empty answer never replaces a non-empty one — the
    same rule as every scraper: a fetch that returned nothing mustn't wipe data."""
    known = {cin for (cin,) in db.query(Cadet.cin).filter(Cadet.cin.in_([c.cin for c in payload.cadets]))}
    existing = {
        (row.cadet_id, row.dataset): row
        for row in db.query(CadetPortalData).filter(CadetPortalData.cadet_id.in_(known))
    }
    counts = {"matched": 0, "unmatched": 0, "saved": 0, "failed": 0, "kept": 0, "theory": 0}
    for cadet in payload.cadets:
        if cadet.cin not in known:
            counts["unmatched"] += 1
            continue
        counts["matched"] += 1
        for dataset, value in cadet.data.items():
            row = existing.get((cadet.cin, dataset))
            if value is None:
                counts["failed"] += 1
            elif row and _is_empty(value) and not _is_empty(row.data):
                counts["kept"] += 1
            elif row:
                row.data, row.synced_at = value, now
                counts["saved"] += 1
            else:
                row = CadetPortalData(cadet_id=cadet.cin, dataset=dataset, data=value, synced_at=now)
                db.add(row)
                existing[(cadet.cin, dataset)] = row  # a CIN sent twice updates, not duplicates
                counts["saved"] += 1
        counts["theory"] += _mark_passed_exams(db, cadet.cin, cadet.data.get("exams"))
    return counts


def _mark_passed_exams(db: Session, cin: int, raw_exams) -> int:
    """Tick the ACP theory lessons a cadet has passed on the portal, so staff
    don't mark them by hand. Only ever adds — a lesson staff marked stays."""
    passed = portal_data.completed_theory(raw_exams)
    if not passed:
        return 0
    held = {
        key for (key,) in db.query(CadetTheoryProgress.lesson_key)
        .filter(CadetTheoryProgress.cadet_id == cin, CadetTheoryProgress.lesson_key.in_(passed))
    }
    for key, when in passed.items():
        if key not in held:
            db.add(CadetTheoryProgress(cadet_id=cin, lesson_key=key, completed_at=when, recorded_by="Volunteer Portal"))
    db.flush()  # a CIN sent twice must see the rows just added
    return len(passed.keys() - held)


@router.post("/vp-sync")
async def vp_sync(
    request: Request,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    # Read by hand, after the staff check, so nobody else's body is ever read
    # and the size cap applies before anything is parsed.
    raw = await _read_capped(request)
    try:
        payload = PortalSync.model_validate_json(raw)
    except ValidationError as e:
        raise RequestValidationError(e.errors(include_url=False))

    now = datetime.now()
    counts = apply_sync(db, payload, now)
    summary = ", ".join(f"{k} {v}" for k, v in counts.items())
    db.add(ScraperRun(scraper_id=SCRAPER_ID, ran_at=now, success=True, ran_by=idinfo["email"],
                      logs=f"Volunteer Portal sync: {summary}"))
    db.commit()
    logger.info(f"vp-sync by {idinfo['email']}: {summary}")
    return counts


# ── Reading it back ───────────────────────────────────────────────────────────

@router.get("/vp/cadets/{cin}")
def cadet_portal_data(cin: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """One cadet's portal data, parsed. A data set never synced is null."""
    cadet = db.get(Cadet, cin)
    if cadet is None:
        raise HTTPException(status_code=404, detail="Cadet not found")
    return portal_data.parse_all(cadet.portal_data)


def _overview_row(cadet: Cadet, today: date) -> dict:
    p = portal_data.parse_all(cadet.portal_data)
    flights = p["flying"] or []
    exams = p["exams"] or {"enrolments": [], "results": []}
    units = p["unit_history"] or []
    learning = p["learning"] or []
    fieldcraft = p["fieldcraft"] or []
    return {
        "cin": cadet.cin,
        "name": f"{cadet.first_name} {cadet.last_name}",
        "rank": cadet.rank,
        "flight": cadet.flight,
        "synced_at": p["synced_at"],
        "whts": None if p["whts"] is None else portal_data.wht_summary(p["whts"], today),
        "shooting": None if p["shooting_log"] is None else {
            "shoots": len(p["shooting_log"]),
            "last": p["shooting_log"][0]["date"] if p["shooting_log"] else None,
        },
        "fieldcraft": None if p["fieldcraft"] is None else {
            level: sum(1 for r in fieldcraft if r["level"] == level) for level in portal_data.FIELDCRAFT_LEVELS
        },
        "exams": None if p["exams"] is None else {
            "enrolled": len(exams["enrolments"]),
            "completed": sum(1 for r in exams["results"] if r["status"] == "completed"),
            "in_progress": sum(1 for r in exams["results"] if r["status"] == "in_progress"),
        },
        "flying": None if p["flying"] is None else {
            "sorties": len(flights),
            "minutes": sum(r["minutes"] or 0 for r in flights),
            "last": flights[0]["date"] if flights else None,
        },
        "learning": None if p["learning"] is None else {
            "complete": sum(1 for r in learning if r["complete"]),
            "total": len(learning),
        },
        # When they joined this squadron: the current primary unit's start date.
        "joined": next((u["start"] for u in reversed(units) if u["primary"] and not u["end"]), None),
        "classification": p["classification"],
    }


@router.get("/vp/overview")
def portal_overview(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Every cadet's portal data summarised for the squadron dashboard."""
    today = date.today()
    cadets = db.query(Cadet).options(selectinload(Cadet.portal_data)).order_by(Cadet.last_name, Cadet.first_name).all()
    rows = [_overview_row(c, today) for c in cadets]
    weapons = sorted({w for r in rows for w in (r["whts"] or {})})
    synced = [r["synced_at"] for r in rows if r["synced_at"]]
    return {"weapons": weapons, "last_synced": max(synced) if synced else None, "cadets": rows}
