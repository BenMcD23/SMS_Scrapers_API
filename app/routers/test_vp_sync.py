"""Self-check for the VP sync ingest: the two guards (dataset allow-list and the
317-roster CIN check) and roster pruning, which is the one destructive path."""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import app
from core.db import get_db
from core.security import require_staff
from database.database import Base
from database.models import Cadet, Staff, VpPerson, VpRecord

client = TestClient(app)


def _setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add_all([
        Cadet(cin=111, first_name="Ada", last_name="Cadet"),
        Cadet(cin=222, first_name="Bob", last_name="Cadet"),
        Staff(cin=900, first_name="Sam", last_name="Staff"),
    ])
    db.commit()

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_staff] = lambda: {"email": "staff@317atc.co.uk"}
    return db


def _person(wid, cin, kind="cadet", **profile):
    return {"personnelWebId": wid, "cin": cin, "personType": kind, "profile": profile}


def test_requires_staff():
    app.dependency_overrides.clear()
    assert client.get("/vp-sync/status").status_code == 401


def test_roster_only_keeps_317_people_and_whitelisted_fields():
    db = _setup()
    try:
        res = client.post("/vp-sync/people", json={"people": [
            _person("w-ada", 111, givenName="Ada", dateOfBirth="2010-01-01"),
            _person("w-sam", 900, "staff"),
            _person("w-other", 555),  # another unit's cadet
        ]})
        assert res.status_code == 200, res.text
        assert res.json() == {
            "accepted": 2, "skippedNotOnRoster": 1, "removed": 0, "acceptedIds": ["w-ada", "w-sam"],
        }
        ada = db.get(VpPerson, "w-ada")
        assert ada.profile == {"givenName": "Ada"}  # dateOfBirth dropped
        assert ada.synced_by == "staff@317atc.co.uk"
        assert db.get(VpPerson, "w-other") is None
    finally:
        app.dependency_overrides.clear()


def test_roster_with_nobody_from_317_is_refused():
    db = _setup()
    try:
        client.post("/vp-sync/people", json={"people": [_person("w-ada", 111)]})
        res = client.post("/vp-sync/people", json={"people": [_person("w-other", 555)]})
        assert res.status_code == 422
        # and nothing was pruned on the strength of it
        assert db.get(VpPerson, "w-ada") is not None
        assert client.post("/vp-sync/people", json={"people": []}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_records_upsert_and_reject_unknown_datasets():
    db = _setup()
    try:
        client.post("/vp-sync/people", json={"people": [_person("w-ada", 111)]})

        res = client.post("/vp-sync/records", json={"records": [
            {"personnelWebId": "w-ada", "dataset": "whts", "payload": [{"test": "L98"}]},
            {"personnelWebId": "w-nobody", "dataset": "whts", "payload": []},
        ]})
        assert res.json() == {"stored": 1, "skippedUnknownPerson": 1}

        client.post("/vp-sync/records", json={"records": [
            {"personnelWebId": "w-ada", "dataset": "whts", "payload": [{"test": "L98"}, {"test": "L144"}]},
        ]})
        rows = db.query(VpRecord).all()
        assert len(rows) == 1
        db.refresh(rows[0])
        assert len(rows[0].payload) == 2

        res = client.post("/vp-sync/records", json={"records": [
            {"personnelWebId": "w-ada", "dataset": "security_dbs", "payload": {}},
        ]})
        assert res.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_person_missing_from_a_later_roster_is_removed_with_their_records():
    db = _setup()
    try:
        client.post("/vp-sync/people", json={"people": [_person("w-ada", 111), _person("w-bob", 222)]})
        client.post("/vp-sync/records", json={"records": [
            {"personnelWebId": "w-bob", "dataset": "learning", "payload": []},
        ]})

        res = client.post("/vp-sync/people", json={"people": [_person("w-ada", 111)]})
        assert res.json()["removed"] == 1
        assert db.get(VpPerson, "w-bob") is None
        assert db.query(VpRecord).count() == 0

        status = client.get("/vp-sync/status").json()
        assert status["people"] == 1
        assert client.get("/vp-sync/people/222").status_code == 404
        assert client.get("/vp-sync/people/111").json()["personnelWebId"] == "w-ada"
    finally:
        app.dependency_overrides.clear()
