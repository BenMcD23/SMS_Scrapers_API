"""Hardening added around the public Funnel exposure: the audit trail on
sensitive actions, per-caller rate limits, and the newsletter upload cap.
(Docs gating and the startup guard live in core/test_config_guard.py.)"""

from datetime import datetime

import pytest

import routers.backups as backups
import routers.texts as tx
from database.models import (
    AuditEvent,
    Cadet,
    CadetMedical,
    CommitteeRequest,
    CommitteeRequestReceipt,
    ParadeNightMessage,
    SmsRecipient,
    User,
)

STAFF = {"Authorization": "Bearer staff"}
OWNER = {"Authorization": "Bearer owner"}


def _events(db, **kw):
    db.expire_all()
    return db.query(AuditEvent).filter_by(**kw).all()


# ── audit trail ───────────────────────────────────────────────────────────────

def test_reading_the_medical_audit_is_recorded(api, db):
    db.add_all([Cadet(cin=1, first_name="A", last_name="Able"), CadetMedical(cadet_id=1, allergy_name="Nuts")])
    db.commit()
    assert api.get("/cadets/audit/medical", headers=STAFF).status_code == 200
    (row,) = _events(db, resource="cadet_medical")
    assert row.actor_email == "staff@317atc.co.uk" and row.action == "read"
    assert row.detail == {"cadet_count": 1}


def test_a_refused_medical_read_is_not_recorded(api, db):
    # An NCO is turned away before the handler runs, so nothing was read.
    assert api.get("/cadets/audit/medical", headers=api.as_("nco")).status_code == 403
    assert _events(db) == []


def test_sending_a_parade_text_is_recorded(api, db, monkeypatch):
    msg = ParadeNightMessage(parade_date=datetime(2026, 9, 2), uniform="Blues", dnco="X", main_message="Hi",
                             c_flight_message="", status="ready", generated_at=datetime.now())
    db.add(msg)
    db.commit()
    monkeypatch.setattr(tx, "send_parade_message", lambda d, m: [{"status": "sent"}, {"status": "failed"}])
    assert api.post(f"/texts/messages/{msg.id}/send", headers=STAFF).status_code == 200
    (row,) = _events(db, resource="parade_message")
    assert row.action == "send" and row.resource_id == str(msg.id)
    assert row.detail == {"sent": 1, "failed": 1}


def test_a_failed_send_is_not_recorded_as_sent(api, db, monkeypatch):
    msg = ParadeNightMessage(parade_date=datetime(2026, 9, 2), uniform="Blues", dnco="X", main_message="Hi",
                             c_flight_message="", status="ready", generated_at=datetime.now())
    db.add(msg)
    db.commit()

    def refuse(d, m):
        raise RuntimeError("Notify not configured")

    monkeypatch.setattr(tx, "send_parade_message", refuse)
    assert api.post(f"/texts/messages/{msg.id}/send", headers=STAFF).status_code == 400
    assert _events(db) == []


def test_deleting_a_recipient_is_recorded(api, db):
    r = SmsRecipient(rank="Mrs", surname="Parent", phone_number="+447700900004")
    db.add(r)
    db.commit()
    key = f"extra:{r.id}"
    assert api.delete(f"/texts/recipients/{key}", headers=STAFF).status_code == 200
    (row,) = _events(db, resource="sms_recipient")
    assert row.action == "delete" and row.resource_id == key and row.detail == {"source": "extra"}


def test_deleting_a_missing_recipient_is_not_recorded(api, db):
    assert api.delete("/texts/recipients/extra:999", headers=STAFF).status_code == 404
    assert _events(db) == []


def test_pulling_a_committee_receipt_is_recorded(api, db):
    owner = User(google_id="g-other", email="other@317atc.co.uk")
    db.add(owner)
    db.flush()
    req = CommitteeRequest(year=2026, seq=1, reference="CR-2026-001", title="Kit", requester_id=owner.id,
                           created_at=datetime.now())
    db.add(req)
    db.flush()
    rec = CommitteeRequestReceipt(request_id=req.id, filename="r.pdf", mime_type="application/pdf",
                                  data=b"%PDF", uploaded_at=datetime.now())
    db.add(rec)
    db.commit()
    assert api.get(f"/committee-requests/{req.id}/receipts/{rec.id}", headers=STAFF).status_code == 200
    (row,) = _events(db, resource="committee_receipt")
    assert row.resource_id == str(rec.id) and row.detail == {"request_id": req.id}
    assert api.get(f"/committee-requests/{req.id}/receipts/9999", headers=STAFF).status_code == 404
    assert len(_events(db, resource="committee_receipt")) == 1


def test_backup_actions_are_recorded_and_failures_are_not(api, db, monkeypatch):
    monkeypatch.setattr(backups.db_backup, "run_db_backup", lambda: {"ok": True})
    monkeypatch.setattr(backups.db_backup, "preview_backup", lambda f: {"file": f})
    monkeypatch.setattr(backups.db_backup, "restore_backup", lambda f: {"restored": f})
    api.post("/backups/run", headers=OWNER)
    api.get("/backups/f1/preview", headers=OWNER)
    api.post("/backups/f1/restore", headers=OWNER)
    assert sorted(e.action for e in _events(db, resource="db_backup")) == ["preview", "restore", "run"]

    def boom(*a):
        raise RuntimeError("Drive quota")

    monkeypatch.setattr(backups.db_backup, "run_db_backup", boom)
    api.post("/backups/run", headers=OWNER)
    assert len(_events(db, resource="db_backup", action="run")) == 1


def test_a_restore_is_recorded_even_when_it_fails(api, db, monkeypatch):
    # Recorded before the call: a restore that dies half way has still happened.
    def boom(f):
        raise RuntimeError("psql failed")

    monkeypatch.setattr(backups.db_backup, "restore_backup", boom)
    assert api.post("/backups/f1/restore", headers=OWNER).status_code == 500
    assert len(_events(db, action="restore")) == 1


def test_an_audit_write_failure_does_not_break_the_medical_page(api, db, monkeypatch):
    import core.audit as audit

    monkeypatch.setattr(audit, "get_user_role", lambda e: (_ for _ in ()).throw(RuntimeError("down")))
    assert api.get("/cadets/audit/medical", headers=STAFF).status_code == 200


# ── rate limits ───────────────────────────────────────────────────────────────

@pytest.fixture
def fake_send(monkeypatch):
    monkeypatch.setattr(tx, "send_parade_message", lambda d, m: [])
    monkeypatch.setattr(tx, "send_test_sms", lambda m, n: None)


def _message(db):
    msg = ParadeNightMessage(parade_date=datetime(2026, 9, 2), uniform="Blues", dnco="X", main_message="Hi",
                             c_flight_message="", status="ready", generated_at=datetime.now())
    db.add(msg)
    db.commit()
    return msg.id


def test_the_parade_send_is_limited_per_caller(api, db, fake_send):
    # Not-yet-sent each time would 400 after the first real send, so use a
    # message that stays "ready" (the fake doesn't mark it sent).
    mid = _message(db)
    codes = [api.post(f"/texts/messages/{mid}/send", headers=STAFF).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    # Another person has their own bucket: one busy user can't lock out the rest.
    assert api.post(f"/texts/messages/{mid}/send", headers=api.as_("staff2")).status_code == 200


def test_test_send_is_limited(api, db, fake_send):
    mid = _message(db)
    codes = [api.post(f"/texts/messages/{mid}/test-send", json={"phone_number": "07700900123"},
                      headers=STAFF).status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]


def test_cadet_search_is_limited(api):
    codes = [api.get("/cadets/search?q=ab", headers=api.as_("nco")).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


# ── newsletter size cap ───────────────────────────────────────────────────────

def _upload(api, path, content, **data):
    return api.post(path, headers=STAFF, files={"file": ("n.pdf", content, "application/pdf")},
                    data={"title": "Summer", "date": "2026-07", "issue": "9", "description": "x", **data})


def test_an_oversized_newsletter_upload_is_refused_before_anything_is_committed(api, github):
    from routers.newsletters import MAX_PDF_BYTES

    res = _upload(api, "/upload-newsletter", b"%PDF" + b"0" * MAX_PDF_BYTES)
    assert res.status_code == 400 and "25 MB" in res.json()["detail"]
    assert github.commits == []


def test_a_newsletter_at_the_cap_is_accepted(api, github):
    import json

    from core.config import NEWSLETTER_JSON_PATH, NEWSLETTER_REPO
    from routers.newsletters import MAX_PDF_BYTES

    github.files[NEWSLETTER_REPO] = {NEWSLETTER_JSON_PATH: json.dumps([]).encode()}
    assert _upload(api, "/upload-newsletter", b"x" * MAX_PDF_BYTES).status_code == 200
