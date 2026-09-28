"""Stored quals mirror Bader, except when the tab failed to load."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Cadet, CadetQualification
from scripts.scraper_calls import remove_stale_qualifications


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(Cadet(cin=1, first_name="Alex", last_name="Smith"))
    for qt in ("Blue Leadership", "Djabaev_leadership.pdf", "FilenameDescriptionFile Size (kb)"):
        session.add(CadetQualification(cadet_id=1, qual_type=qt, status="true"))
    session.commit()
    yield session
    session.close()


def quals(db):
    return sorted(q.qual_type for q in db.query(CadetQualification))


def test_quals_bader_no_longer_lists_are_removed(db):
    cadet = db.get(Cadet, 1)
    assert remove_stale_qualifications(db, cadet, {"Blue Leadership"}) == 2
    db.commit()
    assert quals(db) == ["Blue Leadership"]


def test_failed_tab_load_removes_nothing(db):
    assert remove_stale_qualifications(db, db.get(Cadet, 1), None) == 0
    db.commit()
    assert len(quals(db)) == 3


def test_cadet_with_no_quals_in_bader_is_emptied(db):
    assert remove_stale_qualifications(db, db.get(Cadet, 1), set()) == 3
    db.commit()
    assert quals(db) == []
