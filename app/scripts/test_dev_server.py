"""The local preview seed (scripts/dev_server.py). It's what every UI preview
and screenshot runs against, so it has to keep working as the models move."""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.security as security
from core.qualifications import BADGE_TYPES
from database.database import Base
from database.models import Cadet, CadetAttendance, CadetQualification, Staff
from scripts.dev_server import CADET_COUNT, PARADE_NIGHTS, _parade_nights, seed, seed_stores

NOW = datetime(2026, 10, 8, 18, 0)  # a Thursday


def fresh_session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng)()


def test_seeds_a_squadron_into_an_empty_database(db):
    assert seed(db, now=NOW) is True
    assert db.query(Cadet).count() == CADET_COUNT
    assert db.query(CadetAttendance).count() == CADET_COUNT * PARADE_NIGHTS
    assert db.query(Cadet).filter(Cadet.banned).count() == 1
    # Flights are stored as the letter, as the scraper stores them.
    assert {c.flight for c in db.query(Cadet)} <= {"A", "B", "C"}


def test_never_touches_a_database_that_already_has_cadets(db):
    # Someone's been clicking around in it; a restart must not add a second squadron.
    db.add(Cadet(cin=1, first_name="Kept", last_name="Mine"))
    db.commit()
    assert seed(db, now=NOW) is False
    assert [c.first_name for c in db.query(Cadet)] == ["Kept"]


def test_the_dev_logins_are_on_the_roster(db):
    seed(db, now=NOW)
    emails = {c.email: c.rank for c in db.query(Cadet)}
    assert emails[security._dev_fake_email("snco")] == "Sgt"
    assert emails[security._dev_fake_email("nco")] == "Cpl"
    # The dev staff login is the owner; Settings needs a staff record to link to.
    assert db.query(Staff).filter(Staff.email == security.OWNER_EMAIL).count() == 1


def test_every_seeded_qualification_counts_toward_a_dashboard_badge(db):
    # A made-up name ("first_aid") would leave the badge bars at 0% and the
    # expiring list showing raw keys — the preview would look broken when it isn't.
    seed(db, now=NOW)
    levels = [lvl for b in BADGE_TYPES for lvl in b.levels]
    quals = db.query(CadetQualification).all()
    assert quals
    for q in quals:
        assert any(lvl.matches(q.qual_type) for lvl in levels), q.qual_type


def test_first_aid_expiries_include_lapsed_and_upcoming(db):
    seed(db, now=NOW)
    expiries = [q.date_expires for q in db.query(CadetQualification) if q.date_expires]
    assert any(d < NOW for d in expiries)
    assert any(d > NOW for d in expiries)


def test_is_the_same_squadron_every_time():
    a, b = fresh_session(), fresh_session()
    seed(a, now=NOW)
    seed(b, now=NOW)
    names = lambda s: [(c.cin, c.first_name, c.last_name, c.rank) for c in s.query(Cadet).order_by(Cadet.cin)]  # noqa: E731
    assert names(a) == names(b)


def test_parade_nights_are_the_mondays_before_today():
    nights = _parade_nights(NOW, 3)
    assert [n.date().isoformat() for n in nights] == ["2026-09-21", "2026-09-28", "2026-10-05"]
    # On a Monday, that Monday hasn't been registered yet.
    assert _parade_nights(datetime(2026, 10, 5, 9), 1)[0].date().isoformat() == "2026-09-28"


def test_stores_seed_goes_through_the_api(api, db):
    seed(db, now=NOW)
    staff = api.as_("staff")
    seed_stores(api, staff)

    orders = api.get("/stores/orders", headers=staff).json()
    assert len(orders) == 4
    assert all(not o["completed"] for o in orders)
    assert any(i["needSizing"] for o in orders for i in o["items"])
    assert api.get("/stores/stock", headers=staff).json()
    assert len(api.get("/stores/badges/orders", headers=staff).json()) == 1
