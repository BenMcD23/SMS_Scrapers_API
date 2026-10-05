"""Assessment sheets end to end: create each type (real PDFs), the pass rules,
validation, overview/upload thresholds, edit permissions, PDFs, completion and
the Bader upload trigger."""

import base64
import io

import pytest
from PIL import Image
from pypdf import PdfReader

import routers.assessments as a
from assessment_builders.leadership import process_assessment_data as leadership_rules
from assessment_builders.moi import process_assessment_data as moi_rules
from assessment_builders.pdf_utils import decode_pdf_data_url, merge_pdfs
from core.crypto import encrypt_password
from database.models import AssessmentSheet, BaderCredentials, Cadet, User

STAFF = {"Authorization": "Bearer staff"}
NCO = {"Authorization": "Bearer nco"}
NCO2 = {"Authorization": "Bearer nco2"}


def _png_data_url() -> str:
    buf = io.BytesIO()
    Image.new("RGBA", (40, 10), (0, 0, 0, 255)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


SIG = _png_data_url()
PDF_URL = "data:application/pdf;base64," + base64.b64encode(merge_pdfs([])).decode()


@pytest.fixture
def cadet(db):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able", email="amy@x", rank="Cdt", flight="A"))
    db.commit()


def leadership(**over):
    body = {"cadet_cin": 1, "scores": {str(i): 3 for i in range(1, 11)}, "exercise_no": "1",
            "exercise_name": "Bridge", "date": "2026-03-04", "debriefing_notes": "Good",
            "assessor_signature": SIG}
    return {**body, **over}


def radio(**over):
    body = {"cadet_cin": 1, "criteria": {"callsigns": True, "auth_1a": True}, "cyber_sec_date": "2026-01-01",
            "comments": "ok", "date": "2026-03-04", "assessor_signature": SIG}
    return {**body, **over}


def space(**over):
    checklist = {k: True for k in ("pts", "section_1a", "section_1b", "section_2", "section_3", "section_4",
                                   "section_5", "practical")}
    body = {"cadet_cin": 1, "checklist": checklist, "pts_date": "2026-02-02", "experiments": "Rockets",
            "date": "2026-03-04", "assessor_signature": SIG, "cadet_signature": SIG}
    return {**body, **over}


def moi(**over):
    body = {"cadet_cin": 1, "scores": {str(i): 3 for i in range(1, 14)}, "date": "2026-03-04",
            "section_comments": {"identifying": "x"}, "strengths_summary": "s", "improvements_summary": "i",
            "general_comments": "g", "assessor_signature": SIG, "cadet_signature": SIG}
    return {**body, **over}


def _add(api, kind, body, headers=NCO):
    return api.post(f"/assessments/{kind}/add-assessment", json=body, headers=headers)


# ── pass rules ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scores,passed", [
    ({str(i): 3 for i in range(1, 11)}, True),            # exactly 30
    ({**{str(i): 3 for i in range(1, 10)}, "10": 2}, False),  # 29
    ({str(i): 3 for i in range(1, 10)}, False),            # only 9 answered
    ({**{str(i): 4 for i in range(1, 10)}, "10": 1}, False),  # a 1 fails outright
    ({**{str(i): 3 for i in range(1, 11)}, "11": None}, True),  # None is unanswered, ignored
    ({}, False),
])
def test_leadership_pass_rule(scores, passed):
    assert leadership_rules({"scores": scores})["passed"] is passed


@pytest.mark.parametrize("scores,passed", [
    ({str(i): 3 for i in range(1, 14)}, True),             # 39
    ({**{str(i): 3 for i in range(1, 13)}, "13": 1}, False),
    ({str(i): 2 for i in range(1, 14)}, False),            # 26
    ({**{str(i): 3 for i in range(1, 12)}, "12": 1, "13": 1}, False),
    ({str(i): 3 for i in range(1, 13)}, False),            # 12 answered
])
def test_moi_pass_rule(scores, passed):
    assert moi_rules({"scores": scores})["passed"] is passed


def test_dates_are_reformatted_and_bad_dates_pass_through():
    assert leadership_rules({"date": "2026-03-04"})["date"] == "04/03/26"
    assert leadership_rules({"date": "4th March"})["date"] == "4th March"
    assert leadership_rules({})["date"] == ""


# ── pdf utils ─────────────────────────────────────────────────────────────────

def test_decode_pdf_data_url_variants():
    assert decode_pdf_data_url(None) is None
    assert decode_pdf_data_url("") is None
    raw = base64.b64encode(b"%PDF-1.4").decode()
    assert decode_pdf_data_url(raw) == b"%PDF-1.4"
    assert decode_pdf_data_url("data:application/pdf;base64," + raw) == b"%PDF-1.4"
    assert decode_pdf_data_url("not base64!!") is None


def test_merge_pdfs_skips_empty_and_unreadable_blobs():
    one_page = _real_pdf()
    merged = merge_pdfs([one_page, None, b"", b"this is not a pdf", one_page])
    assert len(PdfReader(io.BytesIO(merged)).pages) == 2


def _real_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(10, 10, "page")
    c.showPage()
    c.save()
    return buf.getvalue()


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["leadership", "radio", "space", "moi"])
def test_create_needs_nco_or_above(api, cadet, kind):
    assert _add(api, kind, {}, headers={}).status_code == 401
    assert _add(api, kind, {}, headers=api.as_("cadet")).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("post", "/assessments/1/Blue Radio/mark-complete"),
    ("delete", "/assessments/1"),
    ("post", "/assessments/upload-to-bader"),
])
def test_staff_only_actions(api, method, path):
    res = api.delete(path, headers=NCO) if method == "delete" else api.post(path, json={}, headers=NCO)
    assert res.status_code == 403


# ── create ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,body,atype,passed", [
    ("leadership", leadership(), "Blue Leadership", True),
    ("radio", radio(), "Blue Radio", True),
    ("radio", radio(criteria={"callsigns": True, "auth_1a": False}), "Blue Radio", False),
    ("space", space(), "Blue Space", True),
    ("space", space(checklist={"pts": False}), "Blue Space", False),
    ("moi", moi(), "MOI", True),
])
def test_create_each_type_renders_a_pdf_and_emails_the_cadet(api, db, cadet, outbox, kind, body, atype, passed):
    res = _add(api, kind, body)
    assert res.status_code == 200, res.text
    sheet = db.get(AssessmentSheet, res.json()["assessment_id"])
    assert sheet.assessment_type == atype
    assert sheet.fields["passed"] is passed
    assert sheet.pdf_data.startswith(b"%PDF")
    # The assessor is the signed-in NCO, by their Google name.
    assert sheet.fields["assessor_name"] == "Nina Corporal"
    assert len(outbox) == 1 and outbox[0]["to"] == "amy@x"
    assert outbox[0]["attachment_filename"] == f"{atype.replace(' ', '_')}_Able_Amy.pdf"


def test_assessor_name_prefers_the_profile_name(api, db, cadet):
    from database.models import UserProfile

    api.get("/assessments/overview", headers=NCO)  # creates the user
    user = db.query(User).filter(User.email == "nco@317atc.co.uk").one()
    db.add(UserProfile(user_id=user.id, assessor_name="Cpl N Corporal"))
    db.commit()
    sid = _add(api, "leadership", leadership()).json()["assessment_id"]
    assert db.get(AssessmentSheet, sid).fields["assessor_name"] == "Cpl N Corporal"


def test_cadet_without_email_still_saves(api, db, outbox):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.commit()
    assert _add(api, "leadership", leadership()).status_code == 200
    assert outbox == []


def test_radio_initials_come_from_the_assessor(api, db, cadet):
    sid = _add(api, "radio", radio()).json()["assessment_id"]
    assert db.get(AssessmentSheet, sid).fields["assessor_initials"] == "NC"


@pytest.mark.parametrize("kind,body,detail", [
    ("radio", radio(cyber_sec_date="  "), "Cyber Security"),
    ("radio", radio(assessor_signature=""), "signature"),
    ("radio", radio(comments="x" * 141), "140"),
    ("space", space(pts_date=""), "PTS date"),
    ("space", space(experiments="x" * 501), "500"),
    ("space", space(assessor_signature=""), "Instructor signature"),
    ("space", space(cadet_signature=None), "Cadet signature"),
    ("moi", moi(general_comments="x" * 1151), "general_comments"),
    ("moi", moi(section_comments={"identifying": "x" * 671}), "identifying"),
    ("moi", moi(section_comments={"delivery": "x" * 501}), "delivery"),
    ("moi", moi(section_comments={"planning": "x" * 901}), "planning"),
])
def test_create_validation(api, cadet, kind, body, detail):
    res = _add(api, kind, body)
    assert res.status_code == 400
    assert detail in res.json()["detail"]


def test_limits_are_inclusive(api, cadet):
    assert _add(api, "radio", radio(comments="x" * 140)).status_code == 200
    assert _add(api, "space", space(experiments="x" * 500)).status_code == 200
    assert _add(api, "moi", moi(section_comments={"identifying": "x" * 670})).status_code == 200


def test_space_without_pts_needs_no_pts_date(api, cadet):
    assert _add(api, "space", space(checklist={"pts": False}, pts_date="")).status_code == 200


@pytest.mark.parametrize("kind,body,code", [
    ("leadership", leadership(cadet_cin=99), 404),
    ("leadership", {**leadership(cadet_cin=None), "cadet_name": "Amy Able"}, 200),
    ("leadership", {**leadership(cadet_cin=None), "cadet_name": "amy able"}, 200),
    ("leadership", {**leadership(cadet_cin=None), "cadet_name": "Nobody"}, 404),
    ("leadership", {**leadership(cadet_cin=None), "cadet_name": "%"}, 404),
    ("leadership", {**leadership(cadet_cin=None), "cadet_name": "Amy_Able"}, 404),
    ("leadership", {**leadership(cadet_cin=None), "cadet_name": "  "}, 400),
    ("space", {**space(cadet_cin=None), "cadet_name": "Amy Able"}, 400),
    ("moi", {**moi(cadet_cin=None), "cadet_name": "Amy Able"}, 400),
])
def test_cadet_resolution(api, cadet, kind, body, code):
    # Name fallback is exact (case-insensitive) — wildcards never land the
    # sheet on the wrong cadet. Space and MOI insist on a CIN.
    assert _add(api, kind, body).status_code == code


def test_moi_lesson_plan_is_stored(api, db, cadet):
    sid = _add(api, "moi", moi(lesson_plan_pdf=PDF_URL, lesson_plan_filename="plan.pdf")).json()["assessment_id"]
    sheet = db.get(AssessmentSheet, sid)
    assert sheet.lesson_plan_pdf.startswith(b"%PDF") and sheet.lesson_plan_filename == "plan.pdf"
    detail = api.get(f"/assessments/{sid}/detail", headers=NCO).json()
    assert detail["has_lesson_plan"] is True and detail["lesson_plan_filename"] == "plan.pdf"


# ── overview ──────────────────────────────────────────────────────────────────

def test_overview_groups_and_upload_thresholds(api, cadet):
    _add(api, "leadership", leadership())
    assert api.get("/assessments/overview", headers=NCO).json()[0]["groups"][0]["can_upload"] is False
    _add(api, "leadership", leadership(scores={"1": 1}))  # a fail
    _add(api, "leadership", leadership(), headers=NCO2)
    _add(api, "moi", moi(scores={"1": 1}))  # MOI counts attempts, not passes
    _add(api, "moi", moi(scores={"1": 1}))
    _add(api, "radio", radio())

    [row] = api.get("/assessments/overview", headers=NCO).json()
    groups = {g["assessment_type"]: g for g in row["groups"]}
    lead = groups["Blue Leadership"]
    assert (lead["passed_count"], lead["required_to_upload"], lead["can_upload"]) == (2, 2, True)
    assert [x["is_mine"] for x in lead["assessments"]] == [True, True, False]
    assert groups["MOI"]["passed_count"] == 0 and groups["MOI"]["can_upload"] is True
    assert groups["Blue Radio"]["required_to_upload"] == 1 and groups["Blue Radio"]["can_upload"] is True
    assert lead["uploaded"] is False and lead["uploaded_at"] is None


def test_overview_empty(api):
    assert api.get("/assessments/overview", headers=NCO).json() == []


# ── detail / edit ─────────────────────────────────────────────────────────────

def test_detail_strips_signatures(api, cadet):
    sid = _add(api, "space", space()).json()["assessment_id"]
    detail = api.get(f"/assessments/{sid}/detail", headers=NCO).json()
    assert "assessor_signature" not in detail["fields"] and "cadet_signature" not in detail["fields"]
    assert detail["editable"] is True and detail["is_mine"] is True
    assert detail["cadet"] == {"cin": 1, "first_name": "Amy", "last_name": "Able", "rank": "Cdt"}
    assert api.get("/assessments/999/detail", headers=NCO).status_code == 404


@pytest.mark.parametrize("kind,body,edit", [
    ("leadership", leadership(), leadership(scores={"1": 1})),
    ("radio", radio(), radio(criteria={"callsigns": False}, assessor_signature="")),
    ("space", space(), space(checklist={"pts": False}, assessor_signature="", cadet_signature="")),
    ("moi", moi(), moi(scores={"1": 1}, assessor_signature="", cadet_signature="")),
])
def test_assessor_can_edit_and_signatures_are_kept(api, db, cadet, kind, body, edit):
    sid = _add(api, kind, body).json()["assessment_id"]
    before = dict(db.get(AssessmentSheet, sid).fields)
    res = api.put(f"/assessments/{sid}", json=edit, headers=NCO)
    assert res.status_code == 200, res.text
    assert res.json()["passed"] is False
    sheet = db.get(AssessmentSheet, sid)
    db.refresh(sheet)
    # An edit keeps the original assessor and their signature when none is sent.
    assert sheet.fields["assessor_name"] == before["assessor_name"]
    assert sheet.fields["assessor_signature"] == before["assessor_signature"]
    if "cadet_signature" in before:
        assert sheet.fields["cadet_signature"] == before["cadet_signature"]


def test_radio_edit_with_no_criteria_is_a_fail_not_a_pass(api, cadet):
    sid = _add(api, "radio", radio()).json()["assessment_id"]
    # all([]) is True — an empty criteria dict must not become a pass.
    assert api.put(f"/assessments/{sid}", json=radio(criteria={}), headers=NCO).json()["passed"] is False


def test_edit_permissions_and_states(api, db, cadet):
    sid = _add(api, "leadership", leadership()).json()["assessment_id"]
    assert api.put(f"/assessments/{sid}", json=leadership(), headers=NCO2).status_code == 403
    assert api.get(f"/assessments/{sid}/detail", headers=NCO2).json()["editable"] is False
    assert api.put(f"/assessments/{sid}", json=leadership(), headers=STAFF).status_code == 200

    api.post("/assessments/1/Blue Leadership/mark-complete", json={}, headers=STAFF)
    res = api.put(f"/assessments/{sid}", json=leadership(), headers=NCO)
    assert res.status_code == 409 and "Reopen" in res.json()["detail"]
    assert api.get(f"/assessments/{sid}/detail", headers=NCO).json()["editable"] is False
    assert api.put("/assessments/999", json={}, headers=NCO).status_code == 404


def test_legacy_types_cannot_be_edited(api, db, cadet):
    api.get("/assessments/overview", headers=NCO)
    user = db.query(User).filter(User.email == "nco@317atc.co.uk").one()
    db.add(AssessmentSheet(id=50, assessment_type="First Aid", fields={}, cadet_id=1, assessor_id=user.id,
                           created_at=a.datetime.utcnow()))
    db.commit()
    assert api.put("/assessments/50", json={}, headers=NCO).status_code == 400


def test_moi_edit_lesson_plan_replace_keep_remove(api, db, cadet):
    sid = _add(api, "moi", moi(lesson_plan_pdf=PDF_URL, lesson_plan_filename="v1.pdf")).json()["assessment_id"]
    api.put(f"/assessments/{sid}", json=moi(), headers=NCO)
    sheet = db.get(AssessmentSheet, sid)
    db.refresh(sheet)
    assert sheet.lesson_plan_filename == "v1.pdf"

    api.put(f"/assessments/{sid}", json=moi(lesson_plan_pdf=PDF_URL, lesson_plan_filename="v2.pdf"), headers=NCO)
    db.refresh(sheet)
    assert sheet.lesson_plan_filename == "v2.pdf"

    api.put(f"/assessments/{sid}", json=moi(remove_lesson_plan=True), headers=NCO)
    db.refresh(sheet)
    assert sheet.lesson_plan_pdf is None and sheet.lesson_plan_filename is None


# ── completion, pdfs, delete ──────────────────────────────────────────────────

def test_mark_complete_and_reopen(api, db, cadet):
    _add(api, "radio", radio())
    res = api.post("/assessments/1/Blue Radio/mark-complete", json={}, headers=STAFF)
    assert res.status_code == 200 and "marked complete" in res.json()["message"]
    group = api.get("/assessments/overview", headers=STAFF).json()[0]["groups"][0]
    assert group["uploaded"] is True and group["uploaded_at"]

    res = api.post("/assessments/1/Blue Radio/mark-complete", json={"completed": False}, headers=STAFF)
    assert "reopened" in res.json()["message"]
    group = api.get("/assessments/overview", headers=STAFF).json()[0]["groups"][0]
    assert group["uploaded"] is False and group["uploaded_at"] is None

    assert api.post("/assessments/99/Blue Radio/mark-complete", json={}, headers=STAFF).status_code == 404
    assert api.post("/assessments/1/MOI/mark-complete", json={}, headers=STAFF).status_code == 404


def test_single_and_combined_pdfs(api, db, cadet):
    first = _add(api, "moi", moi(lesson_plan_pdf="data:application/pdf;base64,"
                                 + base64.b64encode(_real_pdf()).decode())).json()["assessment_id"]
    _add(api, "moi", moi())

    single = api.get(f"/assessments/{first}/pdf", headers=NCO)
    assert single.status_code == 200 and single.content.startswith(b"%PDF")

    combined = api.get("/assessments/group/1/MOI/combined-pdf", headers=NCO)
    assert combined.status_code == 200
    # Two 2-page MOI sheets plus a 1-page lesson plan.
    assert len(PdfReader(io.BytesIO(combined.content)).pages) == 5
    assert "MOI_1_combined.pdf" in combined.headers["content-disposition"]

    assert api.get("/assessments/group/1/Blue Radio/combined-pdf", headers=NCO).status_code == 404
    assert api.get("/assessments/999/pdf", headers=NCO).status_code == 404


def test_combined_pdf_survives_a_corrupt_lesson_plan(api, db, cadet):
    sid = _add(api, "moi", moi()).json()["assessment_id"]
    sheet = db.get(AssessmentSheet, sid)
    sheet.lesson_plan_pdf = b"PNG pretending to be a PDF"
    db.commit()
    res = api.get("/assessments/group/1/MOI/combined-pdf", headers=NCO)
    assert res.status_code == 200
    assert len(PdfReader(io.BytesIO(res.content)).pages) == 2


def test_pdf_missing_blob_is_404(api, db, cadet):
    sid = _add(api, "leadership", leadership()).json()["assessment_id"]
    db.get(AssessmentSheet, sid).pdf_data = None
    db.commit()
    assert api.get(f"/assessments/{sid}/pdf", headers=NCO).status_code == 404


def test_delete(api, cadet):
    sid = _add(api, "leadership", leadership()).json()["assessment_id"]
    assert api.delete(f"/assessments/{sid}", headers=STAFF).json()["status"] == "success"
    assert api.delete(f"/assessments/{sid}", headers=STAFF).status_code == 404


# ── upload to Bader ───────────────────────────────────────────────────────────

def test_upload_needs_credentials_ids_and_existing_sheets(api, db, cadet, monkeypatch):
    res = api.post("/assessments/upload-to-bader", json={"assessment_ids": [1]}, headers=STAFF)
    assert res.status_code == 400 and "Settings" in res.json()["detail"]

    user = db.query(User).filter(User.email == "staff@317atc.co.uk").one()
    db.add(BaderCredentials(user_id=user.id, role_username="u", role_password=encrypt_password("p")))
    db.commit()
    assert api.post("/assessments/upload-to-bader", json={"assessment_ids": []}, headers=STAFF).status_code == 400
    res = api.post("/assessments/upload-to-bader", json={"assessment_ids": [1, 2]}, headers=STAFF)
    assert res.status_code == 404 and "[1, 2]" in res.json()["detail"]
    assert api.post("/assessments/upload-to-bader", json={}, headers=STAFF).status_code == 422


def test_upload_starts_a_background_job(api, db, cadet, monkeypatch):
    started = {}
    monkeypatch.setattr(a.scrapers, "create_upload_job", lambda email: ("job123", {}))
    monkeypatch.setattr(a.scrapers, "run_upload_job",
                        lambda job_id, uid, email, ids: started.update(job=job_id, ids=ids))
    sid = _add(api, "radio", radio()).json()["assessment_id"]
    user = db.query(User).filter(User.email == "staff@317atc.co.uk").first()
    if user is None:
        api.get("/assessments/overview", headers=STAFF)
        user = db.query(User).filter(User.email == "staff@317atc.co.uk").one()
    db.add(BaderCredentials(user_id=user.id, role_username="u"))
    db.commit()

    res = api.post("/assessments/upload-to-bader", json={"assessment_ids": [sid]}, headers=STAFF).json()
    assert res == {"status": "started", "job_id": "job123", "assessment_ids": [sid]}
    assert started == {"job": "job123", "ids": [sid]}


def test_upload_refuses_when_ram_is_low(api, db, cadet, monkeypatch):
    from fastapi import HTTPException

    def low(email):
        raise HTTPException(status_code=503, detail="Server RAM too low")

    monkeypatch.setattr(a.scrapers, "create_upload_job", low)
    sid = _add(api, "radio", radio()).json()["assessment_id"]
    api.get("/assessments/overview", headers=STAFF)
    user = db.query(User).filter(User.email == "staff@317atc.co.uk").one()
    db.add(BaderCredentials(user_id=user.id, role_username="u"))
    db.commit()
    assert api.post("/assessments/upload-to-bader", json={"assessment_ids": [sid]}, headers=STAFF).status_code == 503
