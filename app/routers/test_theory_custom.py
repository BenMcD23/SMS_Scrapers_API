"""Sections and QR subjects staff add beside the built-in syllabus: who may add
them, the codes they get (and keep), checking in with them, recording and
progress, and the rules that keep printed codes and the syllabus safe."""

import pytest

from core.lesson_codes import LESSON_BY_CODE, LESSON_CODE_ALPHABET, LESSON_CODES
from core.theory_catalogue import catalogue
from core.theory_progress import QR_CHECK_IN
from database.models import Cadet, CadetTheoryLesson, CadetTheoryProgress, TheoryLessonCode

STAFF = {"Authorization": "Bearer staff"}
CADET = {"Authorization": "Bearer cadet"}


@pytest.fixture
def roster(db):
    db.add(Cadet(cin=1, first_name="Cara", last_name="Cadet", email="cadet@317atc.co.uk", flight="A"))
    db.commit()


def section(api, name="Fieldcraft"):
    res = api.post("/cadets/theory/sections", json={"name": name}, headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


def subject(api, name="Shelter Building", section_name="Fieldcraft", parts=2, **over):
    res = api.post("/cadets/theory/subjects", json={"name": name, "section": section_name, "parts": parts, **over},
                   headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


# ── access ────────────────────────────────────────────────────────────────────

WRITES = [
    ("post", "/cadets/theory/sections", {"name": "X"}),
    ("put", "/cadets/theory/sections/1", {"name": "X"}),
    ("delete", "/cadets/theory/sections/1", None),
    ("post", "/cadets/theory/subjects", {"name": "X", "section": "Badges"}),
    ("put", "/cadets/theory/subjects/custom_x", {"name": "X", "section": "Badges"}),
    ("delete", "/cadets/theory/subjects/custom_x", None),
]


@pytest.mark.parametrize("method,path,body", WRITES + [
    ("get", "/cadets/theory/sections", None), ("get", "/cadets/theory/subjects", None),
])
def test_managing_qr_codes_is_staff_only(api, method, path, body):
    kw = {"json": body} if body else {}
    assert getattr(api, method)(path, **kw).status_code == 401
    for persona in ("snco", "nco", "cadet"):
        assert getattr(api, method)(path, headers=api.as_(persona), **kw).status_code == 403, persona


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_role_can_add_a_section(api, persona):
    res = api.post("/cadets/theory/sections", json={"name": f"Section {persona}"}, headers=api.as_(persona))
    assert res.status_code == 201


# ── sections ──────────────────────────────────────────────────────────────────

def test_sections_list_the_built_in_ones_then_staffs(api):
    section(api, "Fieldcraft")
    rows = api.get("/cadets/theory/sections", headers=STAFF).json()
    assert [r["name"] for r in rows] == ["Badges", "Leading", "Senior/Master", "Fieldcraft"]
    by = {r["name"]: r for r in rows}
    assert by["Badges"]["can_add"] and by["Fieldcraft"]["can_add"]
    # The classification syllabus is read from source by the planner and stats.
    assert not by["Leading"]["can_add"] and not by["Senior/Master"]["can_add"]
    assert by["Badges"]["subjects"] == 4 and by["Fieldcraft"]["subjects"] == 0
    assert by["Badges"]["built_in"] and not by["Fieldcraft"]["built_in"]


@pytest.mark.parametrize("name", ["fieldcraft", "  FIELDCRAFT ", "badges", "Leading"])
def test_section_names_are_unique_ignoring_case_and_built_ins_count(api, name):
    section(api, "Fieldcraft")
    assert api.post("/cadets/theory/sections", json={"name": name}, headers=STAFF).status_code == 409


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "   "}, {"name": "x" * 61}, {}])
def test_bad_section_names_are_refused(api, body):
    assert api.post("/cadets/theory/sections", json=body, headers=STAFF).status_code == 422


def test_renaming_a_section_moves_its_subjects_and_keeps_their_codes(api):
    s = section(api)
    sub = subject(api)
    res = api.put(f"/cadets/theory/sections/{s['id']}", json={"name": "Fieldcraft & Survival"}, headers=STAFF)
    assert res.status_code == 200
    [after] = api.get("/cadets/theory/subjects", headers=STAFF).json()
    assert after["category"] == "Fieldcraft & Survival" and after["codes"] == sub["codes"]


def test_a_section_cant_be_renamed_onto_another(api):
    a = section(api, "Fieldcraft")
    section(api, "Drill")
    assert api.put(f"/cadets/theory/sections/{a['id']}", json={"name": "drill"}, headers=STAFF).status_code == 409
    assert api.put(f"/cadets/theory/sections/{a['id']}", json={"name": "Badges"}, headers=STAFF).status_code == 409
    # Its own name, re-cased, is fine.
    assert api.put(f"/cadets/theory/sections/{a['id']}", json={"name": "FIELDCRAFT"}, headers=STAFF).status_code == 200


def test_a_section_with_subjects_cant_be_deleted_but_an_empty_one_can(api):
    s = section(api)
    sub = subject(api)
    assert api.delete(f"/cadets/theory/sections/{s['id']}", headers=STAFF).status_code == 409
    assert api.delete(f"/cadets/theory/subjects/{sub['key']}", headers=STAFF).status_code == 204
    assert api.delete(f"/cadets/theory/sections/{s['id']}", headers=STAFF).status_code == 204


def test_missing_sections_are_404(api):
    assert api.put("/cadets/theory/sections/9", json={"name": "X"}, headers=STAFF).status_code == 404
    assert api.delete("/cadets/theory/sections/9", headers=STAFF).status_code == 404


# ── subjects and their codes ──────────────────────────────────────────────────

def test_a_new_subject_gets_a_fresh_code_per_lesson(api, db):
    section(api)
    sub = subject(api, parts=3)
    assert sub["key"].startswith("custom_") and sub["parts"] == 3 and sub["used"] is False
    codes = [c["code"] for c in sub["codes"]]
    assert [c["part"] for c in sub["codes"]] == [1, 2, 3]
    for code in codes:
        assert len(code) == 8 and set(code) <= set(LESSON_CODE_ALPHABET)
        assert code not in LESSON_BY_CODE  # never a built-in one
    assert len(set(codes)) == 3


def test_new_subjects_show_on_the_qr_page_after_the_built_ins_in_their_section(api):
    section(api)
    subject(api, name="Map Symbols", section_name="Badges", parts=1)
    sub = subject(api, parts=2)
    codes = api.get("/cadets/theory/qr-codes", headers=STAFF).json()
    badges = [c["name"] for c in codes if c["category"] == "Badges"]
    assert badges[-1] == "Map Symbols" and badges[0] == "Blue Radio"
    mine = [c for c in codes if c["key"] == sub["key"]]
    assert [(c["part"], c["code"], c["custom"]) for c in mine] == [
        (c["part"], c["code"], True) for c in sub["codes"]
    ]
    assert codes[-1]["category"] == "Fieldcraft"


def test_the_built_in_codes_are_untouched(api):
    section(api)
    subject(api)
    served = {(c["key"], c["part"]): c["code"] for c in api.get("/cadets/theory/qr-codes", headers=STAFF).json()
              if not c["custom"]}
    assert served == LESSON_CODES


def test_subjects_cant_be_added_to_the_classification_syllabus(api):
    for name in ("Leading", "senior/master"):
        res = api.post("/cadets/theory/subjects", json={"name": "X", "section": name}, headers=STAFF)
        assert res.status_code == 400 and "syllabus" in res.json()["detail"]


def test_a_subject_needs_a_section_that_exists(api):
    assert api.post("/cadets/theory/subjects", json={"name": "X", "section": "Nowhere"},
                    headers=STAFF).status_code == 404


def test_a_section_name_is_matched_ignoring_case(api):
    assert subject(api, section_name="badges", parts=1)["category"] == "Badges"


@pytest.mark.parametrize("over", [{"name": ""}, {"name": "  "}, {"parts": 0}, {"parts": 11}, {"name": "x" * 121}])
def test_bad_subjects_are_refused(api, over):
    body = {"name": "X", "section": "Badges", "parts": 1, **over}
    assert api.post("/cadets/theory/subjects", json=body, headers=STAFF).status_code == 422


def test_two_subjects_in_a_section_cant_share_a_name(api):
    section(api)
    subject(api)
    assert api.post("/cadets/theory/subjects", json={"name": "shelter building", "section": "Fieldcraft"},
                    headers=STAFF).status_code == 409
    # A built-in name in its own section counts too.
    assert api.post("/cadets/theory/subjects", json={"name": "Blue Radio", "section": "Badges"},
                    headers=STAFF).status_code == 409


def test_two_subjects_with_the_same_name_get_different_keys(api):
    section(api, "A")
    section(api, "B")
    a = subject(api, name="Knots", section_name="A")
    b = subject(api, name="Knots", section_name="B")
    assert a["key"] != b["key"]


def test_non_ascii_names_are_kept(api):
    sub = subject(api, name="Pont d’Été", section_name="Badges", parts=1)
    assert sub["name"] == "Pont d’Été" and sub["key"].startswith("custom_")


def test_adding_lessons_keeps_the_old_codes_and_adds_new_ones(api):
    section(api)
    sub = subject(api, parts=1)
    res = api.put(f"/cadets/theory/subjects/{sub['key']}", json={"name": "Shelter Building", "section": "Fieldcraft",
                                                                   "parts": 3}, headers=STAFF)
    assert res.status_code == 200
    codes = res.json()["codes"]
    assert codes[0] == sub["codes"][0] and len(codes) == 3


def test_lessons_cant_be_taken_away_once_given_codes(api):
    section(api)
    sub = subject(api, parts=3)
    res = api.put(f"/cadets/theory/subjects/{sub['key']}", json={"name": "Shelter Building", "section": "Fieldcraft",
                                                                   "parts": 2}, headers=STAFF)
    assert res.status_code == 409


def test_built_in_subjects_cant_be_edited_or_deleted(api):
    body = {"name": "Blue Radio", "section": "Badges", "parts": 2}
    assert api.put("/cadets/theory/subjects/blue_radio", json=body, headers=STAFF).status_code == 400
    assert api.delete("/cadets/theory/subjects/blue_radio", headers=STAFF).status_code == 400


def test_missing_subjects_are_404(api):
    body = {"name": "X", "section": "Badges"}
    assert api.put("/cadets/theory/subjects/custom_nope", json=body, headers=STAFF).status_code == 404
    assert api.delete("/cadets/theory/subjects/custom_nope", headers=STAFF).status_code == 404


# ── checking in, recording and progress ───────────────────────────────────────

def test_a_cadet_checks_in_with_a_new_subjects_code(api, db, roster):
    section(api)
    sub = subject(api, parts=2)
    res = api.post(f"/cadets/me/lessons/{sub['codes'][1]['code']}", headers=CADET)
    assert res.status_code == 200
    body = res.json()
    assert (body["key"], body["part"], body["custom"], body["complete"]) == (sub["key"], 2, True, False)
    row = db.query(CadetTheoryLesson).one()
    assert (row.lesson_key, row.part, row.recorded_by) == (sub["key"], 2, QR_CHECK_IN)


def test_every_lesson_in_marks_a_new_subject_taught(api, db, roster):
    section(api)
    sub = subject(api, parts=2)
    for c in sub["codes"]:
        api.post(f"/cadets/me/lessons/{c['code']}", headers=CADET)
    assert db.query(CadetTheoryProgress).filter_by(cadet_id=1, lesson_key=sub["key"]).one()


def test_staff_can_record_a_new_subject_and_it_shows_in_progress(api, roster):
    section(api)
    sub = subject(api, parts=2)
    res = api.post("/cadets/theory/mark", json={"cadet_cins": [1], "lessons": [{"key": sub["key"], "part": 1}]},
                   headers=STAFF)
    assert res.status_code == 200
    [cara] = api.get("/cadets/theory/progress", headers=STAFF).json()
    assert cara["subjects"][sub["key"]] == {"lessons": [1], "taught": False, "passed": False}
    keys = [lesson["key"] for lesson in api.get("/cadets/theory/lessons", headers=STAFF).json()]
    assert sub["key"] in keys


def test_a_retired_subjects_codes_stop_working_and_it_leaves_the_page(api, db, roster):
    section(api)
    sub = subject(api, parts=1)
    code = sub["codes"][0]["code"]
    api.put(f"/cadets/theory/subjects/{sub['key']}", json={"name": "Shelter Building", "section": "Fieldcraft",
                                                             "parts": 1, "active": False}, headers=STAFF)
    assert api.post(f"/cadets/me/lessons/{code}", headers=CADET).status_code == 404
    assert sub["key"] not in {c["key"] for c in api.get("/cadets/theory/qr-codes", headers=STAFF).json()}
    # Still listed for staff to bring back, with the same code.
    [listed] = api.get("/cadets/theory/subjects", headers=STAFF).json()
    assert listed["active"] is False and listed["codes"][0]["code"] == code
    api.put(f"/cadets/theory/subjects/{sub['key']}", json={"name": "Shelter Building", "section": "Fieldcraft",
                                                             "parts": 1, "active": True}, headers=STAFF)
    assert api.post(f"/cadets/me/lessons/{code}", headers=CADET).status_code == 200


def test_a_subject_with_lessons_recorded_is_retired_not_deleted(api, roster):
    section(api)
    sub = subject(api, parts=1)
    api.post(f"/cadets/me/lessons/{sub['codes'][0]['code']}", headers=CADET)
    res = api.delete(f"/cadets/theory/subjects/{sub['key']}", headers=STAFF)
    assert res.status_code == 409 and "Retire" in res.json()["detail"]
    [listed] = api.get("/cadets/theory/subjects", headers=STAFF).json()
    assert listed["used"] is True


def test_a_deleted_subjects_codes_are_never_handed_out_again(api, db):
    section(api)
    sub = subject(api, parts=2)
    api.delete(f"/cadets/theory/subjects/{sub['key']}", headers=STAFF)
    # The codes stay reserved, so one already printed can't open a new subject.
    kept = {c for (c,) in db.query(TheoryLessonCode.code)}
    assert {c["code"] for c in sub["codes"]} <= kept
    assert not set(catalogue(db).by_code) & {c["code"] for c in sub["codes"]}


def test_an_unknown_code_is_still_a_404(api, roster):
    assert api.post("/cadets/me/lessons/zzzzzzzz", headers=CADET).status_code == 404


def test_with_nothing_added_the_catalogue_is_the_built_in_one(db):
    from core.theory_lessons import THEORY_LESSONS
    cat = catalogue(db)
    assert cat.lessons == THEORY_LESSONS and cat.codes == LESSON_CODES and cat.custom == frozenset()


def test_a_new_subject_named_like_a_deleted_one_gets_new_codes(api):
    # Same name means the same slug; reusing the key would revive the old,
    # possibly printed, codes for a different subject.
    section(api)
    old = subject(api, parts=1)
    api.delete(f"/cadets/theory/subjects/{old['key']}", headers=STAFF)
    new = subject(api, parts=1)
    assert new["key"] != old["key"]
    assert new["codes"][0]["code"] != old["codes"][0]["code"]
