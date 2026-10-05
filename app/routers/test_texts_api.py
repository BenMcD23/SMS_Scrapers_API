"""Parade-night texts over HTTP: the access gate, editing and sending a night,
the scheduled send job, recipients CRUD and file imports. Generation itself is
covered in test_texts.py; GOV.UK Notify is replaced by a recorder here."""

import io
from datetime import datetime, timedelta

import openpyxl
import pytest

import routers.texts as tx
import texts.sender as sender
from database.models import Cadet, ParadeNightMessage, SmsRecipient, Staff

STAFF = {"Authorization": "Bearer staff"}


class FakeNotify:
    def __init__(self, fail_numbers=()):
        self.sent = []
        self.fail_numbers = set(fail_numbers)

    def send_sms_notification(self, phone_number, template_id, personalisation):
        if phone_number in self.fail_numbers:
            raise RuntimeError("invalid number")
        self.sent.append((phone_number, personalisation))


@pytest.fixture
def notify(monkeypatch):
    fake = FakeNotify()
    monkeypatch.setattr(sender, "_notify_client", lambda: fake)
    monkeypatch.setattr(sender, "NOTIFY_SMS_TEMPLATE_ID", "tmpl")
    return fake


def _message(db, day=datetime(2026, 9, 2), status="ready", **kw):
    m = ParadeNightMessage(parade_date=day, uniform="Blues", dnco="Cpl X", main_message="Drill night",
                           c_flight_message="Kit issue", status=status, generated_at=datetime.now(), **kw)
    db.add(m)
    db.commit()
    return m


@pytest.mark.parametrize("method,path", [
    ("get", "/texts/messages"), ("patch", "/texts/messages/1"), ("post", "/texts/messages/1/send"),
    ("post", "/texts/messages/1/test-send"), ("post", "/texts/messages/1/regenerate"),
    ("post", "/texts/generate"), ("post", "/texts/generate/night"), ("get", "/texts/settings"),
    ("patch", "/texts/settings"), ("get", "/texts/recipients"), ("post", "/texts/recipients"),
    ("patch", "/texts/recipients/cadet:1"), ("delete", "/texts/recipients/cadet:1"),
    ("post", "/texts/recipients/import"),
])
@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_texts_are_staff_only(api, method, path, persona):
    call = getattr(api, method)
    h = api.as_(persona)
    res = call(path, headers=h) if method in ("get", "delete") else call(path, json={}, headers=h)
    assert res.status_code == 403


# ── messages ──────────────────────────────────────────────────────────────────

def test_list_messages_for_a_month(api, db):
    _message(db, datetime(2026, 9, 2))
    _message(db, datetime(2026, 9, 4))
    _message(db, datetime(2026, 10, 2))
    got = api.get("/texts/messages?month=9&year=2026", headers=STAFF).json()
    assert [m["parade_date"][:10] for m in got] == ["2026-09-02", "2026-09-04"]
    assert api.get("/texts/messages?month=1&year=2020", headers=STAFF).json() == []


def test_patch_message(api, db):
    m = _message(db, status="draft")
    res = api.patch(f"/texts/messages/{m.id}", json={"main_message": "New", "status": "ready"}, headers=STAFF)
    assert res.status_code == 200
    assert (res.json()["main_message"], res.json()["status"], res.json()["uniform"]) == ("New", "ready", "Blues")


@pytest.mark.parametrize("status", ["sent", "bogus", ""])
def test_patch_rejects_other_statuses(api, db, status):
    m = _message(db, status="draft")
    assert api.patch(f"/texts/messages/{m.id}", json={"status": status}, headers=STAFF).status_code == 400


def test_a_sent_message_is_frozen(api, db, notify):
    m = _message(db, status="sent")
    assert api.patch(f"/texts/messages/{m.id}", json={"dnco": "x"}, headers=STAFF).status_code == 400
    assert api.post(f"/texts/messages/{m.id}/send", headers=STAFF).status_code == 400
    assert api.post(f"/texts/messages/{m.id}/regenerate", headers=STAFF).status_code == 400
    assert notify.sent == []


def test_unknown_message_is_404(api):
    for method, path in (("patch", "/texts/messages/9"), ("post", "/texts/messages/9/send"),
                         ("post", "/texts/messages/9/regenerate")):
        assert getattr(api, method)(path, json={}, headers=STAFF).status_code == 404
    assert api.post("/texts/messages/9/test-send", json={"phone_number": "07700900123"},
                    headers=STAFF).status_code == 404


def test_regenerate_ai_failure_is_a_502(api, db, monkeypatch):
    m = _message(db, status="draft")

    def down(*a):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(tx, "generate_message", down)
    res = api.post(f"/texts/messages/{m.id}/regenerate", headers=STAFF)
    assert res.status_code == 502 and "quota exhausted" in res.json()["detail"]


# ── sending ───────────────────────────────────────────────────────────────────

def _recipients(db):
    db.add_all([
        Cadet(cin=1, first_name="A", last_name="Able", rank="Cpl", phone_number="+447700900001"),
        Cadet(cin=2, first_name="B", last_name="Baker", phone_number=None),
        Staff(cin=3, first_name="S", last_name="Smith", rank="Sgt", phone_number="+447700900003"),
        SmsRecipient(rank="Mrs", surname="Parent", phone_number="+447700900004"),
    ])
    db.commit()


def test_send_reaches_everyone_and_records_failures(api, db, notify):
    _recipients(db)
    notify.fail_numbers = {"+447700900004"}
    m = _message(db)
    res = api.post(f"/texts/messages/{m.id}/send", headers=STAFF).json()
    assert (res["sent"], res["failed"]) == (2, 1)
    assert res["message"]["status"] == "sent" and res["message"]["sent_at"]
    results = {r["phone"]: r for r in res["message"]["send_results"]}
    assert results["+447700900004"]["status"] == "failed" and results["+447700900004"]["error"] == "invalid number"
    body = notify.sent[0][1]["body"]
    assert body == "Uniform: Blues\n\nDrill night\n\nC Flight\nKit issue\n\nDNCO: Cpl X"


def test_send_with_no_recipients(api, db, monkeypatch):
    m = _message(db)
    monkeypatch.setattr(sender, "_notify_client", lambda: FakeNotify())
    res = api.post(f"/texts/messages/{m.id}/send", headers=STAFF)
    assert res.status_code == 400 and "No SMS recipients" in res.json()["detail"]



def test_send_without_notify_configured(api, db, monkeypatch):
    monkeypatch.setattr(sender, "NOTIFY_API_KEY", None)
    _recipients(db)
    m = _message(db)
    res = api.post(f"/texts/messages/{m.id}/send", headers=STAFF)
    assert res.status_code == 400 and "not configured" in res.json()["detail"]
    db.refresh(m)
    assert m.status == "ready"


def test_message_body_skips_empty_sections():
    m = ParadeNightMessage(uniform="", main_message="Only this", c_flight_message="", dnco="")
    assert sender.build_message_body(m) == "Only this"


def test_test_send(api, db, notify):
    m = _message(db)
    assert api.post(f"/texts/messages/{m.id}/test-send", json={"phone_number": " 07700900123 "},
                    headers=STAFF).json() == {"status": "success"}
    assert notify.sent[0][0] == "07700900123"
    assert notify.sent[0][1]["rank"] == "Test"

    notify.fail_numbers = {"bad"}
    res = api.post(f"/texts/messages/{m.id}/test-send", json={"phone_number": "bad"}, headers=STAFF)
    assert res.status_code == 502


# ── scheduled send ────────────────────────────────────────────────────────────

def _tomorrow():
    t = (datetime.now(sender.LONDON) + timedelta(days=1)).date()
    return datetime(t.year, t.month, t.day, 0, 0)


def test_scheduled_send_sends_a_ready_message(db, session_factory, notify, outbox):
    _recipients(db)
    m = _message(db, day=_tomorrow(), status="ready")
    sender.scheduled_send_job()
    db.refresh(m)
    assert m.status == "sent" and len(notify.sent) == 3 and outbox == []


def test_scheduled_send_alerts_when_some_fail(db, session_factory, notify, outbox):
    _recipients(db)
    notify.fail_numbers = {"+447700900001"}
    _message(db, day=_tomorrow(), status="ready")
    sender.scheduled_send_job()
    assert "1 of 3 messages failed" in outbox[0]["html"]


@pytest.mark.parametrize("status,expect", [(None, "no message has been generated"),
                                            ("draft", "not marked as ready"), ("sent", None)])
def test_scheduled_send_alerts_staff(db, session_factory, notify, outbox, status, expect):
    if status:
        _message(db, day=_tomorrow(), status=status)
    sender.scheduled_send_job()
    if expect:
        assert expect in outbox[0]["html"]
    else:
        assert outbox == []
    assert notify.sent == []


def test_scheduled_send_never_raises(db, session_factory, monkeypatch):
    monkeypatch.setattr(sender, "_notify_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    _message(db, day=_tomorrow(), status="ready")
    sender.scheduled_send_job()  # logged, not raised


# ── recipients ────────────────────────────────────────────────────────────────

def test_recipient_crud(api, db):
    _recipients(db)
    listed = {r["key"]: r for r in api.get("/texts/recipients", headers=STAFF).json()}
    assert set(listed) == {"cadet:1", "staff:3", next(k for k in listed if k.startswith("extra:"))}
    assert listed["cadet:1"]["cin"] == 1

    created = api.post("/texts/recipients", json={"rank": " Mr ", "surname": " Dad ", "phone_number": "07700 900 555"},
                       headers=STAFF)
    assert created.status_code == 200
    key = created.json()["key"]
    assert created.json()["rank"] == "Mr" and created.json()["phone_number"].endswith("900555")

    assert api.post("/texts/recipients", json={"phone_number": ""}, headers=STAFF).status_code == 400
    assert api.post("/texts/recipients", json={"phone_number": "12"}, headers=STAFF).status_code == 400
    assert api.patch(f"/texts/recipients/{key}", json={"phone_number": ""}, headers=STAFF).status_code == 400
    assert api.patch(f"/texts/recipients/{key}", json={"surname": "Father"}, headers=STAFF).json()["surname"] == "Father"
    assert api.delete(f"/texts/recipients/{key}", headers=STAFF).json() == {"status": "success"}
    assert api.delete(f"/texts/recipients/{key}", headers=STAFF).status_code == 404


@pytest.mark.parametrize("key", ["nonsense", "cadet:", "cadet:abc", "robot:1", "staff:999"])
def test_bad_recipient_keys(api, db, key):
    assert api.patch(f"/texts/recipients/{key}", json={}, headers=STAFF).status_code == 404


# ── imports ───────────────────────────────────────────────────────────────────

def _upload(api, name, content, mode="merge"):
    return api.post("/texts/recipients/import", files={"file": (name, content, "text/plain")},
                    data={"mode": mode}, headers=STAFF)


def test_import_csv_tsv_and_xlsx(api, db):
    _recipients(db)
    csv = "﻿Rank,Surname,Phone Number\nCpl,Able,07700 900 111\nMr,Unknown,07700900222\n,,\nx,y,\n"
    res = _upload(api, "list.csv", csv.encode()).json()
    assert (res["imported"], res["matched"], res["extras"], res["skipped"]) == (2, 1, 1, 1)
    db.expire_all()
    assert db.get(Cadet, 1).phone_number.endswith("900111")

    tsv = "rank\tsurname\tphone number\nMrs\tParent\t07700900333\n"
    assert _upload(api, "list.txt", tsv.encode()).json()["imported"] == 1

    wb = openpyxl.Workbook()
    wb.active.append(["Phone Number", "Surname"])
    wb.active.append([7700900444, "Xlsx"])
    buf = io.BytesIO()
    wb.save(buf)
    res = _upload(api, "list.xlsx", buf.getvalue())
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("name,content,detail", [
    ("a.csv", b"", "empty"),
    ("a.csv", b"rank,surname\nCpl,Able\n", "'phone number' column"),
    ("a.csv", b"phone number\n\n,\n", "No rows"),
    ("a.xlsx", b"not a zip", "Could not read"),
])
def test_import_rejections(api, name, content, detail):
    res = _upload(api, name, content)
    assert res.status_code == 400 and detail in res.json()["detail"]


def test_import_bad_mode(api):
    assert _upload(api, "a.csv", b"phone number\n07700900123\n", mode="append").status_code == 400


def test_replace_import_wipes_only_extras(api, db):
    _recipients(db)
    res = _upload(api, "a.csv", b"phone number,surname\n07700900999,New\n", mode="replace").json()
    assert res["extras"] == 1
    assert [r.surname for r in db.query(SmsRecipient).all()] == ["New"]
    db.expire_all()
    assert db.get(Cadet, 1).phone_number == "+447700900001"


def test_import_matching_a_person_removes_their_duplicate_extra(api, db):
    _recipients(db)
    db.add(SmsRecipient(rank="Cpl", surname="Able", phone_number="+447700900777"))
    db.commit()
    _upload(api, "a.csv", b"rank,surname,phone number\nCpl,Able,07700900777\n")
    assert all(r.phone_number != "+447700900777" for r in db.query(SmsRecipient).all())
