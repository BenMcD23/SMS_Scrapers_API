"""How likely each cadet is to be in on a given night, for the planners.

Shared by the classification day planner and the session planner so both flag
the same cadets: the parade-night register over the lookback plus any booked
absence covering the night, in one query each. The rules themselves are
``attendance_outlook`` in core/classification_plan.py.
"""

from collections import defaultdict
from datetime import date as Date
from datetime import datetime

from sqlalchemy import and_
from sqlalchemy.orm import Session

from core.classification_plan import LOOKBACK, attendance_outlook
from database.models import CadetAbsence, CadetAttendance

PARADE_NIGHT = "Parade Night"


def night_outlooks(db: Session, night: Date, cins) -> dict[int, dict]:
    """``attendance_outlook`` for each of ``cins`` on ``night``."""
    since = datetime.combine(night - LOOKBACK, datetime.min.time())
    register: dict[int, list] = defaultdict(list)
    for cin, when, status in db.query(
        CadetAttendance.cadet_id, CadetAttendance.date, CadetAttendance.status
    ).filter(CadetAttendance.register_type == PARADE_NIGHT, CadetAttendance.date >= since):
        register[cin].append((when.date(), status))

    at = datetime.combine(night, datetime.min.time())
    absent = {
        cin for (cin,) in db.query(CadetAbsence.cadet_id).filter(
            and_(CadetAbsence.date_from <= at, CadetAbsence.date_to >= at)
        )
    }
    return {cin: attendance_outlook(register[cin], night, cin in absent) for cin in cins}
