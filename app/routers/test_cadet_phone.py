"""Staff setting a cadet's text number from the cadet's own page.

The number is the same field the portal writes and the recipient list reads, so
what matters here is that it's cleaned on the way in and that clearing it takes
the cadet off the list rather than leaving an empty string behind.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from core.db import get_db
from core.security import require_staff, require_staff_or_nco, require_staff_or_snco
from database.database import Base
from database.models import Cadet
from routers import cadets
from texts.recipients import list_recipients


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
    db.add(Cadet(cin=1, first_name="Ada", last_name="Adams", rank="Corporal"))
    db.commit()

    app = FastAPI()
    app.include_router(cadets.router)
    app.dependency_overrides[get_db] = lambda: db
    for dep in (require_staff, require_staff_or_nco, require_staff_or_snco):
        app.dependency_overrides[dep] = lambda: {"email": "staff@317.org"}

    yield TestClient(app), db
    db.close()


def test_a_cadet_starts_with_no_number(client):
    api, _ = client
    assert api.get("/cadets/1").json()["phone_number"] is None


def test_staff_can_set_a_number_and_it_reaches_the_text_list(client):
    api, db = client
    assert api.patch("/cadets/1", json={"phone_number": "07700 900123"}).status_code == 200

    # Stored in the one shape everything else reads.
    assert api.get("/cadets/1").json()["phone_number"] == "07700900123"
    assert [r.phone_number for r in list_recipients(db)] == ["07700900123"]


def test_a_number_notify_cannot_text_is_refused(client):
    api, _ = client
    resp = api.patch("/cadets/1", json={"phone_number": "01234 567890"})
    assert resp.status_code == 400
    assert "UK mobile" in resp.json()["detail"]
    assert api.get("/cadets/1").json()["phone_number"] is None


def test_clearing_the_number_takes_the_cadet_off_the_list(client):
    api, db = client
    api.patch("/cadets/1", json={"phone_number": "07700900123"})

    api.patch("/cadets/1", json={"phone_number": ""})

    # NULL, not "" — an empty string would still be a row with a number on it.
    assert api.get("/cadets/1").json()["phone_number"] is None
    assert list_recipients(db) == []


def test_editing_another_field_leaves_the_number_alone(client):
    api, _ = client
    api.patch("/cadets/1", json={"phone_number": "07700900123"})

    api.patch("/cadets/1", json={"email": "ada@317.org"})

    assert api.get("/cadets/1").json()["phone_number"] == "07700900123"
