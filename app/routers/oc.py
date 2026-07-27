"""OC dashboard — a single OC-only aggregate endpoint.

Real data where it exists (squadron strength, staff attendance, expiring
qualifications); the committee-request and stubbed sections live on the frontend.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.models import Cadet, CadetQualification, Staff

from core import cache
from core.db import get_db
from core.qualifications import quali_expiry_cutoff
from core.security import require_oc
from routers.stats import STATS_CACHE_KEY, STATS_CACHE_TTL, compute_stats

router = APIRouter()


@router.get("/oc/dashboard")
async def oc_dashboard(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_oc),
):
    # Squadron strength — reuse the stats cache the home page already warms.
    strength = cache.get(STATS_CACHE_KEY)
    if strength is None:
        strength = compute_stats(db)
        cache.set(STATS_CACHE_KEY, strength, STATS_CACHE_TTL)

    # Staff attendance — pass through the scraped per-month JSON.
    staff_rows = db.query(Staff).order_by(Staff.last_name, Staff.first_name).all()
    staff_attendance = [
        {
            "cin": s.cin,
            "name": f"{s.first_name} {s.last_name}".strip(),
            "rank": s.rank or "",
            "attendance": s.attendance or {},
        }
        for s in staff_rows
    ]

    # Qualifications expiring within the next 3 months (everything in-window, not
    # just the un-notified ones the weekly email dedupes on).
    now = datetime.now()
    today = datetime(now.year, now.month, now.day)
    quals = (
        db.query(CadetQualification)
        .join(Cadet)
        .filter(
            CadetQualification.date_expires >= today,
            CadetQualification.date_expires <= quali_expiry_cutoff(today),
        )
        .order_by(CadetQualification.date_expires)
        .all()
    )
    expiring_quals = [
        {
            "cadet_name": f"{q.cadet.first_name} {q.cadet.last_name}".strip(),
            "qual_type": q.qual_type,
            "date_expires": q.date_expires.strftime("%d/%m/%Y"),
            "days_left": (q.date_expires - now).days,
        }
        for q in quals
    ]

    return {
        "strength": strength,
        "staff_attendance": staff_attendance,
        "expiring_quals": expiring_quals,
    }
