"""NCO appraisals over HTTP — the gaps the in-process test in
test_nco_appraisals.py doesn't reach: access, reminders, AI drafting, editing
guards and the email/document endpoints' error paths."""

import pytest

import scripts.nco_appraisal_ai as ai
from database.models import Cadet

STAFF = {"Authorization": "Bearer staff"}


@pytest.fixture
def ncos(db):
    db.add_all([
        Cadet(cin=1, first_name="Nia", last_name="Cole", rank="Corporal", email="nia@x"),
        Cadet(cin=2, first_name="Joe", last_name="Bloggs", rank="Cadet"),
        Cadet(cin=3, first_name="Sam", last_name="Sarge", rank=" SERGEANT "),
    ])
    db.commit()


def _create(api, cadet_id=1, **body):
    res = api.post("/nco-appraisals", json={"cadet_id": cadet_id, **body}, headers=STAFF)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.parametrize("method,path", [
    ("get", "/nco-appraisals"), ("post", "/nco-appraisals"), ("get", "/nco-appraisals/1"),
    ("put", "/nco-appraisals/1"), ("delete", "/nco-appraisals/1"), ("get", "/nco-appraisals/1/document"),
    ("post", "/nco-appraisals/1/email"), ("post", "/nco-appraisals/reminders"),
    ("delete", "/nco-appraisals/reminders/1"), ("post", "/nco-appraisals/ai"),
])
@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_staff_only(api, method, path, persona):
    call = getattr(api, method)
    h = api.as_(persona)
    res = call(path, headers=h) if method in ("get", "delete") else call(path, json={}, headers=h)
    assert res.status_code == 403


# ── reminders ─────────────────────────────────────────────────────────────────

def test_reminder_upserts_one_per_nco(api, ncos):
    first = api.post("/nco-appraisals/reminders", headers=STAFF,
                     json={"cadet_id": 1, "due_date": "2026-11-01", "note": "  first  "})
    assert first.status_code == 200
    moved = api.post("/nco-appraisals/reminders", headers=STAFF,
                     json={"cadet_id": 1, "due_date": "2026-12-01", "note": "x" * 3000}).json()
    assert moved["id"] == first.json()["id"]
    assert moved["due_date"].startswith("2026-12-01") and len(moved["note"]) == 2000
    assert api.delete(f"/nco-appraisals/reminders/{moved['id']}", headers=STAFF).json() == {"ok": True}
    assert api.delete(f"/nco-appraisals/reminders/{moved['id']}", headers=STAFF).status_code == 404


@pytest.mark.parametrize("body,code", [
    ({"cadet_id": 1, "due_date": ""}, 400),
    ({"cadet_id": 1, "due_date": "soon"}, 400),
    ({"cadet_id": 2, "due_date": "2026-11-01"}, 400),   # not an NCO
    ({"cadet_id": 99, "due_date": "2026-11-01"}, 404),
    ({"cadet_id": 1}, 422),
])
def test_reminder_validation(api, ncos, body, code):
    assert api.post("/nco-appraisals/reminders", json=body, headers=STAFF).status_code == code


def test_rank_match_is_case_and_space_insensitive(api, ncos):
    assert api.post("/nco-appraisals/reminders", headers=STAFF,
                    json={"cadet_id": 3, "due_date": "2026-11-01"}).status_code == 200


# ── AI drafting ───────────────────────────────────────────────────────────────

POINTS = "Leads drill well, needs to delegate more, very reliable"


def test_ai_draft_returns_sections_without_saving(api, db, ncos, monkeypatch):
    seen = {}

    def fake(name, age, attendance, points):
        seen.update(name=name, points=points)
        return {"general_observations": "Good", "strengths": "Drill"}, "gemini-x"

    monkeypatch.setattr(ai, "generate_appraisal", fake)
    res = api.post("/nco-appraisals/ai", json={"cadet_id": 1, "points": POINTS}, headers=STAFF).json()
    assert res["sections"]["strengths"] == "Drill" and res["model"] == "gemini-x"
    assert res["used_fallback"] is True
    assert seen["name"] == "Corporal Nia Cole"
    assert api.get("/nco-appraisals", headers=STAFF).json()["appraisals"] == []


def test_ai_draft_uses_overrides(api, ncos, monkeypatch):
    seen = {}
    monkeypatch.setattr(ai, "generate_appraisal",
                        lambda name, age, attendance, points: (seen.update(name=name) or ({"targets": "x"}, "m")))
    api.post("/nco-appraisals/ai", json={"cadet_id": 1, "points": POINTS, "nco_name": "Cpl N Cole"}, headers=STAFF)
    assert seen["name"] == "Cpl N Cole"


@pytest.mark.parametrize("points", ["", "too short", " " * 50])
def test_ai_needs_enough_notes(api, ncos, points):
    res = api.post("/nco-appraisals/ai", json={"cadet_id": 1, "points": points}, headers=STAFF)
    assert res.status_code == 400 and "a few more points" in res.json()["detail"]


def test_ai_errors(api, ncos, monkeypatch):
    assert api.post("/nco-appraisals/ai", json={"cadet_id": 2, "points": POINTS}, headers=STAFF).status_code == 400

    monkeypatch.setattr(ai, "generate_appraisal", lambda **k: ({"targets": ""}, "m"))
    res = api.post("/nco-appraisals/ai", json={"cadet_id": 1, "points": POINTS}, headers=STAFF)
    assert res.status_code == 502 and "nothing usable" in res.json()["detail"]

    def down(**k):
        raise RuntimeError("all providers failed")

    monkeypatch.setattr(ai, "generate_appraisal", down)
    res = api.post("/nco-appraisals/ai", json={"cadet_id": 1, "points": POINTS}, headers=STAFF)
    assert res.status_code == 502 and "by hand" in res.json()["detail"]


# ── create / edit guards ──────────────────────────────────────────────────────

def test_create_validation(api, ncos):
    assert api.post("/nco-appraisals", json={"cadet_id": 2}, headers=STAFF).status_code == 400
    assert api.post("/nco-appraisals", json={"cadet_id": 99}, headers=STAFF).status_code == 404
    bad_date = {"cadet_id": 1, "appraisal_date": "31/12/2026"}
    assert api.post("/nco-appraisals", json=bad_date, headers=STAFF).status_code == 400


def test_an_appraisal_cannot_move_to_another_nco(api, ncos):
    a = _create(api)
    res = api.put(f"/nco-appraisals/{a['id']}", json={"cadet_id": 3}, headers=STAFF)
    assert res.status_code == 400 and "write a new one" in res.json()["detail"]


def test_a_demoted_nco_can_still_have_their_appraisal_corrected(api, db, ncos):
    a = _create(api, strengths="Typo")
    db.query(Cadet).filter(Cadet.cin == 1).update({"rank": "Cadet"})
    db.commit()
    res = api.put(f"/nco-appraisals/{a['id']}", json={"cadet_id": 1, "strengths": "Fixed"}, headers=STAFF)
    assert res.status_code == 200 and res.json()["strengths"] == "Fixed"


def test_missing_appraisal_404s(api):
    assert api.get("/nco-appraisals/9", headers=STAFF).status_code == 404
    assert api.put("/nco-appraisals/9", json={"cadet_id": 1}, headers=STAFF).status_code == 404
    assert api.delete("/nco-appraisals/9", headers=STAFF).status_code == 404
    assert api.get("/nco-appraisals/9/document", headers=STAFF).status_code == 404
    assert api.post("/nco-appraisals/9/email", json={}, headers=STAFF).status_code == 404


def test_document_formats(api, ncos):
    a = _create(api, strengths="Calm")
    pdf = api.get(f"/nco-appraisals/{a['id']}/document", headers=STAFF)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    docx = api.get(f"/nco-appraisals/{a['id']}/document?fmt=docx", headers=STAFF)
    assert docx.status_code == 200 and docx.content.startswith(b"PK")
    assert api.get(f"/nco-appraisals/{a['id']}/document?fmt=rtf", headers=STAFF).status_code == 422


@pytest.mark.parametrize("body", [{"to": "nope"}, {"reply_to": "also nope"}])
def test_email_validates_addresses(api, ncos, body, outbox):
    a = _create(api)
    assert api.post(f"/nco-appraisals/{a['id']}/email", json=body, headers=STAFF).status_code == 422
    assert outbox == []


def test_email_defaults_to_the_ncos_address(api, ncos, outbox):
    a = _create(api)
    res = api.post(f"/nco-appraisals/{a['id']}/email", json={"to": "  ", "reply_to": ""}, headers=STAFF)
    assert res.status_code == 200
    assert outbox[0]["to"] == "nia@x" and outbox[0]["reply_to"] == "staff@317atc.co.uk"
