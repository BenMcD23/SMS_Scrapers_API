"""Self-check for the squadron-wide attendance endpoints.

Run: PYTHONPATH=app:. python -m routers.test_attendance

Covers what the page relies on: a night is (date, register type), the NCO counts
are the cadet counts narrowed by rank rather than a separate register, and the
drill-in marks up cadets — but never staff — as NCOs.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Cadet, CadetAttendance, Staff, StaffAttendance
import routers.attendance as att


def _idinfo() -> dict:
    return {"sub": "staff", "email": "staff@317atc.co.uk", "given_name": "Sam",
            "family_name": "Staff"}


def test():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    night = datetime(2026, 6, 5)
    camp = datetime(2026, 6, 6)

    # Two NCOs, one plain cadet, and a staff sergeant — who holds the rank but
    # is not on the cadet NCO team.
    corporal = Cadet(cin=101, first_name="Sam", last_name="Smith", rank="Corporal", flight="NCO")
    flt_sgt = Cadet(cin=102, first_name="Jo", last_name="Jones", rank="flight sergeant", flight="NCO")
    plain = Cadet(cin=103, first_name="Pat", last_name="Plain", rank="Cadet", flight="A")
    sgt_cfav = Staff(cin=201, first_name="Alex", last_name="Adams", rank="Sergeant")
    db.add_all([corporal, flt_sgt, plain, sgt_cfav])

    for cadet, status in ((corporal, "Present Correctly Dressed"),
                          (flt_sgt, "Authorised Absence"),
                          (plain, "Absent")):
        db.add(CadetAttendance(cadet_id=cadet.cin, date=night, register_type="Parade Night",
                               status=status))
    # A second register on the next day, NCOs only, so the two nights can't be
    # confused with each other.
    db.add(CadetAttendance(cadet_id=corporal.cin, date=camp, register_type="Camp",
                           status="Present Incorrectly Dressed"))
    db.add(StaffAttendance(staff_id=sgt_cfav.cin, date=night, register_type="Parade Night",
                           status="Present Correctly Dressed"))
    db.commit()

    nights = att.attendance_nights(db, _idinfo())
    assert [(n["date"], n["registerType"]) for n in nights] == [
        ("2026-06-06", "Camp"), ("2026-06-05", "Parade Night"),   # newest first
    ]

    parade = nights[1]
    assert parade["cadets"] == {"present": 1, "authorised": 1, "absent": 1}
    # The NCO counts are the cadet counts minus the plain cadet — and the staff
    # sergeant is nowhere in them.
    assert parade["ncos"] == {"present": 1, "authorised": 1, "absent": 0}
    assert parade["staff"] == {"present": 1, "authorised": 0, "absent": 0}

    # A register with no plain cadets on it counts the same either way.
    assert nights[0]["cadets"] == nights[0]["ncos"] == {"present": 1, "authorised": 0, "absent": 0}

    # ── the drill-in ─────────────────────────────────────────────────────────
    detail = att.attendance_night_detail(night.date(), "Parade Night", db, _idinfo())
    by_cin = {p["cin"]: p for p in detail["cadets"]}
    assert by_cin[corporal.cin]["isNco"] and by_cin[flt_sgt.cin]["isNco"]
    assert not by_cin[plain.cin]["isNco"]
    # Staff are never marked up, whatever rank they hold.
    assert detail["staff"][0]["rank"] == "Sergeant"
    assert not detail["staff"][0]["isNco"]

    # The other register's rows stay out of this night.
    assert len(detail["cadets"]) == 3

    print("attendance self-check passed")


if __name__ == "__main__":
    test()
