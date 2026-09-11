"""Only the assessor who created a sheet (or staff) may edit it.

The sheet carries the original assessor's name and signature, so an edit by
anyone else would rewrite the marks under their name.
"""
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import routers.assessments as a
from core.db import get_or_create_user
from database.database import Base
from database.models import AssessmentSheet, Cadet


def _idinfo(name: str) -> dict:
    return {"sub": name, "email": f"{name}@x", "given_name": name, "family_name": "T"}


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    # No Google group lookups or PDF rendering in a unit test.
    monkeypatch.setattr(a, "get_user_role", lambda email: "staff" if email == "staff@x" else "nco")
    monkeypatch.setattr(a, "_leadership_fields_and_pdf", lambda data: ({"edited": True}, b"%PDF"))
    yield session
    session.close()


def _sheet(db, assessor) -> AssessmentSheet:
    cadet = Cadet(cin=1, first_name="C", last_name="Adet", email="c@x", rank="Cadet", flight="A", banned=False)
    db.add(cadet)
    sheet = AssessmentSheet(
        assessment_type="Blue Leadership", fields={"assessor_name": "Alice"}, created_at=datetime.now(),
        cadet_id=1, assessor_id=assessor.id,
    )
    db.add(sheet)
    db.commit()
    return sheet


def test_another_nco_cannot_edit(db):
    alice, bob = _idinfo("alice"), _idinfo("bob")
    sheet = _sheet(db, get_or_create_user(db, alice))
    get_or_create_user(db, bob)
    with pytest.raises(HTTPException) as err:
        a.edit_assessment(sheet.id, {}, db, bob)
    assert err.value.status_code == 403
    assert a.get_assessment_detail(sheet.id, db, bob)["editable"] is False


def test_assessor_and_staff_can_edit(db):
    alice, staff = _idinfo("alice"), _idinfo("staff")
    sheet = _sheet(db, get_or_create_user(db, alice))
    assert a.edit_assessment(sheet.id, {}, db, alice)["status"] == "success"
    assert a.get_assessment_detail(sheet.id, db, alice)["editable"] is True
    assert a.edit_assessment(sheet.id, {}, db, staff)["status"] == "success"
    assert a.get_assessment_detail(sheet.id, db, staff)["is_mine"] is False


def test_content_disposition_strips_header_breaking_characters():
    from core.http import content_disposition
    assert content_disposition("inline", 'a"b\r\nX: y.pdf') == 'inline; filename="abX: y.pdf"'
    assert content_disposition("attachment", "", "file") == 'attachment; filename="file"'
