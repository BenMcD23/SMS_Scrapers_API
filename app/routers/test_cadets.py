"""Cadet records: search, roster, detail, audits, theory progress and edits."""

from datetime import datetime, timedelta

import pytest

from database.models import (
    AllEvent,
    AssessmentSheet,
    Cadet,
    CadetAttendance,
    CadetDietary,
    CadetEvent,
    CadetMedical,
    CadetQualification,
    CadetTheoryProgress,
    User,
)

STAFF = {"Authorization": "Bearer staff"}


@pytest.fixture
def roster(db):
    db.add_all([
        Cadet(cin=1, first_name="Amy", last_name="Adams", rank="Cpl", flight="A", email="amy@x",
              classification="Leading Cadet", date_of_birth=datetime(2010, 5, 1)),
        Cadet(cin=2, first_name="Bob", last_name="Brown", flight="B"),
        Cadet(cin=3, first_name="Cat", last_name="Adams", flight="C"),
    ])
    db.commit()


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,allowed", [
    ("/cadets/search?q=ad", {"staff", "snco", "nco"}),
    ("/cadets", {"staff", "snco"}),
    ("/cadets/audit/badge-types", {"staff"}),
    ("/cadets/audit/medical", {"staff"}),
    ("/cadets/theory/lessons", {"staff"}),
    ("/cadets/1", {"staff"}),
    ("/cadets/1/attendance", {"staff"}),
])
def test_read_access_by_role(api, roster, path, allowed):
    assert api.get(path).status_code == 401
    for persona in ("staff", "snco", "nco", "cadet"):
        expected = 200 if persona in allowed else 403
        assert api.get(path, headers=api.as_(persona)).status_code == expected, persona


@pytest.mark.parametrize("path", [
    "/cadets/audit/check", "/cadets/audit/event-check", "/cadets/audit/export",
    "/cadets/theory/mark", "/cadets/theory/check",
])
@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_write_and_audit_posts_are_staff_only(api, path, persona):
    assert api.post(path, json={}, headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_patch_is_staff_only(api, roster, persona):
    assert api.patch("/cadets/1", json={"banned": True}, headers=api.as_(persona)).status_code == 403


# ── search / list ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["", "a", " a ", "  "])
def test_search_needs_two_characters(api, roster, q):
    assert api.get(f"/cadets/search?q={q}", headers=STAFF).json() == []


def test_search_matches_first_last_and_full_name_case_insensitively(api, roster):
    names = lambda q: [c["first_name"] for c in api.get(f"/cadets/search?q={q}", headers=STAFF).json()]  # noqa: E731
    assert names("ADAMS") == ["Amy", "Cat"]
    assert names("bo") == ["Bob"]
    assert names("amy ad") == ["Amy"]
    assert names("zzz") == []


def test_search_is_capped_at_ten(api, db):
    db.add_all([Cadet(cin=i, first_name=f"Zed{i:02d}", last_name="Zulu") for i in range(15)])
    db.commit()
    assert len(api.get("/cadets/search?q=zulu", headers=STAFF).json()) == 10


@pytest.mark.parametrize("q", ["%25%25", "__", "%25a", "\\\\"])
def test_search_treats_sql_wildcards_as_text(api, roster, q):
    # "%" and "_" mustn't turn into match-everything.
    assert api.get(f"/cadets/search?q={q}", headers=STAFF).json() == []


def test_search_finds_names_that_really_contain_wildcard_characters(api, db):
    db.add_all([Cadet(cin=1, first_name="O_Neil", last_name="X"), Cadet(cin=2, first_name="ONeil", last_name="X")])
    db.commit()
    assert [c["cin"] for c in api.get("/cadets/search?q=O_N", headers=STAFF).json()] == [1]


def test_list_is_sorted_and_cached_until_an_edit(api, db, roster):
    first = api.get("/cadets", headers=STAFF).json()
    assert [(c["last_name"], c["first_name"]) for c in first] == [("Adams", "Amy"), ("Adams", "Cat"), ("Brown", "Bob")]

    # A direct DB write (e.g. a scraper without invalidating) is hidden by the cache...
    db.add(Cadet(cin=4, first_name="Dan", last_name="Aaron"))
    db.commit()
    assert len(api.get("/cadets", headers=STAFF).json()) == 3
    # ...but a staff edit invalidates it.
    api.patch("/cadets/2", json={"banned": False}, headers=STAFF)
    assert len(api.get("/cadets", headers=STAFF).json()) == 4


# ── detail ────────────────────────────────────────────────────────────────────

def test_detail_unknown_cadet_404(api):
    res = api.get("/cadets/99", headers=STAFF)
    assert res.status_code == 404 and "99" in res.json()["detail"]


def test_detail_assembles_every_section(api, db, roster):
    db.add(User(id=1, google_id="g", email="a@x"))
    db.add(AllEvent(id=7, title="Camp"))
    db.add_all([
        CadetQualification(cadet_id=1, qual_type="first_aid", status="true",
                           date_achieved=datetime(2025, 1, 1), date_expires=datetime(2028, 1, 1)),
        CadetEvent(event_id=7, cadet_id=1),
        CadetMedical(cadet_id=1, allergy_name="Nuts", auto_injector="Yes", severity="High"),
        CadetDietary(cadet_id=1, name="Vegan"),
        AssessmentSheet(assessment_type="radio", cadet_id=1, assessor_id=1, created_at=datetime(2026, 1, 1),
                        fields={"passed": True, "total_score": 9, "assessor_name": "X"}, pdf_data=b"%PDF"),
        AssessmentSheet(assessment_type="moi", cadet_id=1, assessor_id=1, created_at=datetime(2026, 1, 2),
                        fields={}),
    ])
    db.commit()
    body = api.get("/cadets/1", headers=STAFF).json()
    assert body["email"] == "amy@x" and body["date_of_birth"] == "2010-05-01T00:00:00"
    assert body["qualifications"][0]["qualification_name"] == "First Aid"
    assert body["qualifications"][0]["expires_date"] == "2028-01-01T00:00:00"
    assert body["events"] == [{"id": 1, "event_name": "Camp", "event_date": None, "attended": True}]
    assert body["allergies"][0]["auto_injector"] == "Yes"
    assert body["dietary"][0]["name"] == "Vegan"
    radio, moi = sorted(body["assessments"], key=lambda a: a["id"])
    assert (radio["passed"], radio["total_score"], radio["assessor_name"]) == (True, 9, "X")
    assert moi["passed"] is None
    # PDFs are never inlined in the detail response.
    assert "pdf_data" not in str(body)


def test_detail_with_nothing_attached(api, roster):
    body = api.get("/cadets/2", headers=STAFF).json()
    assert body["qualifications"] == body["events"] == body["assessments"] == []
    assert body["date_of_birth"] is None and body["banned"] is False


# ── attendance ────────────────────────────────────────────────────────────────

def test_attendance_newest_first_with_classified_state(api, db, roster):
    db.add_all([
        CadetAttendance(cadet_id=1, date=datetime(2026, 1, 1), status="Present Correctly Dressed"),
        CadetAttendance(cadet_id=1, date=datetime(2026, 1, 8), status="Authorised Absence"),
        CadetAttendance(cadet_id=1, date=datetime(2026, 1, 15), status=None),
        CadetAttendance(cadet_id=2, date=datetime(2026, 1, 15), status="Present"),
    ])
    db.commit()
    rows = api.get("/cadets/1/attendance", headers=STAFF).json()
    assert [r["state"] for r in rows] == ["absent", "authorised", "present"]
    assert api.get("/cadets/99/attendance", headers=STAFF).json() == []


# ── patch ─────────────────────────────────────────────────────────────────────

def test_patch_only_touches_the_fields_sent(api, db, roster):
    res = api.patch("/cadets/1", json={"banned": True}, headers=STAFF).json()
    assert res["updated_fields"] == ["banned"]
    cadet = db.get(Cadet, 1)
    db.refresh(cadet)
    assert cadet.banned is True and cadet.email == "amy@x"


def test_patch_phone_is_normalised_and_blank_clears(api, db, roster):
    res = api.patch("/cadets/1", json={"phone_number": "07700 900123"}, headers=STAFF).json()
    stored = res["values"]["phone_number"]
    assert stored and " " not in stored
    res = api.patch("/cadets/1", json={"phone_number": ""}, headers=STAFF).json()
    assert res["values"]["phone_number"] is None


def test_patch_rejects_a_bad_phone_and_unknown_cadet(api, roster):
    assert api.patch("/cadets/1", json={"phone_number": "not a phone"}, headers=STAFF).status_code == 400
    assert api.patch("/cadets/99", json={"banned": True}, headers=STAFF).status_code == 404


def test_patch_rejects_wrong_types(api, roster):
    assert api.patch("/cadets/1", json={"banned": "maybe"}, headers=STAFF).status_code == 422


# ── audits ────────────────────────────────────────────────────────────────────

def test_badge_types_catalogue_shape(api):
    types = api.get("/cadets/audit/badge-types", headers=STAFF).json()
    keys = [t["key"] for t in types]
    assert "first_aid" in keys and len(keys) == len(set(keys))
    assert all({"key", "name", "kind", "levels"} <= set(t) for t in types)


def test_medical_audit_lists_only_cadets_with_something_recorded(api, db, roster):
    db.add_all([CadetMedical(cadet_id=2, allergy_name="Bees"), CadetDietary(cadet_id=3, name="Halal")])
    db.commit()
    rows = api.get("/cadets/audit/medical", headers=STAFF).json()
    assert [r["cin"] for r in rows] == [3, 2]
    assert rows[1]["allergies"][0]["auto_injector"] == "No"


def test_audit_check_uses_only_unexpired_quals(api, db, roster):
    today = datetime.now()
    db.add_all([
        # Live Blue Leadership.
        CadetQualification(cadet_id=1, qual_type="Blue Leadership", status="true",
                           date_achieved=datetime(2025, 3, 4), has_attachment=False),
        # Lapsed first aid: must not count.
        CadetQualification(cadet_id=2, qual_type="Essential First Aid", status="true",
                           date_expires=today - timedelta(days=1), has_attachment=False),
        # Expires today: still valid until tomorrow.
        CadetQualification(cadet_id=3, qual_type="Essential First Aid", status="true",
                           date_expires=today, has_attachment=True),
        CadetMedical(cadet_id=1, allergy_name="Nuts"),
        CadetDietary(cadet_id=1, name="Veg"),
    ])
    db.commit()
    body = {"qualifications": ["leadership", "first_aid", "not_a_badge"], "include_medical": True,
            "include_dietary": True, "include_missing_attachments": True}
    rows = {r["cin"]: r for r in api.post("/cadets/audit/check", json=body, headers=STAFF).json()}
    assert set(rows) == {1, 2, 3}

    amy = {c["qual_type"]: c for c in rows[1]["qualifications_check"]}
    # Unknown badge keys are dropped rather than crashing the audit.
    assert set(amy) == {"leadership", "first_aid"}
    assert amy["leadership"]["has"] is True and amy["leadership"]["level"] == "blue"
    assert amy["leadership"]["date_achieved"] == "2025-03-04"
    assert amy["first_aid"] == {**amy["first_aid"], "has": False, "level": None, "date_achieved": None}
    assert rows[1]["missing_attachments"] == ["Blue Leadership"]
    assert rows[1]["allergies"][0]["allergy_name"] == "Nuts" and rows[1]["dietary"][0]["name"] == "Veg"

    bob_fa = next(c for c in rows[2]["qualifications_check"] if c["qual_type"] == "first_aid")
    assert bob_fa["has"] is False
    # The lapsed qual isn't reported as a missing attachment either.
    assert rows[2]["missing_attachments"] == []
    cat_fa = next(c for c in rows[3]["qualifications_check"] if c["qual_type"] == "first_aid")
    assert cat_fa["has"] is True


def test_audit_check_filters_by_cin_and_omits_unrequested_sections(api, roster):
    rows = api.post("/cadets/audit/check", json={"cadet_cins": [2, 99]}, headers=STAFF).json()
    assert [r["cin"] for r in rows] == [2]
    assert set(rows[0]) == {"cin", "first_name", "last_name", "rank", "flight", "classification"}


def test_event_audit_only_includes_attendees(api, db, roster):
    db.add(AllEvent(id=1, title="Camp"))
    db.add_all([CadetEvent(event_id=1, cadet_id=1), CadetEvent(event_id=1, cadet_id=3)])
    db.commit()
    rows = api.post("/cadets/audit/event-check", json={"event_id": 1}, headers=STAFF).json()
    assert [r["cin"] for r in rows] == [1, 3]
    assert api.post("/cadets/audit/event-check", json={"event_id": 2}, headers=STAFF).json() == []
    assert api.post("/cadets/audit/event-check", json={}, headers=STAFF).status_code == 422


def test_audit_export_rejects_oversize_tables(api):
    too_many_rows = {"headers": ["a"], "rows": [["x"]] * 5001}
    assert api.post("/cadets/audit/export", json=too_many_rows, headers=STAFF).status_code == 422
    too_many_cols = {"headers": ["h"] * 201, "rows": []}
    assert api.post("/cadets/audit/export", json=too_many_cols, headers=STAFF).status_code == 422
    assert api.post("/cadets/audit/export", json={"rows": []}, headers=STAFF).status_code == 422


def test_audit_export_with_awkward_filename(api):
    res = api.post("/cadets/audit/export", headers=STAFF,
                   json={"filename": 'Audit "quoted" – ünïcode', "headers": ["a"], "rows": [[None]]})
    assert res.status_code == 200
    header = res.headers["content-disposition"]
    assert header.startswith('attachment; filename="Audit quoted')
    # The real name survives for browsers via RFC 5987.
    assert "filename*=UTF-8''Audit%20quoted%20%E2%80%93%20%C3%BC" in header


# ── theory ────────────────────────────────────────────────────────────────────

def test_theory_lessons_catalogue(api):
    lessons = api.get("/cadets/theory/lessons", headers=STAFF).json()
    assert lessons[0] == {"key": "blue_radio", "name": "Blue Radio", "category": "Badges"}


@pytest.mark.parametrize("body", [
    {},
    {"cadet_cins": [1]},
    {"lesson_keys": ["blue_radio"]},
    {"cadet_cins": [1], "lesson_keys": ["nonsense"]},
])
def test_theory_mark_needs_a_cadet_and_a_known_lesson(api, roster, body):
    assert api.post("/cadets/theory/mark", json=body, headers=STAFF).status_code == 400


def test_theory_mark_unknown_cadets_is_400(api, roster):
    res = api.post("/cadets/theory/mark", json={"cadet_cins": [99], "lesson_keys": ["blue_radio"]}, headers=STAFF)
    assert res.status_code == 400 and "No matching cadets" in res.json()["detail"]


def test_theory_mark_is_idempotent_and_clear_removes(api, db, roster):
    body = {"cadet_cins": [1, 2, 99], "lesson_keys": ["blue_radio", "acp_32_2", "junk"]}
    res = api.post("/cadets/theory/mark", json=body, headers=STAFF).json()
    assert res == {"status": "success", "action": "marked", "changed": 4}
    assert api.post("/cadets/theory/mark", json=body, headers=STAFF).json()["changed"] == 0
    row = db.query(CadetTheoryProgress).first()
    assert row.recorded_by == "staff@317atc.co.uk"

    cleared = api.post("/cadets/theory/mark", json={**body, "cadet_cins": [1], "completed": False},
                       headers=STAFF).json()
    assert cleared == {"status": "success", "action": "cleared", "changed": 2}
    assert db.query(CadetTheoryProgress).count() == 2


def test_theory_check_puts_cadets_needing_an_assessment_first(api, db, roster):
    # Amy is a Leading Cadet already (qualification held); Bob has done the
    # theory but not the classification — Bob needs booking, so Bob comes first.
    api.post("/cadets/theory/mark", json={"cadet_cins": [1, 2], "lesson_keys": ["acp_32_2"]}, headers=STAFF)
    rows = api.post("/cadets/theory/check", json={"lesson_keys": ["acp_32_2", "blue_radio"]}, headers=STAFF).json()
    assert [r["cin"] for r in rows] == [2, 1]
    bob = {c["lesson_key"]: c for c in rows[0]["lessons_check"]}
    assert bob["acp_32_2"]["has"] is True and bob["acp_32_2"]["has_qualification"] is False
    assert bob["blue_radio"] == {**bob["blue_radio"], "has": False, "completed_at": None}
    amy = {c["lesson_key"]: c for c in rows[1]["lessons_check"]}
    assert amy["acp_32_2"]["has_qualification"] is True


def test_theory_check_filters_and_empty_cases(api, roster):
    api.post("/cadets/theory/mark", json={"cadet_cins": [1, 2], "lesson_keys": ["blue_radio"]}, headers=STAFF)
    assert api.post("/cadets/theory/check", json={"lesson_keys": []}, headers=STAFF).json() == []
    assert api.post("/cadets/theory/check", json={"lesson_keys": ["junk"]}, headers=STAFF).json() == []
    assert api.post("/cadets/theory/check", json={"lesson_keys": ["acp_35_4"]}, headers=STAFF).json() == []
    only = api.post("/cadets/theory/check", json={"lesson_keys": ["blue_radio"], "cadet_cins": [2]},
                    headers=STAFF).json()
    assert [r["cin"] for r in only] == [2]


def test_the_audit_shows_blue_flying_proved_by_the_flying_record(api, db):
    from database.models import CadetFlight
    db.add_all([
        Cadet(cin=7, first_name="Fay", last_name="Flyer"),
        CadetQualification(cadet_id=7, qual_type="Blue ATP Ground School", status="true"),
        CadetQualification(cadet_id=7, qual_type="PTT Blue", status="true"),
        CadetFlight(cadet_id=7, date=datetime(2025, 8, 6).date(), activity="powered", aircraft="Tutor"),
    ])
    db.commit()
    rows = api.post("/cadets/audit/check", json={"qualifications": ["flying"]}, headers=STAFF).json()
    [flying] = rows[0]["qualifications_check"]
    # Bader hasn't recorded the badge yet, so there's no award date to show.
    assert flying == {**flying, "has": True, "level": "blue", "date_achieved": None}
