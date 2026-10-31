"""The programme as a record: import months from the programme doc, and set
what each flight did against who was there (core/programme.py does the work).

Separate from routers/programme.py, which publishes the programme PDF to the
website: that one ships the doc as it is, this one reads what's in it.

Reading the stats is open to NCOs as well as staff, like the rest of the stats
page: they're squadron turnout figures, with no names in them. Importing,
and one cadet's nights, are staff only; a backfill of many months is the
owner's, as it can run for minutes and spends model calls.
"""

import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import Text, cast, func
from sqlalchemy.orm import Session

import database.database as database
from core import programme
from core.db import get_db
from core.security import require_owner, require_staff, require_staff_or_nco
from database.models import ProgrammeNight, ProgrammeSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/programme", tags=["programme"])

# The earliest month a backfill may ask for: the docs go back to 2023, and a
# typo shouldn't send it walking through a decade of missing folders.
EARLIEST = date(2023, 1, 1)
MAX_BACKFILL_MONTHS = 48


class ImportMonth(BaseModel):
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2023, le=2100)
    use_ai: bool = True


class Backfill(BaseModel):
    # "YYYY-MM"; `to` defaults to next month, as the programme is written ahead.
    start: str = Field(pattern=r"^\d{4}-\d{2}$")
    end: str | None = Field(None, pattern=r"^\d{4}-\d{2}$")
    use_ai: bool = True


def _month(value: str) -> date:
    try:
        return date(int(value[:4]), int(value[5:7]), 1)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{value} isn't a month (use YYYY-MM)")


def _next_month(today: date) -> date:
    return date(today.year + today.month // 12, today.month % 12 + 1, 1)


@router.get("/months")
def imported_months(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> list[dict]:
    """Each month that has been imported: nights, sessions and when, newest first."""
    # "YYYY-MM" from the date: casting a date to text gives ISO on both
    # Postgres and SQLite, so this needs no dialect switch.
    month = func.substr(cast(ProgrammeNight.date, Text), 1, 7)
    rows = (db.query(month.label("month"), func.count(func.distinct(ProgrammeNight.id)),
                     func.count(ProgrammeSession.id), func.max(ProgrammeNight.imported_at))
            .outerjoin(ProgrammeSession).group_by(month).order_by(month.desc()).all())
    return [{"month": m, "nights": n, "sessions": s, "imported_at": at.isoformat() if at else None}
            for m, n, s, at in rows]


@router.post("/import")
async def import_month(body: ImportMonth, db: Session = Depends(get_db),
                       idinfo: dict = Depends(require_staff)) -> dict:
    """Import one month now, replacing what was there for it."""
    try:
        return await run_in_threadpool(programme.import_month, db, body.month, body.year, body.use_ai)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        # The doc is there but isn't the table we know how to read.
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("programme import failed")
        raise HTTPException(status_code=502, detail="Couldn't read the programme doc from Google Drive")


def _backfill_job(months: list[tuple[int, int]], use_ai: bool) -> None:
    with database.SessionLocal() as db:
        results = programme.import_months(db, months, use_ai=use_ai)
    done = [r for r in results if not r.get("skipped")]
    logger.info(f"programme backfill: {len(done)} of {len(results)} months imported")


@router.post("/backfill", status_code=202)
def backfill(body: Backfill, background: BackgroundTasks,
             idinfo: dict = Depends(require_owner)) -> dict:
    """Import every month from `start` to `end` in the background. Months with
    no doc are skipped; /programme/months shows what landed."""
    start = _month(body.start)
    end = _month(body.end) if body.end else _next_month(date.today())
    if start < EARLIEST:
        raise HTTPException(status_code=422, detail=f"The programme docs start in {EARLIEST:%B %Y}")
    if end < start:
        raise HTTPException(status_code=422, detail="The end month is before the start")
    months = programme.months_between(start, end)
    if len(months) > MAX_BACKFILL_MONTHS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_BACKFILL_MONTHS} months at a time")
    background.add_task(_backfill_job, months, body.use_ai)
    return {"months": len(months), "from": body.start, "to": f"{end:%Y-%m}"}


@router.get("/nights")
def nights(start: date, end: date, db: Session = Depends(get_db),
           idinfo: dict = Depends(require_staff_or_nco)) -> list[dict]:
    """What ran on each night between two dates, merged across flights."""
    if (end - start).days > 400:
        raise HTTPException(status_code=422, detail="At most about a year at a time")
    rows = (db.query(ProgrammeNight).filter(ProgrammeNight.date >= start, ProgrammeNight.date <= end)
            .order_by(ProgrammeNight.date).all())
    return [{"date": n.date.isoformat(), "uniform": n.uniform, "duty_nco": n.duty_nco,
             "sessions": programme._night_summary(n)} for n in rows]


@router.get("/stats")
def stats(days: int = 365, start: date | None = None, end: date | None = None,
          db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)) -> dict:
    """Turnout by kind of night, by activity, and night by night: the last
    `days`, or from `start` to `end` (the stats page's custom range)."""
    if start and end:
        if end < start:
            raise HTTPException(status_code=422, detail="The end date is before the start")
        result = programme.programme_stats(db, days=min((end - start).days, 1095), today=end)
    else:
        result = programme.programme_stats(db, max(7, min(days, 1095)))
    return {**result, "categories_all": [{"key": k, "label": v} for k, v in programme.CATEGORIES.items()]}


@router.get("/cadets/{cin}")
def cadet(cin: int, days: int = 365, db: Session = Depends(get_db),
          idinfo: dict = Depends(require_staff)) -> dict:
    """One cadet's nights against the programme, with turnout by category."""
    result = programme.cadet_sessions(db, cin, max(7, min(days, 1095)))
    if "error" in result:
        raise HTTPException(status_code=404, detail="Cadet not found")
    return result
