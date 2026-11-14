"""Staff compliance: DBS, BPSS and mandatory training from the Volunteer Portal.

The data comes from the portal sync (routers/volunteer_portal.py); the rules
for what is due are in core/staff_compliance.py. The OC sees everyone; any
other staff member sees only their own row, the same rule as staff addresses
and attendance (routers/portal.py).
"""

from collections import defaultdict
from datetime import date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core import staff_compliance as sc
from core.db import get_db
from core.security import is_oc, require_staff
from database.models import Staff, StaffPortalData
from routers.portal import own_staff

router = APIRouter()


def _item(i: dict) -> dict:
    return {**i,
            "achieved": i["achieved"].isoformat() if i["achieved"] else None,
            "expires": i["expires"].isoformat() if i["expires"] else None}


def compliance_rows(db: Session, today: date, staff: list[Staff] | None = None) -> list[dict]:
    """Each member of staff with their items (None if never synced), their
    worst status and when the portal was last read for them."""
    staff = staff if staff is not None else db.query(Staff).order_by(Staff.last_name, Staff.first_name).all()
    stored: dict[int, dict] = defaultdict(dict)
    synced: dict[int, datetime] = {}
    q = db.query(StaffPortalData).filter(StaffPortalData.staff_id.in_([s.cin for s in staff]))
    for row in q:
        stored[row.staff_id][row.dataset] = row.data
        synced[row.staff_id] = max(synced.get(row.staff_id, row.synced_at), row.synced_at)
    rows = []
    for s in staff:
        items = sc.items_for(stored.get(s.cin, {}), today)
        rows.append({
            "cin": s.cin,
            "name": f"{s.first_name} {s.last_name}".strip(),
            "rank": s.rank,
            "email": s.email,
            "items": items,
            "status": sc.worst(items),
            "synced_at": synced[s.cin].isoformat() if s.cin in synced else None,
        })
    return rows


@router.get("/staff/compliance")
def staff_compliance(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    email = idinfo.get("email", "")
    if is_oc(email):
        people = None
    else:
        me = own_staff(db, email)
        people = [me] if me else []
    rows = compliance_rows(db, date.today(), people)
    for r in rows:
        r["items"] = [_item(i) for i in r["items"]] if r["items"] is not None else None
        # The weekly email needs it; the page doesn't, so it isn't sent.
        del r["email"]
    return {"everyone": people is None, "due_within_days": sc.DUE_WINDOW.days, "staff": rows}
