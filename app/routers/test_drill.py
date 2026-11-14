"""The drill syllabus tracker over HTTP: who may read, record and edit, and
that records are never lost to an edit of the syllabus."""

from datetime import date, timedelta

import pytest

from database.models import Cadet, CadetDrillLesson, CadetDrillTest, DrillLesson, DrillStage

CIN, OTHER = 2100001, 2100002


@pytest.fixture
def syllabus(db):
    """Two stages: the first tested with a pass mark, the second untested."""
    one = DrillStage(name="Stage 1", position=0, test_name="5x5 drill test", max_score=25, pass_mark=15)
    two = DrillStage(name="Stage 2", position=1)
    db.add_all([one, two])
    db.flush()
    db.add_all([
        DrillLesson(stage_id=one.id, name="Turns at the halt", position=0),
        DrillLesson(stage_id=one.id, name="Saluting", position=1),
        DrillLesson(stage_id=two.id, name="Wheeling", position=0),
        Cadet(cin=CIN, first_name="Siân", last_name="Ó Briain", rank="Cadet", flight="A"),
        Cadet(cin=OTHER, first_name="Tom", last_name="Able", rank="Cpl", flight="B"),
    ])
    db.commit()
    lessons = {lesson.name: lesson.id for lesson in db.query(DrillLesson)}
    return {"one": one.id, "two": two.id, **lessons}


def _cadet(api, cin=CIN, persona="staff"):
    body = api.get("/drill/progress", headers=api.as_(persona)).json()
    return next(c for c in body["cadets"] if c["cin"] == cin)


# ── access ────────────────────────────────────────────────────────────────────

READS = [("get", "/drill/syllabus"), ("get", "/drill/progress")]
RECORDS = [
    ("put", "/cadets/{cin}/drill/lessons/{lesson}", {"taught": True, "passed": False}),
    ("post", "/drill/lessons/{lesson}/mark", {"cins": [CIN]}),
    ("post", "/cadets/{cin}/drill/tests", {"stage_id": "{one}", "tested_on": "2026-01-10", "score": 20}),
]
EDITS = [
    ("post", "/drill/stages", {"name": "Stage 3"}),
    ("put", "/drill/stages/{one}", {"name": "Renamed"}),
    ("put", "/drill/stages-order", {"ids": ["{two}", "{one}"]}),
    ("post", "/drill/stages/{one}/lessons", {"name": "Eyes right"}),
    ("put", "/drill/lessons/{lesson}", {"name": "Renamed"}),
    ("put", "/drill/stages/{one}/lessons-order", {"ids": ["{lesson}", "{lesson2}"]}),
]


def _fill(value, ids):
    if isinstance(value, str):
        filled = value.format(cin=CIN, lesson=ids["Turns at the halt"], lesson2=ids["Saluting"], **ids)
        return int(filled) if value.startswith("{") and filled.isdigit() else filled
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


def _call(api, method, path, body, ids, persona=None):
    headers = api.as_(persona) if persona else {}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = _fill(body, ids)
    return getattr(api, method)(_fill(path, ids), **kwargs)


@pytest.mark.parametrize("method, path, body", [(*r, None) for r in READS] + RECORDS + EDITS)
def test_no_token_is_401(api, syllabus, method, path, body):
    assert _call(api, method, path, body, syllabus).status_code == 401


@pytest.mark.parametrize("method, path", READS)
@pytest.mark.parametrize("persona", ["staff", "snco", "nco", "oc"])
def test_staff_and_every_nco_can_see_the_tracker(api, syllabus, method, path, persona):
    assert _call(api, method, path, None, syllabus, persona).status_code == 200


@pytest.mark.parametrize("method, path", READS)
def test_cadets_cant_see_the_tracker(api, syllabus, method, path):
    assert _call(api, method, path, None, syllabus, "cadet").status_code == 403


@pytest.mark.parametrize("method, path, body", RECORDS)
@pytest.mark.parametrize("persona", ["nco", "cadet"])
def test_ncos_and_cadets_cant_sign_anyone_off(api, db, syllabus, method, path, body, persona):
    assert _call(api, method, path, body, syllabus, persona).status_code == 403
    assert db.query(CadetDrillLesson).count() == db.query(CadetDrillTest).count() == 0


@pytest.mark.parametrize("method, path, body", RECORDS)
@pytest.mark.parametrize("persona", ["staff", "snco"])
def test_staff_and_sncos_can_sign_cadets_off(api, syllabus, method, path, body, persona):
    assert _call(api, method, path, body, syllabus, persona).status_code in (200, 201)


@pytest.mark.parametrize("method, path, body", EDITS)
@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_edit_the_syllabus(api, db, syllabus, method, path, body, persona):
    before = [(s.name, s.position) for s in db.query(DrillStage).order_by(DrillStage.id)]
    assert _call(api, method, path, body, syllabus, persona).status_code == 403
    db.expire_all()
    assert [(s.name, s.position) for s in db.query(DrillStage).order_by(DrillStage.id)] == before


@pytest.mark.parametrize("method, path, body", EDITS)
def test_staff_edit_the_syllabus(api, syllabus, method, path, body):
    assert _call(api, method, path, body, syllabus, "staff").status_code in (200, 201)


# ── the syllabus ──────────────────────────────────────────────────────────────

def test_the_syllabus_lists_stages_and_lessons_in_order(api, syllabus):
    body = api.get("/drill/syllabus", headers=api.as_("nco")).json()
    assert [(s["name"], [lesson["name"] for lesson in s["lessons"]]) for s in body] == [
        ("Stage 1", ["Turns at the halt", "Saluting"]),
        ("Stage 2", ["Wheeling"]),
    ]
    assert body[0]["test_name"] == "5x5 drill test" and body[1]["test_name"] is None


def test_an_empty_syllabus_is_an_empty_list(api):
    assert api.get("/drill/syllabus", headers=api.as_("staff")).json() == []
    assert api.get("/drill/progress", headers=api.as_("staff")).json() == {"stages": [], "cadets": []}


def test_a_new_stage_goes_on_the_end(api, syllabus):
    api.post("/drill/stages", json={"name": "  Colour   party ", "test_name": "Colour drill"},
             headers=api.as_("staff"))
    body = api.get("/drill/syllabus", headers=api.as_("staff")).json()
    assert [s["name"] for s in body] == ["Stage 1", "Stage 2", "Colour party"]


def test_reordering_stages_changes_the_order(api, syllabus):
    api.put("/drill/stages-order", json={"ids": [syllabus["two"], syllabus["one"]]}, headers=api.as_("staff"))
    assert [s["name"] for s in api.get("/drill/syllabus", headers=api.as_("staff")).json()] == ["Stage 2", "Stage 1"]


@pytest.mark.parametrize("ids", [[], ["one"], ["one", "one"], ["one", "two", 999]])
def test_an_order_that_doesnt_list_each_stage_once_is_400(api, syllabus, ids):
    ids = [syllabus.get(i, i) for i in ids]
    assert api.put("/drill/stages-order", json={"ids": ids}, headers=api.as_("staff")).status_code == 400


def test_a_lesson_order_must_be_the_stages_own_lessons(api, syllabus):
    # Wheeling belongs to stage 2: listing it under stage 1 would move nothing sensibly.
    r = api.put(f"/drill/stages/{syllabus['one']}/lessons-order",
                json={"ids": [syllabus["Saluting"], syllabus["Wheeling"]]}, headers=api.as_("staff"))
    assert r.status_code == 400


def test_lessons_reorder_within_their_stage(api, syllabus):
    r = api.put(f"/drill/stages/{syllabus['one']}/lessons-order",
                json={"ids": [syllabus["Saluting"], syllabus["Turns at the halt"]]}, headers=api.as_("staff"))
    assert [lesson["name"] for lesson in r.json()["lessons"]] == ["Saluting", "Turns at the halt"]


@pytest.mark.parametrize("body, message", [
    ({"name": "   "}, "Enter a name"),
    ({"name": "x" * 121}, "at most 120"),
    ({"name": "S", "max_score": 25}, "needs a test"),
    ({"name": "S", "test_name": "5x5", "max_score": 20, "pass_mark": 21}, "more than the maximum"),
    ({"name": "S", "test_name": "5x5", "max_score": 0}, "greater than or equal to 1"),
])
def test_a_bad_stage_is_422(api, syllabus, body, message):
    r = api.post("/drill/stages", json=body, headers=api.as_("staff"))
    assert r.status_code == 422
    assert message in str(r.json())


def test_a_blank_test_name_means_no_test(api, syllabus):
    r = api.put(f"/drill/stages/{syllabus['two']}", json={"name": "Stage 2", "test_name": "  "},
                headers=api.as_("staff"))
    assert r.json()["test_name"] is None


@pytest.mark.parametrize("method, path", [
    ("put", "/drill/stages/999"), ("delete", "/drill/stages/999"),
    ("post", "/drill/stages/999/lessons"), ("put", "/drill/lessons/999"), ("delete", "/drill/lessons/999"),
    ("put", "/drill/stages/999/lessons-order"),
])
def test_editing_something_that_isnt_there_is_404(api, syllabus, method, path):
    body = {"ids": []} if path.endswith("order") else {"name": "X"}
    kwargs = {} if method == "delete" else {"json": body}
    assert getattr(api, method)(path, headers=api.as_("staff"), **kwargs).status_code == 404


def test_an_unused_lesson_can_be_deleted(api, db, syllabus):
    assert api.delete(f"/drill/lessons/{syllabus['Wheeling']}", headers=api.as_("staff")).status_code == 204
    assert db.get(DrillLesson, syllabus["Wheeling"]) is None


def test_a_lesson_with_records_cant_be_deleted_so_the_history_stays(api, db, syllabus):
    api.put(f"/cadets/{CIN}/drill/lessons/{syllabus['Wheeling']}", json={"taught": True, "passed": False},
            headers=api.as_("staff"))
    r = api.delete(f"/drill/lessons/{syllabus['Wheeling']}", headers=api.as_("staff"))
    assert r.status_code == 409 and "Rename it" in r.json()["detail"]
    assert db.query(CadetDrillLesson).count() == 1


def test_a_stage_with_lessons_cant_be_deleted(api, syllabus):
    assert api.delete(f"/drill/stages/{syllabus['two']}", headers=api.as_("staff")).status_code == 409


def test_a_stage_with_test_results_cant_be_deleted(api, db, syllabus):
    db.add(DrillStage(id=50, name="Old", position=5, test_name="5x5"))
    db.add(CadetDrillTest(cadet_id=CIN, stage_id=50, tested_on=date(2026, 1, 1), passed=True,
                          tested_by="x", recorded_at=date(2026, 1, 1)))
    db.commit()
    assert api.delete("/drill/stages/50", headers=api.as_("staff")).status_code == 409


def test_an_empty_unused_stage_can_be_deleted(api, db, syllabus):
    sid = api.post("/drill/stages", json={"name": "Spare"}, headers=api.as_("staff")).json()["id"]
    assert api.delete(f"/drill/stages/{sid}", headers=api.as_("staff")).status_code == 204
    assert db.get(DrillStage, sid) is None


# ── recording lessons ─────────────────────────────────────────────────────────

def test_a_lesson_is_taught_then_passed_and_who_passed_it_is_kept(api, syllabus):
    lesson = syllabus["Turns at the halt"]
    api.put(f"/cadets/{CIN}/drill/lessons/{lesson}", json={"taught": True, "passed": False}, headers=api.as_("snco"))
    api.put(f"/cadets/{CIN}/drill/lessons/{lesson}", json={"taught": True, "passed": True}, headers=api.as_("staff"))
    mark = _cadet(api)["lessons"][str(lesson)]
    assert mark["taught_at"] and mark["passed_at"]
    assert mark["passed_by"] == "staff@317atc.co.uk"


def test_ticking_again_keeps_the_first_date(api, db, syllabus):
    lesson = syllabus["Turns at the halt"]
    url = f"/cadets/{CIN}/drill/lessons/{lesson}"
    first = api.put(url, json={"taught": True, "passed": False}, headers=api.as_("staff")).json()["taught_at"]
    again = api.put(url, json={"taught": True, "passed": False}, headers=api.as_("snco")).json()["taught_at"]
    assert first == again
    assert db.query(CadetDrillLesson).one().taught_by == "staff@317atc.co.uk"


def test_unticking_passed_keeps_taught(api, syllabus):
    url = f"/cadets/{CIN}/drill/lessons/{syllabus['Saluting']}"
    api.put(url, json={"taught": True, "passed": True}, headers=api.as_("staff"))
    r = api.put(url, json={"taught": True, "passed": False}, headers=api.as_("staff")).json()
    assert r["taught_at"] and r["passed_at"] is None


def test_unticking_both_removes_the_record(api, db, syllabus):
    url = f"/cadets/{CIN}/drill/lessons/{syllabus['Saluting']}"
    api.put(url, json={"taught": True, "passed": True}, headers=api.as_("staff"))
    api.put(url, json={"taught": False, "passed": False}, headers=api.as_("staff"))
    assert db.query(CadetDrillLesson).count() == 0


def test_unticking_something_never_recorded_is_fine(api, db, syllabus):
    r = api.put(f"/cadets/{CIN}/drill/lessons/{syllabus['Saluting']}", json={"taught": False, "passed": False},
                headers=api.as_("staff"))
    assert r.status_code == 200 and db.query(CadetDrillLesson).count() == 0


def test_passed_without_taught_is_422(api, syllabus):
    r = api.put(f"/cadets/{CIN}/drill/lessons/{syllabus['Saluting']}", json={"taught": False, "passed": True},
                headers=api.as_("staff"))
    assert r.status_code == 422 and "taught before" in str(r.json())


@pytest.mark.parametrize("cin, lesson", [(999, "Saluting"), (CIN, 999)])
def test_an_unknown_cadet_or_lesson_is_404(api, syllabus, cin, lesson):
    lesson = syllabus.get(lesson, lesson)
    r = api.put(f"/cadets/{cin}/drill/lessons/{lesson}", json={"taught": True, "passed": False},
                headers=api.as_("staff"))
    assert r.status_code == 404


def test_a_squad_is_marked_taught_in_one_go_without_undoing_a_pass(api, syllabus):
    lesson = syllabus["Turns at the halt"]
    api.put(f"/cadets/{OTHER}/drill/lessons/{lesson}", json={"taught": True, "passed": True},
            headers=api.as_("staff"))
    r = api.post(f"/drill/lessons/{lesson}/mark", json={"cins": [CIN, OTHER]}, headers=api.as_("snco"))
    assert r.json() == {"changed": 1}  # the other cadet was already further on
    assert _cadet(api)["lessons"][str(lesson)]["passed_at"] is None
    assert _cadet(api, OTHER)["lessons"][str(lesson)]["passed_at"]


def test_a_squad_can_be_passed_in_one_go(api, syllabus):
    lesson = syllabus["Saluting"]
    api.post(f"/drill/lessons/{lesson}/mark", json={"cins": [CIN], "passed": True}, headers=api.as_("staff"))
    assert _cadet(api)["lessons"][str(lesson)]["passed_at"]


def test_one_unknown_cin_in_a_squad_saves_nothing(api, db, syllabus):
    r = api.post(f"/drill/lessons/{syllabus['Saluting']}/mark", json={"cins": [CIN, 42]}, headers=api.as_("staff"))
    assert r.status_code == 404 and "42" in r.json()["detail"]
    assert db.query(CadetDrillLesson).count() == 0


def test_an_empty_squad_is_422(api, syllabus):
    r = api.post(f"/drill/lessons/{syllabus['Saluting']}/mark", json={"cins": []}, headers=api.as_("staff"))
    assert r.status_code == 422


# ── tests and progress ────────────────────────────────────────────────────────

def _pass_stage_one_lessons(api, syllabus, cin=CIN):
    for name in ("Turns at the halt", "Saluting"):
        api.put(f"/cadets/{cin}/drill/lessons/{syllabus[name]}", json={"taught": True, "passed": True},
                headers=api.as_("staff"))


def test_passing_every_lesson_makes_a_cadet_ready_for_the_test(api, syllabus):
    _pass_stage_one_lessons(api, syllabus)
    c = _cadet(api, persona="nco")
    assert c["stages"][str(syllabus["one"])] == "ready"
    assert c["ready_for_test"] == [syllabus["one"]]
    assert c["current"] == syllabus["one"]


def test_a_failed_attempt_is_kept_and_a_later_pass_completes_the_stage(api, syllabus):
    _pass_stage_one_lessons(api, syllabus)
    url = f"/cadets/{CIN}/drill/tests"
    first = api.post(url, json={"stage_id": syllabus["one"], "tested_on": "2026-01-05", "score": 12},
                     headers=api.as_("snco")).json()
    second = api.post(url, json={"stage_id": syllabus["one"], "tested_on": "2026-01-19", "score": 19,
                                 "notes": " Sharp turns "}, headers=api.as_("snco")).json()
    assert (first["passed"], second["passed"]) == (False, True)
    c = _cadet(api)
    assert [t["score"] for t in c["tests"]] == [12, 19]
    assert c["tests"][1]["notes"] == "Sharp turns"
    assert c["stages"][str(syllabus["one"])] == "complete"
    assert c["current"] == syllabus["two"]


def test_the_pass_mark_overrides_a_ticked_pass(api, syllabus):
    r = api.post(f"/cadets/{CIN}/drill/tests",
                 json={"stage_id": syllabus["one"], "tested_on": "2026-01-05", "score": 10, "passed": True},
                 headers=api.as_("staff"))
    assert r.json()["passed"] is False


@pytest.mark.parametrize("body, status, message", [
    ({"score": 30}, 422, "between 0 and 25"),
    ({}, 422, "pass mark is 15"),
    ({"score": 20, "tested_on": (date.today() + timedelta(days=1)).isoformat()}, 422, "future"),
    ({"score": 20, "stage_id": 999}, 404, "Stage not found"),
])
def test_a_bad_attempt_is_refused(api, db, syllabus, body, status, message):
    payload = {"stage_id": syllabus["one"], "tested_on": "2026-01-05", **body}
    r = api.post(f"/cadets/{CIN}/drill/tests", json=payload, headers=api.as_("staff"))
    assert r.status_code == status and message in r.json()["detail"]
    assert db.query(CadetDrillTest).count() == 0


def test_a_stage_without_a_test_cant_be_tested(api, syllabus):
    r = api.post(f"/cadets/{CIN}/drill/tests", json={"stage_id": syllabus["two"], "tested_on": "2026-01-05",
                                                     "passed": True}, headers=api.as_("staff"))
    assert r.status_code == 409


def test_a_test_for_an_unknown_cadet_is_404(api, syllabus):
    r = api.post("/cadets/999/drill/tests", json={"stage_id": syllabus["one"], "tested_on": "2026-01-05",
                                                  "score": 20}, headers=api.as_("staff"))
    assert r.status_code == 404


def test_a_mistaken_attempt_can_be_deleted(api, db, syllabus):
    tid = api.post(f"/cadets/{CIN}/drill/tests", json={"stage_id": syllabus["one"], "tested_on": "2026-01-05",
                                                       "score": 20}, headers=api.as_("staff")).json()["id"]
    assert api.delete(f"/drill/tests/{tid}", headers=api.as_("nco")).status_code == 403
    assert api.delete(f"/drill/tests/{tid}", headers=api.as_("snco")).status_code == 204
    assert api.delete(f"/drill/tests/{tid}", headers=api.as_("snco")).status_code == 404
    assert db.query(CadetDrillTest).count() == 0


def test_progress_lists_every_cadet_even_with_no_records(api, syllabus):
    body = api.get("/drill/progress", headers=api.as_("staff")).json()
    names = [c["name"] for c in body["cadets"]]
    assert names == ["Tom Able", "Siân Ó Briain"]
    fresh = body["cadets"][0]
    assert fresh["lessons"] == {} and fresh["tests"] == [] and fresh["current"] == syllabus["one"]
    # Nothing personal goes to NCOs: no email, phone or date of birth keys.
    assert set(fresh) == {"cin", "name", "rank", "flight", "lessons", "tests", "stages", "current",
                          "ready_for_test"}


def test_removing_a_cadet_removes_their_drill_records(api, db, syllabus):
    _pass_stage_one_lessons(api, syllabus)
    api.post(f"/cadets/{CIN}/drill/tests", json={"stage_id": syllabus["one"], "tested_on": "2026-01-05",
                                                 "score": 20}, headers=api.as_("staff"))
    db.expire_all()
    db.delete(db.get(Cadet, CIN))
    db.commit()
    assert db.query(CadetDrillLesson).count() == db.query(CadetDrillTest).count() == 0
