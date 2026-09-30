"""Volunteer Portal data pushed in by the VP sync browser extension.

VP (volunteers.bader.mod.uk) sits behind Microsoft sign-in with MFA, so it can't
be scraped from here the way SMS is. Instead a staff member's browser, already
signed in to VP, fetches VP's own JSON API and posts the results to these
endpoints. The extension lives in vp-sync-extension/; docs/vp-sync.md covers
what VP exposes and why only some of it is stored.

Two guards live here rather than in the extension, since this is the side we
control:
  - only datasets in DATASETS are accepted, so sensitive VP data (DBS, contact
    details, next of kin) can't end up in the database by accident;
  - only people whose CIN is already in Cadets or Staff (the SMS-scraped 317
    roster) are accepted, so a user whose VP access covers the whole wing
    doesn't pull the wing in.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.db import get_db
from core.security import require_staff
from database.models import Cadet, Staff, VpPerson, VpRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vp-sync", tags=["vp-sync"])

# What the extension may send, keyed by the name stored in VP_Records.dataset.
# All of these are things SMS doesn't carry. The value is the VP endpoint it
# comes from, kept here as documentation of where each one originates.
DATASETS = {
    "learning": "api/person/{id}/learning/history",
    "agreements": "api/person/{id}/agreements",
    "whts": "api/shootingmanagement/{id}/whts",
    "shooting_log": "api/shootingmanagement/{id}/shootinglog",
    "fieldcraft": "api/fieldcraftmanagement/{id}/completions",
    "aviation": "api/person/{id}/aviation/history",
    # Unit-wide in VP; the extension splits them into per-person rows.
    "mandatory_training": "api/trackers/mandatorytraining",
    "exam_results": "api/exams/management/subjects/{examId}/results",
}

# Roster fields kept from VP's persons list. The rest of the row (date of birth,
# gender, avatar and so on) either duplicates SMS or isn't needed, so it's
# dropped on the way in.
PROFILE_FIELDS = (
    "personnelId", "givenName", "familyName", "rankAbbreviation", "unitName",
    "wing", "classification", "flight", "activeStatusType", "serviceJoinDate",
    "appointmentType", "appointmentTitle",
)

PERSON_TYPES = ("cadet", "staff")
MAX_PEOPLE = 1000
MAX_RECORDS = 1000


class RosterPerson(BaseModel):
    personnelWebId: str = Field(min_length=1)
    cin: int
    personType: str
    profile: dict = {}


class RosterBody(BaseModel):
    people: list[RosterPerson]


class RecordItem(BaseModel):
    personnelWebId: str = Field(min_length=1)
    dataset: str
    # Whatever VP returned — a list, an object, or null when VP had nothing.
    payload: object = None


class RecordsBody(BaseModel):
    records: list[RecordItem]


def _roster_cins(db: Session) -> set[int]:
    return {c for (c,) in db.query(Cadet.cin).all()} | {c for (c,) in db.query(Staff.cin).all()}


@router.post("/people")
def sync_people(body: RosterBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Replace the VP roster with the one the extension just read.

    The body is the whole unit as VP sees it, so anyone synced before but
    missing now has left VP, and is removed along with their records.
    """
    if not body.people:
        raise HTTPException(status_code=422, detail="Empty roster")
    if len(body.people) > MAX_PEOPLE:
        raise HTTPException(status_code=413, detail=f"At most {MAX_PEOPLE} people per sync")
    bad_types = {p.personType for p in body.people} - set(PERSON_TYPES)
    if bad_types:
        raise HTTPException(status_code=422, detail=f"Unknown person type(s): {', '.join(sorted(bad_types))}")

    known = _roster_cins(db)
    accepted = [p for p in body.people if p.cin in known]
    skipped = len(body.people) - len(accepted)
    # Nobody matching means the extension is looking at the wrong unit, or the
    # SMS roster is empty. Either way pruning against it would wipe every VP
    # record, so refuse rather than guess.
    if not accepted:
        raise HTTPException(status_code=422, detail="No one in this roster is on the 317 SMS roster")

    now = datetime.now()
    email = idinfo["email"]
    existing = {p.personnel_web_id: p for p in db.query(VpPerson).all()}
    for p in accepted:
        profile = {k: p.profile[k] for k in PROFILE_FIELDS if k in p.profile}
        row = existing.get(p.personnelWebId)
        if row is None:
            row = VpPerson(personnel_web_id=p.personnelWebId)
            db.add(row)
        row.cin = p.cin
        row.person_type = p.personType
        row.profile = profile
        row.synced_at = now
        row.synced_by = email

    seen = {p.personnelWebId for p in accepted}
    departed = [wid for wid in existing if wid not in seen]
    if departed:
        # Records first: SQLite (local dev) doesn't enforce the cascade.
        db.query(VpRecord).filter(VpRecord.personnel_web_id.in_(departed)).delete(synchronize_session=False)
        db.query(VpPerson).filter(VpPerson.personnel_web_id.in_(departed)).delete(synchronize_session=False)
    db.commit()

    logger.info("vp-sync: roster from %s, %d accepted, %d not on the SMS roster, %d removed",
                email, len(accepted), skipped, len(departed))
    # The extension fetches per-person data only for these, so it never pulls
    # records the API would throw away.
    return {
        "accepted": len(accepted), "skippedNotOnRoster": skipped, "removed": len(departed),
        "acceptedIds": sorted(seen),
    }


@router.post("/records")
def sync_records(body: RecordsBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Upsert per-person VP datasets. People must already be on the VP roster
    (POST /vp-sync/people first); records for anyone else are skipped."""
    if len(body.records) > MAX_RECORDS:
        raise HTTPException(status_code=413, detail=f"At most {MAX_RECORDS} records per request")
    bad = {r.dataset for r in body.records} - set(DATASETS)
    if bad:
        raise HTTPException(status_code=422, detail=f"Unknown dataset(s): {', '.join(sorted(bad))}")

    ids = {r.personnelWebId for r in body.records}
    people = {wid for (wid,) in db.query(VpPerson.personnel_web_id)
              .filter(VpPerson.personnel_web_id.in_(ids)).all()} if ids else set()
    existing = {
        (r.personnel_web_id, r.dataset): r
        for r in db.query(VpRecord).filter(VpRecord.personnel_web_id.in_(people)).all()
    } if people else {}

    now = datetime.now()
    email = idinfo["email"]
    stored = skipped = 0
    for item in body.records:
        if item.personnelWebId not in people:
            skipped += 1
            continue
        key = (item.personnelWebId, item.dataset)
        row = existing.get(key)
        if row is None:
            row = VpRecord(personnel_web_id=item.personnelWebId, dataset=item.dataset)
            db.add(row)
            existing[key] = row
        row.payload = item.payload
        row.synced_at = now
        row.synced_by = email
        stored += 1
    db.commit()
    return {"stored": stored, "skippedUnknownPerson": skipped}


@router.get("/status")
def sync_status(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """How fresh the VP data is: when the roster and each dataset last synced,
    and by whom. The extension popup shows this, as can the SMS site."""
    last = db.query(VpPerson).order_by(VpPerson.synced_at.desc()).first()
    rows = (
        db.query(VpRecord.dataset, func.count(VpRecord.id), func.max(VpRecord.synced_at))
        .group_by(VpRecord.dataset).all()
    )
    return {
        "people": db.query(VpPerson).count(),
        "lastRosterSync": {"at": last.synced_at.isoformat(), "by": last.synced_by} if last else None,
        "datasets": {
            name: {"count": count, "lastSyncedAt": at.isoformat() if at else None}
            for name, count, at in rows
        },
    }


@router.get("/people/{cin}")
def person_records(cin: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Everything synced from VP for one cadet or staff member, by CIN."""
    person = db.query(VpPerson).filter(VpPerson.cin == cin).first()
    if not person:
        raise HTTPException(status_code=404, detail="No VP data for this CIN")
    return {
        "personnelWebId": person.personnel_web_id,
        "cin": person.cin,
        "personType": person.person_type,
        "profile": person.profile,
        "syncedAt": person.synced_at.isoformat(),
        "records": {
            r.dataset: {"payload": r.payload, "syncedAt": r.synced_at.isoformat(), "syncedBy": r.synced_by}
            for r in person.records
        },
    }
