"""Leaving process over HTTP: who gets listed, sending the email, cancelling.
The date arithmetic itself is pinned in test_leaving.py."""

from datetime import datetime, timedelta

import pytest

import routers.leaving as leaving
from database.models import Cadet, CadetAttendance, CadetLeavingProcess

STAFF = {"Authorization": "Bearer staff"}
TONIGHT = datetime(2026, 9, 30)


def _parade(db, cin, days_ago, status="Present Correctly Dressed", register="Parade Night"):
    db.add(CadetAttendance(cadet_id=cin, date=TONIGHT - timedelta(days=days_ago), status=status,
                           register_type=register))


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Here", last_name="Always", email="here@x"),
        Cadet(cin=2, first_name="Gone", last_name="Away", email="gone@x", rank="Cdt", flight="B"),
        Cadet(cin=3, first_name="Excused", last_name="Exams"),
        Cadet(cin=4, first_name="Brand", last_name="New"),
        Cadet(cin=5, first_name="Camp", last_name="Only"),
    ])
    _parade(db, 1, 0)
    _parade(db, 2, 35)
    _parade(db, 2, 0, status="Absent")               # absences don't break the gap
    _parade(db, 3, 0, status="Authorised Absence")   # authorised absence does
    _parade(db, 3, 60)
    _parade(db, 5, 0, register="Camp")               # other registers don't count
    _parade(db, 5, 40)
    db.commit()


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_staff_only(api, persona):
    h = api.as_(persona)
    assert api.get("/leaving-process", headers=h).status_code == 403
    assert api.post("/leaving-process/2/send", json={"to": "a@b.c", "replyTo": "a@b.c"}, headers=h).status_code == 403
    assert api.delete("/leaving-process/2", headers=h).status_code == 403


def test_list_flags_only_lapsed_cadets(api, squadron):
    body = api.get("/leaving-process", headers=STAFF).json()
    assert body["currentParadeNight"] == TONIGHT.isoformat()
    assert body["defaultReplyTo"]
    rows = {c["cin"]: c for c in body["cadets"]}
    assert set(rows) == {2, 5}
    gone = rows[2]
    assert (gone["gapDays"], gone["weeksAbsent"], gone["status"], gone["returned"]) == (35, 5, "flagged", False)
    assert gone["sentAt"] is None and gone["email"] == "gone@x"


def test_list_with_no_registers_at_all(api, db):
    db.add(Cadet(cin=1, first_name="A", last_name="B"))
    db.commit()
    assert api.get("/leaving-process", headers=STAFF).json() == {
        "currentParadeNight": None, "defaultReplyTo": leaving.LEAVING_PROCESS_REPLY_TO, "cadets": []}


def test_send_starts_the_clock_and_cancel_ends_it(api, db, squadron, outbox):
    res = api.post("/leaving-process/2/send", headers=STAFF,
                   json={"to": " parent@example.com ", "replyTo": "oc@317atc.co.uk"})
    assert res.status_code == 200 and res.json()["ok"] is True

    mail = outbox[0]
    assert mail["to"] == "parent@example.com" and mail["reply_to"] == "oc@317atc.co.uk"
    assert "Gone" in mail["html"] and "5" in mail["html"]

    row = {c["cin"]: c for c in api.get("/leaving-process", headers=STAFF).json()["cadets"]}[2]
    assert row["status"] == "waiting" and row["daysLeft"] == 14
    assert row["sentTo"] == "parent@example.com" and row["sentBy"] == "staff@317atc.co.uk"

    again = api.post("/leaving-process/2/send", headers=STAFF, json={"to": "a@b.co", "replyTo": "a@b.co"})
    assert again.status_code == 409
    assert len(outbox) == 1

    assert api.delete("/leaving-process/2", headers=STAFF).status_code == 204
    assert api.delete("/leaving-process/2", headers=STAFF).status_code == 404
    assert db.query(CadetLeavingProcess).count() == 0


def test_a_returned_cadet_stays_listed_until_cancelled(api, db, squadron):
    api.post("/leaving-process/2/send", headers=STAFF, json={"to": "a@b.co", "replyTo": "a@b.co"})
    _parade(db, 2, 0, status="Present Incorrectly Dressed")
    db.commit()
    row = {c["cin"]: c for c in api.get("/leaving-process", headers=STAFF).json()["cadets"]}[2]
    assert row["returned"] is True and row["gapDays"] == 0


def test_send_without_any_register_assumes_four_weeks(api, db, outbox):
    db.add(Cadet(cin=9, first_name="", last_name="X"))
    db.commit()
    assert api.post("/leaving-process/9/send", headers=STAFF, json={"to": "a@b.co", "replyTo": "a@b.co"}).status_code == 200
    # Blank first name falls back to the CIN rather than "Dear ,".
    assert "CIN 9" in outbox[0]["html"] and "4" in outbox[0]["html"]


@pytest.mark.parametrize("body", [
    {"to": "not-an-email", "replyTo": "a@b.co"},
    {"to": "a@b.co", "replyTo": ""},
    {"to": "a@b.co"},
    {},
])
def test_send_validates_addresses(api, squadron, body, outbox):
    assert api.post("/leaving-process/2/send", json=body, headers=STAFF).status_code == 422
    assert outbox == []


def test_send_to_unknown_cadet(api):
    res = api.post("/leaving-process/99/send", json={"to": "a@b.co", "replyTo": "a@b.co"}, headers=STAFF)
    assert res.status_code == 404
