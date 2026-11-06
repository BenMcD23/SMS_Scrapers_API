"""The session planner's task bank and kit list over HTTP: who gets in, the
validation, the 409s that protect leadership history, linking old sheets by
hand, and the task picker on the leadership assessment form."""

from datetime import datetime

import pytest

from database.models import AssessmentSheet, Cadet, CadetQualification, SessionKit, SessionTask, User


def _kit(api, name="Plank", quantity=2, **over):
    res = api.post("/session-planner/kit", json={"name": name, "quantity": quantity, **over},
                   headers=api.as_("staff"))
    assert res.status_code == 201, res.text
    return res.json()


def _task_body(**over):
    return {"kind": "leadership", "number": "1", "name": "Bridge", "duration_minutes": 15,
            "min_group": 4, "max_group": 6, "kit": [], **over}


def _task(api, **over):
    res = api.post("/session-planner/tasks", json=_task_body(**over), headers=api.as_("staff"))
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture
def cadets(db):
    db.add_all([
        Cadet(cin=1, first_name="Amy", last_name="Able", rank="Cpl", flight="A"),
        Cadet(cin=2, first_name="Zoë", last_name="Ørsted", rank="Cdt", flight="B"),
    ])
    db.add(User(id=900, google_id="old", email="old@x", first_name="Old", last_name="Assessor"))
    db.commit()


def _sheet(db, cin, passed=True, **fields):
    sheet = AssessmentSheet(
        assessment_type="Blue Leadership", fields={"passed": passed, **fields}, created_at=datetime(2026, 3, 1),
        cadet_id=cin, assessor_id=900,
    )
    db.add(sheet)
    db.commit()
    return sheet.id


# ── access ────────────────────────────────────────────────────────────────────

STAFF_ONLY = [
    ("get", "/session-planner/kit", None),
    ("post", "/session-planner/kit", {"name": "Plank", "quantity": 1}),
    ("put", "/session-planner/kit/1", {"name": "Plank", "quantity": 1}),
    ("delete", "/session-planner/kit/1", None),
    ("get", "/session-planner/tasks", None),
    ("post", "/session-planner/tasks", _task_body()),
    ("put", "/session-planner/tasks/1", _task_body()),
    ("delete", "/session-planner/tasks/1", None),
    ("get", "/session-planner/unlinked-assessments", None),
    ("post", "/session-planner/assessments/1/link", {"task_id": 1}),
]


@pytest.mark.parametrize("method,path,body", STAFF_ONLY)
def test_the_bank_needs_a_sign_in(api, method, path, body):
    assert getattr(api, method)(path, **({"json": body} if body else {})).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method,path,body", STAFF_ONLY)
def test_the_bank_is_staff_only(api, persona, method, path, body):
    kwargs = {"headers": api.as_(persona), **({"json": body} if body else {})}
    assert getattr(api, method)(path, **kwargs).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_role_can_use_the_bank(api, persona):
    assert api.get("/session-planner/tasks", headers=api.as_(persona)).status_code == 200
    assert api.get("/session-planner/kit", headers=api.as_(persona)).status_code == 200


def test_the_task_picker_needs_a_sign_in(api):
    assert api.get("/assessments/leadership/tasks").status_code == 401


@pytest.mark.parametrize("persona", ["cadet"])
def test_cadets_cant_see_the_task_picker(api, persona):
    assert api.get("/assessments/leadership/tasks", headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "nco", "snco"])
def test_anyone_who_writes_leadership_sheets_sees_the_picker(api, persona):
    # SNCOs pass require_staff_or_nco as NCOs do; they assess leadership too.
    assert api.get("/assessments/leadership/tasks", headers=api.as_(persona)).status_code == 200


# ── kit ───────────────────────────────────────────────────────────────────────

def test_kit_is_listed_by_name_with_what_needs_it(api):
    _kit(api, "Rope", 3)
    plank = _kit(api, "plank", 2)
    _task(api, kit=[{"kit_id": plank["id"], "quantity": 2}])
    kit = api.get("/session-planner/kit", headers=api.as_("staff")).json()
    assert [k["name"] for k in kit] == ["plank", "Rope"]
    assert kit[0]["used_by"] == [{"task_id": 1, "name": "Bridge", "quantity": 2}]
    assert kit[1]["used_by"] == []


def test_kit_names_are_tidied_and_unique_ignoring_case(api):
    assert _kit(api, "  Cargo   net ")["name"] == "Cargo net"
    res = api.post("/session-planner/kit", json={"name": "cargo NET", "quantity": 1}, headers=api.as_("staff"))
    assert res.status_code == 409


def test_renaming_kit_to_its_own_name_is_fine_but_not_to_anothers(api):
    a, _ = _kit(api, "Plank"), _kit(api, "Rope")
    staff = api.as_("staff")
    assert api.put(f"/session-planner/kit/{a['id']}", json={"name": "plank", "quantity": 5},
                   headers=staff).json()["quantity"] == 5
    assert api.put(f"/session-planner/kit/{a['id']}", json={"name": "Rope", "quantity": 5},
                   headers=staff).status_code == 409


@pytest.mark.parametrize("body", [
    {"name": "", "quantity": 1},
    {"name": "   ", "quantity": 1},
    {"name": "Plank", "quantity": -1},
    {"name": "Plank", "quantity": 1000},
    {"name": "x" * 121, "quantity": 1},
    {"quantity": 1},
])
def test_bad_kit_is_refused(api, body):
    assert api.post("/session-planner/kit", json=body, headers=api.as_("staff")).status_code == 422


def test_kit_can_be_zero_when_its_all_broken(api):
    assert _kit(api, "Radio", 0)["quantity"] == 0


def test_missing_kit_is_404(api):
    staff = api.as_("staff")
    assert api.put("/session-planner/kit/9", json={"name": "a", "quantity": 1}, headers=staff).status_code == 404
    assert api.delete("/session-planner/kit/9", headers=staff).status_code == 404


def test_kit_a_task_still_needs_cant_be_deleted(api, db):
    plank = _kit(api)
    _task(api, kit=[{"kit_id": plank["id"], "quantity": 1}])
    res = api.delete(f"/session-planner/kit/{plank['id']}", headers=api.as_("staff"))
    assert res.status_code == 409 and "Bridge" in res.json()["detail"]
    assert db.get(SessionKit, plank["id"]) is not None


def test_unused_kit_can_be_deleted(api, db):
    plank = _kit(api)
    assert api.delete(f"/session-planner/kit/{plank['id']}", headers=api.as_("staff")).status_code == 204
    assert db.query(SessionKit).count() == 0


# ── tasks ─────────────────────────────────────────────────────────────────────

def test_a_task_saves_with_its_kit(api):
    plank, rope = _kit(api, "Plank", 2), _kit(api, "Rope", 1)
    t = _task(api, brief="Cross the river", kit=[{"kit_id": plank["id"], "quantity": 2},
                                                {"kit_id": rope["id"], "quantity": 1}])
    assert t["name"] == "Bridge" and t["brief"] == "Cross the river" and t["active"] is True
    assert {k["name"]: k["quantity"] for k in t["kit"]} == {"Plank": 2, "Rope": 1}
    assert t["kit_short"] == [] and t["led_count"] == 0


def test_a_task_needing_more_kit_than_there_is_saves_but_says_so(api):
    # A radio breaks and the stock drops: editing the task must still work.
    radio = _kit(api, "Radio", 1)
    t = _task(api, kit=[{"kit_id": radio["id"], "quantity": 3}])
    assert t["kit_short"] == ["Radio"]


def test_reducing_kit_flags_the_tasks_that_can_no_longer_run(api):
    plank = _kit(api, "Plank", 2)
    t = _task(api, kit=[{"kit_id": plank["id"], "quantity": 2}])
    api.put(f"/session-planner/kit/{plank['id']}", json={"name": "Plank", "quantity": 1}, headers=api.as_("staff"))
    [listed] = api.get("/session-planner/tasks", headers=api.as_("staff")).json()
    assert listed["id"] == t["id"] and listed["kit_short"] == ["Plank"]


@pytest.mark.parametrize("over", [
    {"kind": "knitting"},
    {"name": ""},
    {"name": "   "},
    {"duration_minutes": 0},
    {"duration_minutes": 241},
    {"min_group": 0},
    {"max_group": 51},
    {"min_group": 7, "max_group": 3},
    {"number": "x" * 21},
    {"kit": [{"kit_id": 1, "quantity": 1}, {"kit_id": 1, "quantity": 2}]},
    {"kit": [{"kit_id": 1, "quantity": 0}]},
])
def test_bad_tasks_are_refused(api, over):
    _kit(api)
    res = api.post("/session-planner/tasks", json=_task_body(**over), headers=api.as_("staff"))
    assert res.status_code == 422, res.text


def test_kit_that_isnt_on_the_list_is_refused(api):
    res = api.post("/session-planner/tasks", json=_task_body(kit=[{"kit_id": 42, "quantity": 1}]),
                   headers=api.as_("staff"))
    assert res.status_code == 400


@pytest.mark.parametrize("over", [
    {"number": "1", "name": "Other"},       # same number
    {"number": "01", "name": "Other"},      # same number written differently
    {"number": "2", "name": "bridge"},      # same name
])
def test_numbers_and_names_are_unique_within_a_kind(api, over):
    # Old sheets are matched on them, so two of either would be ambiguous.
    _task(api)
    res = api.post("/session-planner/tasks", json=_task_body(**over), headers=api.as_("staff"))
    assert res.status_code == 409


def test_a_retired_task_still_holds_its_number(api):
    _task(api, active=False)
    assert api.post("/session-planner/tasks", json=_task_body(name="Other"),
                    headers=api.as_("staff")).status_code == 409


def test_the_same_number_can_be_used_in_another_kind(api):
    _task(api)
    assert _task(api, kind="radio", name="Bridge")["kind"] == "radio"


def test_tasks_list_in_number_order_and_hide_retired_unless_asked(api):
    _task(api, number="10", name="Ten")
    _task(api, number="2", name="Two")
    _task(api, number=None, name="Alpha")
    _task(api, number="3", name="Old", active=False)
    _task(api, kind="radio", number="1", name="Net")
    staff = api.as_("staff")
    names = [t["name"] for t in api.get("/session-planner/tasks?kind=leadership", headers=staff).json()]
    assert names == ["Two", "Ten", "Alpha"]
    everything = api.get("/session-planner/tasks?include_retired=true", headers=staff).json()
    assert {t["name"] for t in everything} == {"Two", "Ten", "Alpha", "Old", "Net"}


def test_an_unknown_kind_filter_is_400(api):
    assert api.get("/session-planner/tasks?kind=knitting", headers=api.as_("staff")).status_code == 400


def test_an_empty_bank_lists_nothing(api):
    assert api.get("/session-planner/tasks", headers=api.as_("staff")).json() == []


def test_editing_a_task_replaces_its_kit(api, db):
    plank, rope = _kit(api, "Plank"), _kit(api, "Rope")
    t = _task(api, kit=[{"kit_id": plank["id"], "quantity": 1}])
    res = api.put(f"/session-planner/tasks/{t['id']}", json=_task_body(
        name="Bridge II", kit=[{"kit_id": rope["id"], "quantity": 2}]), headers=api.as_("staff"))
    assert res.status_code == 200
    assert res.json()["name"] == "Bridge II"
    assert [(k["name"], k["quantity"]) for k in res.json()["kit"]] == [("Rope", 2)]


def test_editing_a_missing_task_is_404(api):
    staff = api.as_("staff")
    assert api.put("/session-planner/tasks/9", json=_task_body(), headers=staff).status_code == 404
    assert api.delete("/session-planner/tasks/9", headers=staff).status_code == 404


def test_a_task_nobody_has_led_can_be_deleted(api, db):
    t = _task(api)
    assert api.delete(f"/session-planner/tasks/{t['id']}", headers=api.as_("staff")).status_code == 204
    assert db.query(SessionTask).count() == 0


def test_a_task_cadets_have_led_must_be_retired_not_deleted(api, db, cadets):
    t = _task(api)
    _sheet(db, 1, exercise_no="1")  # an old free-text sheet that matches it
    res = api.delete(f"/session-planner/tasks/{t['id']}", headers=api.as_("staff"))
    assert res.status_code == 409 and "Retire" in res.json()["detail"]
    retire = api.put(f"/session-planner/tasks/{t['id']}", json=_task_body(active=False), headers=api.as_("staff"))
    assert retire.status_code == 200 and retire.json()["active"] is False


def test_a_led_task_cant_stop_being_a_leadership_task(api, db, cadets):
    t = _task(api)
    _sheet(db, 1, task_id=t["id"])
    res = api.put(f"/session-planner/tasks/{t['id']}", json=_task_body(kind="radio"), headers=api.as_("staff"))
    assert res.status_code == 409


def test_a_task_counts_its_leads_old_and_new(api, db, cadets):
    t = _task(api)
    _sheet(db, 1, passed=False, exercise_no="1", exercise_name="Bridge")  # matched on text
    _sheet(db, 1, passed=True, task_id=t["id"])                          # picked from the bank
    _sheet(db, 2, passed=True, exercise_name="bridge")
    [listed] = api.get("/session-planner/tasks", headers=api.as_("staff")).json()
    assert (listed["led_count"], listed["passed_count"], listed["cadets_led"]) == (3, 2, 2)


# ── linking old sheets ────────────────────────────────────────────────────────

def test_sheets_the_bank_cant_place_are_listed_newest_first(api, db, cadets):
    _task(api)
    _sheet(db, 1, exercise_no="1")                                       # matches
    swamp = _sheet(db, 2, exercise_no="", exercise_name="Swamp", date_iso="2026-04-01")
    rows = api.get("/session-planner/unlinked-assessments", headers=api.as_("staff")).json()
    assert [r["assessment_id"] for r in rows] == [swamp]
    assert rows[0]["first_name"] == "Zoë" and rows[0]["exercise_name"] == "Swamp" and rows[0]["date"] == "2026-04-01"


@pytest.mark.parametrize("held", ["Blue Leadership", "Gold Leadership"])
def test_a_cadet_who_holds_the_leadership_badge_has_nothing_to_link(api, db, cadets, held):
    # They've passed the whole assessment, so what each sheet was for no longer
    # matters. Another cadet's unplaced sheet is still listed.
    _task(api)
    _sheet(db, 1, exercise_name="Swamp")
    _sheet(db, 1, exercise_name="Barrel")
    other = _sheet(db, 2, exercise_name="Swamp")
    db.add(CadetQualification(cadet_id=1, qual_type=held, status="true"))
    db.commit()
    rows = api.get("/session-planner/unlinked-assessments", headers=api.as_("staff")).json()
    assert [r["assessment_id"] for r in rows] == [other]


def test_other_qualifications_dont_hide_a_cadets_unlinked_sheets(api, db, cadets):
    _task(api)
    sid = _sheet(db, 1, exercise_name="Swamp")
    db.add(CadetQualification(cadet_id=1, qual_type="first_aid", status="true"))
    db.commit()
    rows = api.get("/session-planner/unlinked-assessments", headers=api.as_("staff")).json()
    assert [r["assessment_id"] for r in rows] == [sid]


def test_no_leadership_sheets_means_nothing_unlinked(api):
    assert api.get("/session-planner/unlinked-assessments", headers=api.as_("staff")).json() == []


def test_linking_a_sheet_by_hand_places_it_for_good(api, db, cadets):
    t = _task(api, number=None, name="Toxic barrel")
    sid = _sheet(db, 2, exercise_name="The barrel one", scores={"1": 3})
    res = api.post(f"/session-planner/assessments/{sid}/link", json={"task_id": t["id"]}, headers=api.as_("staff"))
    assert res.status_code == 200
    db.expire_all()
    fields = db.get(AssessmentSheet, sid).fields
    assert fields["task_id"] == t["id"] and fields["scores"] == {"1": 3}  # the rest untouched
    assert api.get("/session-planner/unlinked-assessments", headers=api.as_("staff")).json() == []


def test_linking_needs_a_real_leadership_sheet_and_task(api, db, cadets):
    staff = api.as_("staff")
    leadership = _task(api)
    radio = _task(api, kind="radio", name="Net")
    sid = _sheet(db, 1, exercise_name="Swamp")
    db.add(AssessmentSheet(assessment_type="Blue Radio", fields={}, created_at=datetime(2026, 3, 1),
                           cadet_id=1, assessor_id=900))
    db.commit()
    radio_sheet = db.query(AssessmentSheet).filter_by(assessment_type="Blue Radio").one().id
    assert api.post("/session-planner/assessments/999/link", json={"task_id": leadership["id"]},
                    headers=staff).status_code == 404
    assert api.post(f"/session-planner/assessments/{radio_sheet}/link", json={"task_id": leadership["id"]},
                    headers=staff).status_code == 404
    assert api.post(f"/session-planner/assessments/{sid}/link", json={"task_id": 999},
                    headers=staff).status_code == 404
    assert api.post(f"/session-planner/assessments/{sid}/link", json={"task_id": radio["id"]},
                    headers=staff).status_code == 400
    assert api.post(f"/session-planner/assessments/{sid}/link", json={}, headers=staff).status_code == 422


# ── the assessment form's picker ──────────────────────────────────────────────

def test_the_picker_lists_active_leadership_tasks_only(api):
    _task(api, number="2", name="Two")
    _task(api, number="1", name="One")
    _task(api, number="3", name="Retired", active=False)
    _task(api, kind="radio", name="Net")
    tasks = api.get("/assessments/leadership/tasks", headers=api.as_("nco")).json()
    assert [t["name"] for t in tasks] == ["One", "Two"]
    assert all(t["led"] is None for t in tasks)


def test_the_picker_marks_what_this_cadet_has_already_led(api, db, cadets):
    bridge = _task(api, number="1", name="Bridge")
    web = _task(api, number="2", name="Web")
    _task(api, number="3", name="Swamp")
    _sheet(db, 1, passed=False, task_id=bridge["id"])
    _sheet(db, 1, passed=True, task_id=bridge["id"])  # failed then passed: passed
    _sheet(db, 1, passed=False, exercise_no="2")
    _sheet(db, 2, passed=True, task_id=web["id"])     # someone else's
    led = {t["name"]: t["led"] for t in api.get("/assessments/leadership/tasks?cin=1",
                                                  headers=api.as_("nco")).json()}
    assert led == {"Bridge": "passed", "Web": "failed", "Swamp": None}


def test_the_picker_for_a_cadet_with_no_sheets_marks_nothing(api, cadets):
    _task(api)
    assert [t["led"] for t in api.get("/assessments/leadership/tasks?cin=2", headers=api.as_("nco")).json()] == [None]


def test_the_sheet_being_edited_doesnt_count_as_having_led_its_own_task(api, db, cadets):
    bridge = _task(api)
    editing = _sheet(db, 1, passed=True, task_id=bridge["id"])
    url = f"/assessments/leadership/tasks?cin=1&except_assessment={editing}"
    assert [t["led"] for t in api.get(url, headers=api.as_("nco")).json()] == [None]
    # Another sheet for the same task still counts.
    _sheet(db, 1, passed=False, task_id=bridge["id"])
    assert [t["led"] for t in api.get(url, headers=api.as_("nco")).json()] == ["failed"]


# ── planning a session ────────────────────────────────────────────────────────

def _plan_body(**over):
    return {"kind": "leadership", "date": "2026-11-09", "start": "19:00", "end": "20:00",
            "changeover_minutes": 5, "stands": 2, "task_ids": [1], "cins": [1, 2], **over}


@pytest.mark.parametrize("method,path,body", [
    ("get", "/session-planner/cadets?date=2026-11-09", None),
    ("post", "/session-planner/plan", _plan_body()),
])
def test_planning_needs_a_sign_in_and_is_staff_only(api, method, path, body):
    kw = {"json": body} if body else {}
    assert getattr(api, method)(path, **kw).status_code == 401
    for persona in ("snco", "nco", "cadet"):
        assert getattr(api, method)(path, headers=api.as_(persona), **kw).status_code == 403


def test_the_planner_lists_cadets_with_their_outlook_and_leadership(api, db, cadets):
    from database.models import CadetAbsence, CadetQualification
    t = _task(api)
    _sheet(db, 1, passed=True, task_id=t["id"])
    _sheet(db, 1, passed=False, exercise_name="Swamp")
    db.add(CadetQualification(cadet_id=2, qual_type="Blue Leadership", status="true"))
    db.add(CadetAbsence(cadet_id=2, date_from=datetime(2026, 11, 1), date_to=datetime(2026, 11, 20),
                        scraped_at=datetime(2026, 11, 1)))
    db.commit()
    rows = {r["cin"]: r for r in api.get("/session-planner/cadets?date=2026-11-09", headers=api.as_("staff")).json()}
    # Sheets the bank can't place don't count as leads.
    assert rows[1]["leadership"] == {"level": None, "led": 1, "passes": 1}
    assert rows[2]["leadership"]["level"] == "blue"
    assert any(f["code"] == "absence_booked" for f in rows[2]["attendance"]["flags"])
    assert rows[2]["first_name"] == "Zoë"


def test_the_planner_ticks_who_has_checked_in_that_night_and_no_other(api, db, cadets):
    from datetime import date

    from database.models import CadetAttendance, CadetCheckIn
    db.add(CadetCheckIn(cadet_id=1, date=date(2026, 11, 9), checked_in_at=datetime(2026, 11, 9, 19, 2)))
    # Checked in the week before, and on the scraped register that night: neither
    # is a check-in for the night being planned.
    db.add(CadetCheckIn(cadet_id=2, date=date(2026, 11, 2), checked_in_at=datetime(2026, 11, 2, 19, 0)))
    db.add(CadetAttendance(cadet_id=2, date=datetime(2026, 11, 9), register_type="Parade Night",
                           status="Present Correctly Dressed"))
    db.commit()
    rows = {r["cin"]: r for r in api.get("/session-planner/cadets?date=2026-11-09", headers=api.as_("staff")).json()}
    assert rows[1]["checked_in"] is True
    assert rows[2]["checked_in"] is False


def test_the_planner_needs_a_date(api):
    assert api.get("/session-planner/cadets", headers=api.as_("staff")).status_code == 422


def test_the_planner_with_nobody_on_the_roster_lists_nobody(api):
    assert api.get("/session-planner/cadets?date=2026-11-09", headers=api.as_("staff")).json() == []


def _squad(db, n=8):
    db.add_all([Cadet(cin=100 + i, first_name=f"C{i}", last_name=f"Surname{i:02d}", rank="Cadet",
                      flight="AB"[i % 2]) for i in range(n)])
    db.commit()
    return [100 + i for i in range(n)]


def test_a_plan_gives_groups_rounds_and_leaders_who_havent_led_the_task(api, db, cadets):
    cins = _squad(db)
    tasks = [_task(api, number=str(n), name=f"Task {n}")["id"] for n in (1, 2, 3)]
    _sheet(db, 100, task_id=tasks[0])  # cadet 100 has led task 1 already
    res = api.post("/session-planner/plan", headers=api.as_("staff"),
                   json=_plan_body(task_ids=tasks, cins=cins, stands=2, end="20:30"))
    assert res.status_code == 200, res.text
    plan = res.json()
    assert plan["leadership"] is True
    assert sorted(c["cin"] for g in plan["groups"] for c in g["cadets"]) == cins
    assert [r["start"] for r in plan["rounds"]] == ["19:00", "19:20", "19:40"]
    runs = [run for r in plan["rounds"] for run in r["runs"]]
    assert all(run["leader"] for run in runs)
    assert not any(run["leader"]["cin"] == 100 and run["task_id"] == tasks[0] for run in runs)
    assert runs[0]["task_name"].startswith("Task")
    assert len(plan["not_leading"]) == len(cins) - len(runs)


def test_a_session_that_isnt_leadership_has_no_leaders(api, db):
    cins = _squad(db, 4)
    radio = [_task(api, kind="radio", number=str(n), name=f"Net {n}")["id"] for n in (1, 2)]
    plan = api.post("/session-planner/plan", headers=api.as_("staff"),
                    json=_plan_body(kind="radio", task_ids=radio, cins=cins)).json()
    assert plan["leadership"] is False and plan["not_leading"] == []
    assert all(run["leader"] is None for r in plan["rounds"] for run in r["runs"])


def test_a_plan_respects_the_kit_list(api, db):
    cins = _squad(db, 6)
    net = _kit(api, "Cargo net", 1)
    a = _task(api, number="1", name="Wall", kit=[{"kit_id": net["id"], "quantity": 1}])["id"]
    b = _task(api, number="2", name="Swamp", kit=[{"kit_id": net["id"], "quantity": 1}])["id"]
    plan = api.post("/session-planner/plan", headers=api.as_("staff"),
                    json=_plan_body(task_ids=[a, b], cins=cins, end="21:00")).json()
    for r in plan["rounds"]:
        assert len(r["runs"]) == 1, "both need the one cargo net"


@pytest.mark.parametrize("over", [
    {"kind": "knitting"},
    {"start": "20:00", "end": "19:00"},
    {"start": "19:00", "end": "19:00"},
    {"stands": 0},
    {"changeover_minutes": -1},
    {"task_ids": []},
    {"cins": []},
    {"date": "not a date"},
    {"start": "7pm"},
])
def test_bad_plan_requests_are_refused(api, over):
    assert api.post("/session-planner/plan", headers=api.as_("staff"), json=_plan_body(**over)).status_code == 422


def test_a_plan_with_unknown_tasks_or_cadets_is_404(api, db, cadets):
    t = _task(api)["id"]
    staff = api.as_("staff")
    assert api.post("/session-planner/plan", headers=staff, json=_plan_body(task_ids=[t, 999])).status_code == 404
    assert api.post("/session-planner/plan", headers=staff, json=_plan_body(task_ids=[t], cins=[1, 999])).status_code == 404


def test_a_plan_cant_mix_kinds_or_use_retired_tasks(api, db, cadets):
    lead = _task(api)["id"]
    radio = _task(api, kind="radio", name="Net")["id"]
    old = _task(api, number="9", name="Old", active=False)["id"]
    staff = api.as_("staff")
    assert api.post("/session-planner/plan", headers=staff, json=_plan_body(task_ids=[lead, radio])).status_code == 400
    assert api.post("/session-planner/plan", headers=staff, json=_plan_body(task_ids=[old])).status_code == 400
