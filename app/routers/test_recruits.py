"""Recruits' joining forms over HTTP: staff adding a recruit and following
their progress on SMS, and the parent's public link (open, save, submit).
Completeness rules are in core/test_recruit_form.py, the PDFs in
form_generators/test_recruit_pdf.py."""

import base64
import io
from datetime import datetime, timedelta

import pytest
from PIL import Image

import routers.recruits as recruits
from core.recruit_form import london_now
from core.test_recruit_form import COMPLETE, complete_form
from database.models import RecruitApplication

STAFF = {"Authorization": "Bearer staff"}
NEW = {"cadetFirstName": "Alex", "cadetLastName": "Smith", "parentName": "Jane Smith",
       "parentEmail": "jane@example.com"}


def _sig() -> str:
    buf = io.BytesIO()
    Image.new("RGBA", (300, 80), (0, 0, 80, 255)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def recruit(api):
    """A recruit just added by staff: (summary, token)."""
    res = api.post("/recruits", json=NEW, headers=STAFF)
    assert res.status_code == 201
    body = res.json()
    return body, body["link"].rsplit("/", 1)[1]


def _submit(api, token, data=None, **sigs):
    payload = {"data": data or COMPLETE, "parentSignature": _sig(), **sigs}
    return api.post(f"/recruit-form/{token}/submit", json=payload)


# ── who can reach the staff side ─────────────────────────────────────────────

STAFF_ROUTES = [
    ("get", "/recruits"),
    ("post", "/recruits"),
    ("get", "/recruits/1"),
    ("post", "/recruits/1/extend"),
    ("post", "/recruits/1/reopen"),
    ("delete", "/recruits/1"),
    ("get", "/recruits/1/pdf"),
]


@pytest.mark.parametrize("method, path", STAFF_ROUTES)
def test_staff_routes_need_a_token(api, method, path):
    assert getattr(api, method)(path).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method, path", STAFF_ROUTES)
def test_staff_routes_refuse_everyone_but_staff(api, method, path, persona):
    # Recruits' medical and family details are for staff alone.
    kwargs = {"json": NEW} if method == "post" else {}
    assert getattr(api, method)(path, headers=api.as_(persona), **kwargs).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_member_can_list_recruits(api, persona):
    assert api.get("/recruits", headers=api.as_(persona)).status_code == 200


# ── staff: adding and managing ───────────────────────────────────────────────

def test_adding_a_recruit_makes_a_14_day_link_and_prefills_what_staff_know(api, db, recruit):
    body, token = recruit
    assert body["status"] == "not_started" and not body["expired"]
    assert body["link"] == f"{recruits.RECRUIT_FORM_URL}/{token}"
    assert len(token) >= 40
    expires = datetime.fromisoformat(body["expiresAt"])
    assert timedelta(days=13, hours=23) < expires - london_now() <= timedelta(days=14)
    assert body["progress"] == {"done": 0, "total": 8, "steps": body["progress"]["steps"]}

    data = api.get(f"/recruit-form/{token}").json()["data"]
    assert data["cadet"]["fullName"] == "Alex Smith"
    assert data["nok"]["fullName"] == "Jane Smith" and data["nok"]["email"] == "jane@example.com"
    assert data["signer"] == {"forename": "Jane", "surname": "Smith"}
    assert db.query(RecruitApplication).one().created_by == "staff@317atc.co.uk"


def test_each_recruit_gets_its_own_link(api):
    a = api.post("/recruits", json=NEW, headers=STAFF).json()["link"]
    b = api.post("/recruits", json=NEW, headers=STAFF).json()["link"]
    assert a != b


@pytest.mark.parametrize("change", [
    {"parentEmail": "not an email"},
    {"cadetFirstName": ""},
    {"cadetFirstName": "   "},
    {"parentName": "x" * 201},
    {"surprise": "field"},
])
def test_adding_a_recruit_refuses_bad_details(api, change):
    assert api.post("/recruits", json={**NEW, **change}, headers=STAFF).status_code == 422


def test_adding_a_recruit_needs_every_detail(api):
    assert api.post("/recruits", json={"cadetFirstName": "Alex"}, headers=STAFF).status_code == 422


def test_the_list_is_newest_first_with_progress(api, recruit):
    _, token = recruit
    api.put(f"/recruit-form/{token}", json={"data": complete_form(medical={}, about={})})
    api.post("/recruits", json={**NEW, "cadetFirstName": "Blake"}, headers=STAFF)
    rows = api.get("/recruits", headers=STAFF).json()
    assert [r["cadetFirstName"] for r in rows] == ["Blake", "Alex"]
    assert rows[1]["status"] == "in_progress"
    assert rows[1]["progress"]["done"] == 5
    assert {s["key"]: s["done"] for s in rows[1]["progress"]["steps"]}["medical"] is False


def test_the_list_is_empty_with_no_recruits(api):
    assert api.get("/recruits", headers=STAFF).json() == []


@pytest.mark.parametrize("method, path", [
    ("get", "/recruits/999"), ("post", "/recruits/999/extend"), ("post", "/recruits/999/reopen"),
    ("delete", "/recruits/999"), ("get", "/recruits/999/pdf"),
])
def test_a_missing_recruit_is_404(api, method, path):
    assert getattr(api, method)(path, headers=STAFF).status_code == 404


def test_the_detail_shows_answers_gaps_and_options(api, recruit):
    body, _ = recruit
    got = api.get(f"/recruits/{body['id']}", headers=STAFF).json()
    assert got["data"]["cadet"]["fullName"] == "Alex Smith"
    assert "Date of birth" in got["missing"]["cadet"]
    assert got["options"]["steps"][0] == {"key": "cadet", "label": "Cadet details"}
    assert got["parentSignature"] is None and got["hasHealthDeclaration"] is False


def test_extending_gives_14_days_from_now_even_once_expired(api, db, recruit):
    body, token = recruit
    row = db.get(RecruitApplication, body["id"])
    row.expires_at = london_now() - timedelta(days=3)
    db.commit()
    assert api.get(f"/recruit-form/{token}").status_code == 404
    assert api.get("/recruits", headers=STAFF).json()[0]["expired"] is True

    res = api.post(f"/recruits/{body['id']}/extend", headers=STAFF)
    assert res.status_code == 200 and res.json()["expired"] is False
    assert datetime.fromisoformat(res.json()["expiresAt"]) - london_now() > timedelta(days=13)
    assert api.get(f"/recruit-form/{token}").status_code == 200   # same link works again


def test_a_submitted_form_cant_be_extended(api, recruit):
    body, token = recruit
    assert _submit(api, token).status_code == 200
    assert api.post(f"/recruits/{body['id']}/extend", headers=STAFF).status_code == 409


def test_deleting_a_recruit_kills_the_link(api, db, recruit):
    body, token = recruit
    assert api.delete(f"/recruits/{body['id']}", headers=STAFF).status_code == 204
    assert db.query(RecruitApplication).count() == 0
    assert api.get(f"/recruit-form/{token}").status_code == 404


# ── the parent's link ────────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["get", "put", "post"])
def test_an_unknown_link_says_invalid_the_same_way_every_time(api, method):
    path = "/recruit-form/not-a-real-token" + ("/submit" if method == "post" else "")
    kwargs = {} if method == "get" else {"json": {"data": {}}}
    res = getattr(api, method)(path, **kwargs)
    assert res.status_code == 404 and res.json()["detail"] == recruits.BAD_LINK


def test_an_expired_link_looks_exactly_like_an_unknown_one(api, db, recruit):
    _, token = recruit
    db.query(RecruitApplication).one().expires_at = london_now() - timedelta(seconds=1)
    db.commit()
    for res in (api.get(f"/recruit-form/{token}"),
                api.put(f"/recruit-form/{token}", json={"data": {}}),
                _submit(api, token)):
        assert res.status_code == 404 and res.json()["detail"] == recruits.BAD_LINK


def test_opening_the_link_is_recorded_once(api, db, recruit):
    body, token = recruit
    first = api.get(f"/recruit-form/{token}").json()
    assert first["status"] == "open" and first["cadetFirstName"] == "Alex"
    assert "options" in first and "missing" in first
    opened = db.get(RecruitApplication, body["id"]).opened_at
    api.get(f"/recruit-form/{token}")
    db.expire_all()
    assert db.get(RecruitApplication, body["id"]).opened_at == opened
    assert api.get("/recruits", headers=STAFF).json()[0]["status"] == "opened"


def test_the_parent_view_leaves_out_staff_only_details(api, recruit):
    _, token = recruit
    view = api.get(f"/recruit-form/{token}").json()
    for key in ("parentEmail", "createdBy", "id", "link", "parentSignature"):
        assert key not in view


def test_saving_progress_stores_the_answers_and_reports_whats_left(api, recruit):
    body, token = recruit
    partial = complete_form(medical={}, about={})
    res = api.put(f"/recruit-form/{token}", json={"data": partial})
    assert res.status_code == 200
    assert res.json()["missing"]["medical"] and res.json()["missing"]["cadet"] == []
    assert res.json()["lastSavedAt"]
    got = api.get(f"/recruits/{body['id']}", headers=STAFF).json()
    assert got["data"]["about"]["school"] == "" and got["data"]["contact"]["postcode"] == "LE1 2AB"


def test_saving_an_empty_form_is_allowed(api, recruit):
    # Progress is saved as the parent goes, so half an answer is fine until submit.
    _, token = recruit
    assert api.put(f"/recruit-form/{token}", json={"data": {}}).status_code == 200


@pytest.mark.parametrize("data", [
    {"cadet": {"fullName": "x" * 5000}},
    {"cadet": {"isAdmin": True}},
    {"medical": {"allergies": {"unicorns": {}}}},
])
def test_saving_refuses_answers_outside_the_schema(api, recruit, data):
    _, token = recruit
    assert api.put(f"/recruit-form/{token}", json={"data": data}).status_code == 422


def test_submitting_an_incomplete_form_lists_the_gaps_and_keeps_the_answers(api, db, recruit):
    _, token = recruit
    partial = complete_form(about={})
    res = _submit(api, token, partial)
    assert res.status_code == 422
    assert res.json()["detail"]["missing"] == {"about": ["School"]}
    row = db.query(RecruitApplication).one()
    assert row.submitted_at is None and row.last_saved_at is not None
    assert row.data["contact"]["postcode"] == "LE1 2AB"


def test_submitting_needs_the_signers_name(api, recruit):
    _, token = recruit
    res = _submit(api, token, complete_form(signer={"forename": "Jane", "surname": ""}))
    assert res.status_code == 422 and res.json()["detail"]["missing"] == {"sign": ["Your forename and surname"]}


@pytest.mark.parametrize("signature", [
    None,
    "",
    "data:image/jpeg;base64,/9j/4AAQ",                                     # not a PNG data URL
    "data:image/png;base64,not base64!",
    "data:image/png;base64," + base64.b64encode(b"GIF89a....").decode(),   # PNG label, not PNG
    "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 300_001).decode(),
])
def test_submitting_needs_a_real_png_signature(api, db, recruit, signature):
    _, token = recruit
    res = api.post(f"/recruit-form/{token}/submit", json={"data": COMPLETE, "parentSignature": signature})
    assert res.status_code == 422
    assert db.query(RecruitApplication).one().submitted_at is None


def test_a_complete_submission_is_locked_and_no_longer_shows_the_answers(api, db, recruit):
    body, token = recruit
    res = _submit(api, token)
    assert res.status_code == 200 and res.json()["status"] == "submitted"

    # The link now only says it's done; the answers stay on SMS.
    view = api.get(f"/recruit-form/{token}").json()
    assert view["status"] == "submitted" and "data" not in view
    assert api.put(f"/recruit-form/{token}", json={"data": {}}).status_code == 409
    assert _submit(api, token).status_code == 409

    row = db.query(RecruitApplication).one()
    assert row.parent_signature.startswith(b"\x89PNG") and row.cadet_signature is None
    staff_view = api.get(f"/recruits/{body['id']}", headers=STAFF).json()
    assert staff_view["status"] == "submitted"
    assert staff_view["progress"]["done"] == staff_view["progress"]["total"]
    assert staff_view["parentSignature"].startswith("data:image/png;base64,")


def test_a_submitted_link_still_answers_after_it_would_have_expired(api, db, recruit):
    _, token = recruit
    _submit(api, token)
    db.query(RecruitApplication).one().expires_at = london_now() - timedelta(days=30)
    db.commit()
    assert api.get(f"/recruit-form/{token}").json()["status"] == "submitted"
    assert api.get("/recruits", headers=STAFF).json()[0]["expired"] is False


def test_a_cadet_of_sixteen_with_a_health_declaration_must_sign_it_too(api, db, recruit):
    _, token = recruit
    sixteen = f"{london_now().year - 17}-01-01"   # 16 or 17 today, never younger
    data = complete_form(
        cadet={**COMPLETE["cadet"], "dob": sixteen},
        medical={"has": True, "diet": ["vegan"], "ehcPlan": False},
    )
    res = _submit(api, token, data)
    assert res.status_code == 422 and "cadet" in res.json()["detail"]
    assert _submit(api, token, data, cadetSignature=_sig()).status_code == 200
    assert db.query(RecruitApplication).one().cadet_signature.startswith(b"\x89PNG")


# ── PDFs ─────────────────────────────────────────────────────────────────────

def test_the_pdf_waits_for_submission(api, recruit):
    body, _ = recruit
    assert api.get(f"/recruits/{body['id']}/pdf", headers=STAFF).status_code == 409


def test_the_pdf_is_the_filled_form_named_after_the_cadet(api, recruit):
    body, token = recruit
    _submit(api, token)
    res = api.get(f"/recruits/{body['id']}/pdf", headers=STAFF)
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert res.headers["content-disposition"] == 'inline; filename="3822A-H Alex Smith.pdf"'
    assert api.get(f"/recruits/{body['id']}/pdf?form=3822A", headers=STAFF).status_code == 200


def test_there_is_no_3822h_when_the_parent_answered_no_to_medical(api, recruit):
    body, token = recruit
    _submit(api, token)
    assert api.get(f"/recruits/{body['id']}/pdf?form=3822H", headers=STAFF).status_code == 404


def test_the_3822h_is_there_when_there_is_a_medical_declaration(api, recruit):
    body, token = recruit
    _submit(api, token, complete_form(medical={"has": True, "diet": ["halal"], "ehcPlan": False}))
    assert api.get(f"/recruits/{body['id']}/pdf?form=3822H", headers=STAFF).status_code == 200


def test_an_unknown_pdf_form_is_refused(api, recruit):
    body, token = recruit
    _submit(api, token)
    assert api.get(f"/recruits/{body['id']}/pdf?form=9999", headers=STAFF).status_code == 422


def test_a_non_ascii_name_still_downloads(api):
    body = api.post("/recruits", json={**NEW, "cadetFirstName": "Zoë", "cadetLastName": "Łukasz"},
                    headers=STAFF).json()
    _submit(api, body["link"].rsplit("/", 1)[1])
    res = api.get(f"/recruits/{body['id']}/pdf", headers=STAFF)
    assert res.status_code == 200 and "filename*=UTF-8''" in res.headers["content-disposition"]


# ── reopening, tidying, and what must be true to sign ────────────────────────

def test_reopening_sends_a_submitted_form_back_with_its_answers_but_not_its_signatures(api, db, recruit):
    body, token = recruit
    _submit(api, token)
    row = db.query(RecruitApplication).one()
    row.expires_at = london_now() - timedelta(days=1)   # long past the original link's life
    db.commit()

    res = api.post(f"/recruits/{body['id']}/reopen", headers=STAFF)
    assert res.status_code == 200 and res.json()["status"] == "in_progress"
    db.expire_all()
    row = db.query(RecruitApplication).one()
    assert row.submitted_at is None and row.parent_signature is None
    assert row.expires_at - london_now() > timedelta(days=13)
    # The parent's link works again, with their answers in it, and they re-sign.
    view = api.get(f"/recruit-form/{token}").json()
    assert view["status"] == "open" and view["data"]["about"]["school"] == "City Academy"
    assert _submit(api, token).status_code == 200


def test_only_a_submitted_form_can_be_reopened(api, recruit):
    body, _ = recruit
    assert api.post(f"/recruits/{body['id']}/reopen", headers=STAFF).status_code == 409


def test_submitting_keeps_only_the_answers_the_parent_finally_gave(api, db, recruit):
    # They ticked asthma, then answered "no" to medical: the asthma answers
    # mustn't be stored against the child.
    _, token = recruit
    data = complete_form(medical={"has": False, "conditions": ["asthma"], "allergies": {"nut": {"severity": "mild"}}},
                         cadet={**COMPLETE["cadet"], "genderOther": "left over"})
    assert _submit(api, token, data).status_code == 200
    stored = db.query(RecruitApplication).one().data
    assert stored["medical"]["conditions"] == [] and stored["medical"]["allergies"] == {}
    assert stored["medical"]["has"] is False
    assert stored["cadet"]["genderOther"] == ""


def test_a_child_under_twelve_cant_be_signed_up(api, recruit):
    _, token = recruit
    eleven = f"{london_now().year - 11}-01-01"   # 10 or 11 today, never 12
    res = _submit(api, token, complete_form(cadet={**COMPLETE["cadet"], "dob": eleven}))
    assert res.status_code == 422
    assert res.json()["detail"]["missing"]["cadet"] == ["Date of birth: cadets must be at least 12 to join"]


def test_someone_without_parental_responsibility_cant_sign(api, recruit):
    _, token = recruit
    nok = {**COMPLETE["nok"], "hasParentalResponsibility": False}
    res = _submit(api, token, complete_form(nok=nok))
    assert res.status_code == 422 and "parental responsibility" in res.json()["detail"]["missing"]["parent"][0]


def test_the_cadets_own_signature_block_uses_the_names_staff_entered(api, recruit, monkeypatch):
    # "Alex Jordan Smith" split on a space would make "Jordan Smith" the surname.
    body, token = recruit
    captured = {}
    real = recruits.build_pdf
    monkeypatch.setattr(recruits, "build_pdf", lambda *a, **k: captured.update(k) or real(*a, **k))
    sixteen = f"{london_now().year - 17}-01-01"
    data = complete_form(cadet={**COMPLETE["cadet"], "fullName": "Alex Jordan Smith", "dob": sixteen},
                         medical={"has": True, "diet": ["vegan"], "ehcPlan": False})
    _submit(api, token, data, cadetSignature=_sig())
    api.get(f"/recruits/{body['id']}/pdf?form=3822H", headers=STAFF)
    assert captured["cadet_name"] == ("Alex", "Smith")


def test_link_tokens_are_kept_out_of_the_request_log():
    from api import _loggable

    assert _loggable("/recruit-form/s3cr3t-token/submit") == "/recruit-form/…/submit"
    assert _loggable("/recruit-form/s3cr3t-token") == "/recruit-form/…"
    assert _loggable("/recruits/4/pdf") == "/recruits/4/pdf"
