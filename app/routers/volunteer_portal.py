"""Volunteer Portal sync — import what the portal knows that Bader SMS doesn't.

The portal sits behind the RAFAC Microsoft login (MFA), so nothing here can
reach it. A staff member runs the 317 SMS bookmarklet on the portal; it reads
the portal's JSON API with their own session and hands it to the 317 SMS sync
page, which posts it here with their Google token. So this endpoint only ever
receives data — it is staff-only, size-capped, strictly validated, and only
writes for cadets already in our roster.

Each data set is kept raw (Cadet_Portal_Data), then written into the normal
tables so the rest of 317 SMS needn't know where it came from: flights
(Cadet_Flights, which also prove the Blue Flying badge stages — see
core/qualifications.py), join date and classification dates on the cadet, and
passed classification exams as theory lessons.
"""

import json
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator
from sqlalchemy.orm import Session, selectinload

from core import portal_data
from core.db import get_db
from core.security import require_staff
from database.models import Cadet, CadetFlight, CadetPortalData, CadetTheoryProgress, ScraperRun

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
    known = {
        c.cin: c for c in db.query(Cadet)
        .filter(Cadet.cin.in_([c.cin for c in payload.cadets]))
        .options(selectinload(Cadet.flights))
    }
    existing = {
        (row.cadet_id, row.dataset): row
        for row in db.query(CadetPortalData).filter(CadetPortalData.cadet_id.in_(known))
    }
    counts = {"matched": 0, "unmatched": 0, "saved": 0, "failed": 0, "kept": 0, "theory": 0, "flights": 0}
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
        # Written from the stored copy, so the keep rules above apply here too.
        stored = {ds: row.data for (cin, ds), row in existing.items() if cin == cadet.cin}
        counts["flights"] += _write_flights(known[cadet.cin], stored.get("flying"))
        _write_dates(known[cadet.cin], stored)
        counts["theory"] += _write_theory(db, cadet.cin, stored.get("exams"))
        db.flush()  # a CIN sent twice must see what was just written
    return counts


def _write_flights(cadet: Cadet, raw) -> int:
    """Replace the cadet's flights with the portal's record. A record with no
    flights never replaces one with some — a blip mustn't wipe flying history."""
    flights = portal_data.flights(raw)
    if not flights and cadet.flights:
        return 0
    cadet.flights = [CadetFlight(**f) for f in flights]
    return len(flights)


def _write_dates(cadet: Cadet, stored: dict) -> None:
    joined = portal_data.joined_on(stored.get("unit_history"))
    if joined:
        cadet.joined_on = joined
    dates = portal_data.classification_dates(stored.get("classification"))
    if dates:
        cadet.classification_dates = dates


PORTAL = "Volunteer Portal"


def _write_theory(db: Session, cin: int, raw_exams) -> int:
    """Make the cadet's portal-recorded theory lessons match their passed portal
    exams. Lessons staff marked are never touched; a portal-recorded lesson the
    exams no longer show is removed. No exam results at all changes nothing."""
    if not (isinstance(raw_exams, dict) and raw_exams.get("results")):
        return 0
    passed = portal_data.completed_theory(raw_exams)
    rows = {r.lesson_key: r for r in db.query(CadetTheoryProgress).filter(CadetTheoryProgress.cadet_id == cin)}
    for key, row in rows.items():
        if row.recorded_by == PORTAL and key not in passed:
            db.delete(row)
    added = 0
    for key, when in passed.items():
        if key not in rows:
            db.add(CadetTheoryProgress(cadet_id=cin, lesson_key=key, completed_at=when, recorded_by=PORTAL))
            added += 1
    return added


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
