"""Cadet records: search, roster, detail, audits and edits."""

from datetime import date, datetime, timedelta

import pytest

from conftest import PERSONAS
from database.database import Base
from database.models import (
    AllEvent,
    AssessmentSheet,
    BadgeOrder,
    BadgeOrderItem,
    Cadet,
    CadetAttendance,
    CadetDietary,
    CadetEvent,
    CadetMedical,
    CadetQualification,
    CadetWht,
    NcoComment,
    NcoCommentReply,
    RemovedCadet,
    StoresItemIssuance,
    StoresOrder,
    StoresOrderItem,
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


# ── removing a cadet who has left SMS ─────────────────────────────────────────

def _gone(db, **over):
    """Cadet 9, flagged as off SMS, with one of almost everything that hangs off a cadet."""
    db.add(Cadet(cin=9, first_name="Finley", last_name="Kay", missing_since=datetime(2026, 10, 1), **over))
    db.add(User(id=1, google_id="g1", email="u@x"))
    db.add(CadetQualification(cadet_id=9, qual_type="Radio", status="true"))
    db.add(CadetMedical(cadet_id=9, allergy_name="Nuts"))
    db.add(CadetAttendance(cadet_id=9, date=datetime(2026, 9, 1), register_type="Parade", status="P"))
    db.add(AssessmentSheet(cadet_id=9, assessor_id=1, assessment_type="radio", fields={},
                           created_at=datetime.now()))
    db.add(NcoComment(author_id=1, subject="s", body="b", comment_date=datetime.now(), cadet_id=9,
                      cadet_name="Finley Kay", created_at=datetime.now()))
    db.flush()
    db.add(NcoCommentReply(comment_id=db.query(NcoComment.id).scalar(), author_id=1, body="r",
                           created_at=datetime.now()))
    db.add(StoresOrder(id=5, cadet_id=9, created_at=datetime.now()))
    db.add(StoresOrderItem(order_id=5, item_type="Beret", size="56"))
    db.add(StoresItemIssuance(cadet_id=9, item_category="Beret", last_given=datetime.now()))
    db.add(BadgeOrder(id=6, cadet_id=9, created_at=datetime.now()))
    db.add(BadgeOrderItem(order_id=6, badge_name="Radio"))
    db.commit()


def test_the_list_flags_cadets_not_seen_on_sms(api, db, roster):
    _gone(db)
    rows = {r["cin"]: r for r in api.get("/cadets", headers=STAFF).json()}
    assert rows[9]["missing_since"].startswith("2026-10-01") and rows[1]["missing_since"] is None


def test_removing_a_cadet_needs_staff(api, db):
    _gone(db)
    assert api.delete("/cadets/9").status_code == 401
    for persona in ("snco", "nco", "cadet"):
        assert api.delete("/cadets/9", headers=api.as_(persona)).status_code == 403, persona
    assert db.get(Cadet, 9) is not None


def test_removing_an_unknown_cadet_is_404(api):
    assert api.delete("/cadets/404", headers=STAFF).status_code == 404


def test_a_cadet_still_on_sms_cant_be_removed(api, db, roster):
    res = api.delete("/cadets/1", headers=STAFF)
    assert res.status_code == 409 and "still on SMS" in res.json()["detail"]
    assert db.get(Cadet, 1) is not None


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_removing_a_cadet_takes_their_records_but_keeps_order_sizes(api, db, persona):
    _gone(db)
    res = api.delete("/cadets/9", headers=api.as_(persona))
    assert res.status_code == 200 and res.json()["kept_orders"] == 1
    db.expire_all()
    assert db.get(Cadet, 9) is None
    for model in (CadetQualification, CadetMedical, CadetAttendance, AssessmentSheet,
                  StoresItemIssuance, BadgeOrder, BadgeOrderItem):
        assert db.query(model).count() == 0, model.__name__
    # Kept for the stock insights, but off the orders list and not "waiting".
    order = db.get(StoresOrder, 5)
    assert (order.cadet_id, order.completed) == (None, True)
    assert db.query(StoresOrderItem).one().size == "56"
    assert api.get("/stores/orders", headers=STAFF).json() == []
    [beret] = [t for t in api.get("/stores/insights", headers=STAFF).json()["items"] if t["itemType"] == "Beret"]
    assert beret["topSize"] == "56" and beret["sizes"][0]["waiting"] == 0
    assert db.query(NcoComment).count() == 0 and db.query(NcoCommentReply).count() == 0
    removed = db.get(RemovedCadet, 9)
    assert (removed.first_name, removed.removed_by) == ("Finley", PERSONAS[persona]["email"])


def test_every_table_keyed_to_a_cadet_is_cleared_on_removal(api, db):
    # Guards against a new table with a cadet FK blocking the delete on Postgres.
    _gone(db)
    api.delete("/cadets/9", headers=STAFF)
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name == "Cadets":
                assert db.execute(table.select().where(fk.parent == 9)).first() is None, table.name


def test_removing_a_cadet_twice_is_404(api, db):
    _gone(db)
    assert api.delete("/cadets/9", headers=STAFF).status_code == 200
    assert api.delete("/cadets/9", headers=STAFF).status_code == 404


# ── WHT audit ─────────────────────────────────────────────────────────────────

WEAPONS = ["L98A2", "Scorpion (magazine fed)", "Scorpion (single shot)"]


def _whts(api, *weapons, cins=(1, 2, 3)):
    body = {"cadet_cins": list(cins), "qualifications": [f"wht:{w}" for w in weapons]}
    res = api.post("/cadets/audit/check", json=body, headers=api.as_("staff"))
    assert res.status_code == 200
    return {r["cin"]: {c["qual_type"]: c for c in r["qualifications_check"]} for r in res.json()}


def test_the_audit_catalog_offers_each_weapon_as_a_current_or_expired_check(api, db, roster):
    db.add(CadetWht(cadet_id=1, weapon="Air Rifle", date_passed=date(2026, 1, 1), date_expires=date(2026, 7, 1)))
    db.commit()
    whts = [b for b in api.get("/cadets/audit/badge-types", headers=api.as_("staff")).json() if b["kind"] == "wht"]
    # Ours first, always, then any other weapon Bader returned.
    assert [(b["key"], b["name"]) for b in whts] == [(f"wht:{w}", w) for w in [*WEAPONS, "Air Rifle"]]
    assert {tuple(b["levels"]) for b in whts} == {("current", "expired")}


def test_a_wht_check_reports_each_cadets_newest_test_and_whether_it_is_current(api, db, roster):
    today = date.today()
    db.add_all([
        # Amy: an old lapsed L98A2 and a newer current one, plus an expired Scorpion.
        CadetWht(cadet_id=1, weapon="L98A2", date_passed=today - timedelta(days=400),
                 date_expires=today - timedelta(days=218), awarded_by="WO K. Duffy (AC/07333)"),
        CadetWht(cadet_id=1, weapon="L98A2", date_passed=today - timedelta(days=30),
                 date_expires=today + timedelta(days=152), awarded_by="Doherty, S Flt Lt"),
        CadetWht(cadet_id=1, weapon="Scorpion (single shot)", date_passed=today - timedelta(days=200),
                 date_expires=today - timedelta(days=18)),
        # Expiring today is still current; no expiry date at all is not.
        CadetWht(cadet_id=2, weapon="Scorpion (magazine fed)", date_passed=today - timedelta(days=182),
                 date_expires=today, awarded_by="Ó Súilleabháin, É Fg Off"),
        CadetWht(cadet_id=2, weapon="L98A2", date_passed=today - timedelta(days=10), date_expires=None),
    ])
    db.commit()

    checks = _whts(api, *WEAPONS)
    amy, bob, cat = checks[1], checks[2], checks[3]
    assert amy["wht:L98A2"] == {
        "qual_type": "wht:L98A2", "display_name": "L98A2 WHT", "kind": "wht", "level": "current", "has": True,
        "date_achieved": (today - timedelta(days=30)).isoformat(),
        "date_expires": (today + timedelta(days=152)).isoformat(), "awarded_by": "Doherty, S Flt Lt"}
    assert amy["wht:Scorpion (single shot)"]["level"] == "expired"
    assert bob["wht:Scorpion (magazine fed)"]["level"] == "current"
    assert bob["wht:Scorpion (magazine fed)"]["awarded_by"] == "Ó Súilleabháin, É Fg Off"
    assert (bob["wht:L98A2"]["level"], bob["wht:L98A2"]["date_expires"]) == ("expired", None)
    # Never tested: no level, so the audit's "None" chip finds them.
    assert {k: (c["has"], c["level"]) for k, c in cat.items()} == {f"wht:{w}": (False, None) for w in WEAPONS}


def test_wht_checks_sit_beside_badge_checks(api, db, roster):
    db.add(CadetWht(cadet_id=1, weapon="L98A2", date_passed=date.today(), date_expires=date.today()))
    db.commit()
    body = {"cadet_cins": [1], "qualifications": ["shooting", "wht:L98A2"]}
    [amy] = api.post("/cadets/audit/check", json=body, headers=api.as_("staff")).json()
    assert [c["qual_type"] for c in amy["qualifications_check"]] == ["shooting", "wht:L98A2"]


def test_an_event_audit_checks_whts_too(api, db, roster):
    db.add(AllEvent(id=9, title="Range day"))
    db.add(CadetEvent(event_id=9, cadet_id=2))
    db.add(CadetWht(cadet_id=2, weapon="L98A2", date_passed=date.today(), date_expires=date.today()))
    db.commit()
    body = {"event_id": 9, "qualifications": ["wht:L98A2"]}
    [bob] = api.post("/cadets/audit/event-check", json=body, headers=api.as_("staff")).json()
    assert bob["qualifications_check"][0]["level"] == "current"
