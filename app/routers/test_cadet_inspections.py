"""A cadet reading their own inspection history on the portal.

The point of the endpoint is that it is self-scoped: the CIN comes from the
signed-in cadet's row, never from the request, so there is no shape of call that
returns another cadet's marks or notes. These tests pin that down alongside the
numbers the page shows.
"""

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import get_db
from core.security import require_user
from database.database import Base
from database.models import Cadet, InspectionSheet
from routers import portal

ADA = 1
BEN = 2


def mark(cin, score=None, absent=False, awol=False, comments=()):
    return {
        "cin": cin, "score": score, "absent": absent, "awol": awol,
        "comments": list(comments),
    }


@pytest.fixture
def client():
    # StaticPool: TestClient runs the app on another thread, and the default
    # pool would hand it a second — empty — in-memory database.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(Cadet(cin=ADA, first_name="Ada", last_name="Adams",
                 email="ada@317atc.co.uk", rank="Cadet", flight="A"))
    db.add(Cadet(cin=BEN, first_name="Ben", last_name="Brown",
                 email="ben@317atc.co.uk", rank="Cadet", flight="A"))
    db.add(InspectionSheet(
        date=datetime(2026, 1, 6), submitted_by="staff@317atc.co.uk",
        submitted_at=datetime(2026, 1, 6), data={
            "uniform": "blues",
            "marks": [
                mark(ADA, score=6, comments=[
                    {"type": "fault", "region": "Shoes", "text": "Toecaps unpolished"},
                    {"type": "positive", "region": "Beret / Headdress", "text": "Well shaped"},
                ]),
                mark(BEN, score=9, comments=[
                    {"type": "fault", "region": "Trousers", "text": "Ben's creases"},
                ]),
            ],
        },
    ))
    db.add(InspectionSheet(
        date=datetime(2026, 1, 13), submitted_by="staff@317atc.co.uk",
        submitted_at=datetime(2026, 1, 13), data={
            "uniform": "mtp",
            "marks": [
                mark(ADA, absent=True, awol=True),
                mark(BEN, score=8),
            ],
        },
    ))
    db.add(InspectionSheet(
        date=datetime(2026, 1, 20), submitted_by="staff@317atc.co.uk",
        submitted_at=datetime(2026, 1, 20), data={
            "uniform": "blues",
            # Ada's flight wasn't inspected this night — she isn't on the sheet.
            "marks": [mark(BEN, score=7)],
        },
    ))
    db.commit()

    app = FastAPI()
    app.include_router(portal.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_user] = lambda: {
        "sub": "google-ada", "email": "ada@317atc.co.uk",
    }

    yield TestClient(app), app, db
    db.close()


def test_a_cadet_sees_every_night_they_were_marked_on(client):
    api, _, _ = client
    body = api.get("/cadets/me/inspections").json()

    assert body["cin"] == ADA
    assert body["name"] == "Ada Adams"
    assert body["inspection_count"] == 2  # the third sheet didn't include her
    assert [e["date"] for e in body["timeline"]] == ["2026-01-06", "2026-01-13"]


def test_the_notes_come_through_in_full(client):
    api, _, _ = client
    first = api.get("/cadets/me/inspections").json()["timeline"][0]

    assert first["score"] == 6
    assert first["uniform"] == "blues"
    assert first["faults"] == [{"region": "Shoes", "text": "Toecaps unpolished"}]
    assert first["positives"] == [{"region": "Beret / Headdress", "text": "Well shaped"}]


def test_an_absent_night_is_shown_as_absent_not_as_a_zero(client):
    api, _, _ = client
    second = api.get("/cadets/me/inspections").json()["timeline"][1]

    assert second["absent"] is True
    assert second["awol"] is True
    assert second["score"] is None


def test_averages_are_over_the_nights_the_cadet_was_marked_on(client):
    api, _, _ = client
    body = api.get("/cadets/me/inspections").json()

    # Present 1 night of the 2 she was on the sheet for, scoring 6.
    assert body["present_count"] == 1
    assert body["attendance_avg"] == 50.0
    assert body["score_avg"] == 6.0
    assert body["overall"] == 3.0


def test_no_other_cadet_appears_anywhere_in_the_response(client):
    api, _, _ = client
    raw = api.get("/cadets/me/inspections").text

    # Ben shares every sheet with Ada — his name and his notes must not leak.
    assert "Ben" not in raw
    assert "Brown" not in raw
    assert "creases" not in raw


def test_each_cadet_gets_their_own_history(client):
    api, app, _ = client
    app.dependency_overrides[require_user] = lambda: {
        "sub": "google-ben", "email": "ben@317atc.co.uk",
    }

    body = api.get("/cadets/me/inspections").json()
    assert body["cin"] == BEN
    assert body["inspection_count"] == 3
    assert body["score_avg"] == 8.0


def test_a_cadet_with_no_inspections_gets_an_empty_history_not_an_error(client):
    api, app, db = client
    db.add(Cadet(cin=3, first_name="Cai", last_name="Clark", email="cai@317atc.co.uk"))
    db.commit()
    app.dependency_overrides[require_user] = lambda: {
        "sub": "google-cai", "email": "cai@317atc.co.uk",
    }

    resp = api.get("/cadets/me/inspections")
    assert resp.status_code == 200
    assert resp.json()["timeline"] == []
    assert resp.json()["score_avg"] == 0.0


def test_someone_with_no_cadet_record_is_told_so(client):
    api, app, _ = client
    app.dependency_overrides[require_user] = lambda: {
        "sub": "google-staff", "email": "staff@317atc.co.uk",
    }

    resp = api.get("/cadets/me/inspections")
    assert resp.status_code == 404
    assert "not registered" in resp.json()["detail"]
