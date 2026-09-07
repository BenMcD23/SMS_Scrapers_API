"""Staff who've left SMS stop being staff — and stop getting the parade text.

The empty-scrape guard is the one worth pinning hardest: a failed table load
returning no rows must not read as "the whole squadron resigned".
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Staff, StaffAttendance
from scripts.staff_scraper import remove_departed_staff
from texts.recipients import list_recipients


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Staff(cin=10, first_name="Zoe", last_name="Zephyr", rank="Flight Lieutenant",
              phone_number="07700900010"),
        Staff(cin=11, first_name="Sid", last_name="Smith", rank="Sergeant",
              phone_number="07700900011"),
        StaffAttendance(staff_id=11, date=datetime(2026, 9, 4), register_type="PC",
                        status="P", unit="317"),
    ])
    session.commit()
    yield session
    session.close()


def test_staff_not_scraped_are_deleted_with_their_attendance(db):
    assert remove_departed_staff(db, [10]) == 1

    assert [s.cin for s in db.query(Staff).all()] == [10]
    # SQLite doesn't enforce the FK cascade, so this is the explicit delete.
    assert db.query(StaffAttendance).count() == 0


def test_departed_staff_stop_receiving_the_parade_text(db):
    assert "07700900011" in [r.phone_number for r in list_recipients(db)]

    remove_departed_staff(db, [10])

    assert [r.phone_number for r in list_recipients(db)] == ["07700900010"]


def test_an_empty_scrape_deletes_nobody(db):
    assert remove_departed_staff(db, []) == 0
    assert db.query(Staff).count() == 2


def test_a_full_scrape_deletes_nobody(db):
    assert remove_departed_staff(db, [10, 11]) == 0
    assert db.query(Staff).count() == 2
