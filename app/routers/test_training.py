"""The /training endpoints over HTTP: who can reach them, linking programme
activities to lessons (by the model, the rules and staff), the sessions on a
night and how they group whoever is in, and the catch-up lists. The grouping
rules themselves are tested in core/test_training_plan.py."""

import json
from datetime import date, datetime, timedelta

import pytest

import routers.training as training
from database.models import (
    Cadet,
    CadetAttendance,
    CadetCheckIn,
    CadetTheoryLesson,
    ProgrammeLessonLink,
    ProgrammeNight,
    ProgrammeSession,
    TheorySubject,
    TrainingNightSession,
)

NIGHT = date(2026, 11, 2)
NAV = "acp_32_2"  # built in: Basic Navigation using Map and Compass, 3 lessons


@pytest.fixture(autouse=True)
def model(monkeypatch):
    """The model chain: down unless a test gives it a reply. Records prompts."""
    state = {"reply": None, "prompts": []}

    def fake(prompt, system_prompt, **_kw):
        state["prompts"].append(prompt)
        if state["reply"] is None:
            raise RuntimeError("every model in the chain failed")
        return state["reply"], "fake-model"

    monkeypatch.setattr(training, "generate", fake)
    return state


def add_cadets(db, *specs):
    """specs: (cin, flight[, rank])."""
    for spec in specs:
        cin, flight, *rest = spec
        db.add(Cadet(cin=cin, first_name="Sam", last_name=f"Cadet{cin}", flight=flight,
                     rank=rest[0] if rest else "Cadet"))
    db.commit()


def add_night(db, day, *sessions):
    """sessions: (period, flight, activity, category[, split])."""
    night = ProgrammeNight(date=day, imported_at=datetime(2026, 10, 1))
    for period, flight, activity, category, *split in sessions:
        night.sessions.append(ProgrammeSession(period=period, flight=flight, activity=activity, category=category,
                                               instructor="Sgt Davies", split=bool(split and split[0]),
                                               raw=activity))
    db.add(night)
    db.commit()


def had(db, cin, key, part, when=datetime(2026, 10, 1, 20)):
    db.add(CadetTheoryLesson(cadet_id=cin, lesson_key=key, part=part, attended_at=when, recorded_by="QR check-in"))
    db.commit()


def check_in(db, *cins, day=NIGHT):
    for cin in cins:
        db.add(CadetCheckIn(cadet_id=cin, date=day, checked_in_at=datetime.combine(day, datetime.min.time())))
    db.commit()


def link(db, activity, key, part, confirmed=True, new=None):
    db.add(ProgrammeLessonLink(activity=activity, subject_key=key, new_subject=new, part=part, confirmed=confirmed,
                               source="staff", updated_at=datetime(2026, 10, 1)))
    db.commit()


# ─── Who can do what ─────────────────────────────────────────────────────────

ENDPOINTS = [
    ("get", "/training/links", None),
    ("post", "/training/links/suggest?use_ai=false", None),
    ("put", "/training/links", {"activity": "Drill", "subject_key": None, "part": None}),
    ("post", "/training/links/accept", {"activities": ["Drill"]}),
    ("delete", "/training/links?activity=Nope", None),
    ("get", f"/training/nights/{NIGHT}", None),
    ("post", f"/training/nights/{NIGHT}/sessions", {"title": "Drill"}),
    ("put", "/training/sessions/999", {"title": "Drill"}),
    ("delete", "/training/sessions/999", None),
    ("post", f"/training/nights/{NIGHT}/from-programme", None),
    ("get", "/training/catch-ups", None),
]


def call(api, method, path, body, persona=None):
    kw = {"headers": api.as_(persona)} if persona else {}
    if body is not None:
        # TestClient.delete takes no json; nothing here sends a body with one.
        kw["json"] = body
    return getattr(api, method)(path, **kw)


@pytest.mark.parametrize("method, path, body", ENDPOINTS)
def test_no_token_is_401(api, method, path, body):
    assert call(api, method, path, body).status_code == 401


@pytest.mark.parametrize("persona", ["cadet", "nco", "snco"])
@pytest.mark.parametrize("method, path, body", ENDPOINTS)
def test_cadets_and_ncos_are_kept_out(api, persona, method, path, body):
    assert call(api, method, path, body, persona).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
@pytest.mark.parametrize("method, path, body", ENDPOINTS)
def test_staff_get_in(api, db, persona, method, path, body):
    add_night(db, NIGHT, (1, "A", "Drill", "drill"))
    code = call(api, method, path, body, persona).status_code
    # Past the role check: anything but 401/403 (the 404s are the fake ids).
    assert code not in (401, 403), (persona, path, code)


# ─── Programme links ─────────────────────────────────────────────────────────

def test_every_programme_activity_is_listed_once_with_its_nights_and_commonest_category(api, db):
    add_night(db, date(2026, 10, 5), (1, "A", "Map Reading 1", "fieldcraft"), (1, "B", "Map Reading 1", "fieldcraft"))
    add_night(db, date(2026, 10, 12), (1, "C", "Map Reading 1", "lessons"), (2, "A", "Drill", "drill"))
    add_night(db, date(2026, 10, 19), (1, "A", "Map Reading 1", "fieldcraft"))
    rows = api.get("/training/links", headers=api.as_("staff")).json()
    assert [r["activity"] for r in rows] == ["Drill", "Map Reading 1"]
    mr = rows[1]
    assert (mr["nights"], mr["last"], mr["category"], mr["flights"]) == (3, "2026-10-19", "fieldcraft", ["A", "B", "C"])
    assert mr["state"] == "none"


def test_an_empty_programme_lists_nothing(api):
    assert api.get("/training/links", headers=api.as_("staff")).json() == []


def test_with_the_model_down_the_rules_suggest_links(api, db, model):
    add_night(db, NIGHT, (1, "A", "Flying Ops 2", "lessons"), (2, "A", "Drill", "drill"),
              (2, "B", "Blue Radio", "stem"))
    res = api.post("/training/links/suggest", headers=api.as_("staff"))
    assert res.status_code == 200
    assert res.json() == {"suggested": 3, "ai": 0, "rules": 3}
    assert len(model["prompts"]) == 1  # it was asked
    rows = {r["activity"]: r for r in api.get("/training/links", headers=api.as_("staff")).json()}
    assert (rows["Flying Ops 2"]["state"], rows["Flying Ops 2"]["new_subject"], rows["Flying Ops 2"]["part"]) == (
        "suggested", "Flying Ops", 2)
    assert (rows["Blue Radio"]["subject_key"], rows["Blue Radio"]["subject_name"]) == ("blue_radio", "Blue Radio")
    assert rows["Drill"]["state"] == "suggested" and rows["Drill"]["subject_key"] is None
    assert rows["Drill"]["new_subject"] is None


def test_use_ai_false_never_asks_the_model(api, db, model):
    add_night(db, NIGHT, (1, "A", "Drill", "drill"))
    api.post("/training/links/suggest?use_ai=false", headers=api.as_("staff"))
    assert model["prompts"] == []


def test_the_models_good_answers_are_used_and_its_bad_ones_left_to_the_rules(api, db, model):
    add_night(db, NIGHT, (1, "A", "Map reading pt 2", "fieldcraft"), (1, "B", "Flying Ops 1", "lessons"),
              (2, "A", "Rank System", "lessons"), (2, "B", "Drill", "drill"), (2, "C", "Map Reading 9", "fieldcraft"))
    # Indexes follow the alphabetical activity list.
    model["reply"] = "Sure!\n" + json.dumps({"links": [
        {"i": 0, "subject": None, "new_subject": None, "part": None},          # Drill: not a lesson
        {"i": 1, "subject": None, "new_subject": "Flying Operations", "part": 1},
        {"i": 2, "subject": NAV, "part": 9},                                    # Map Reading 9: no lesson 9
        {"i": 3, "subject": NAV, "part": 2},                                    # Map reading pt 2
        {"i": 4, "subject": "made_up_key", "part": 1},                          # Rank System
        {"i": 99, "subject": NAV, "part": 1},                                   # no such activity
    ]})
    res = api.post("/training/links/suggest", headers=api.as_("staff")).json()
    assert res == {"suggested": 5, "ai": 3, "rules": 2}
    rows = {r["activity"]: r for r in api.get("/training/links", headers=api.as_("staff")).json()}
    assert rows["Map reading pt 2"]["subject_key"] == NAV and rows["Map reading pt 2"]["source"] == "ai"
    assert rows["Flying Ops 1"]["new_subject"] == "Flying Operations"
    assert rows["Rank System"]["source"] == "rules" and rows["Rank System"]["new_subject"] == "Rank System"
    assert rows["Map Reading 9"]["source"] == "rules"


@pytest.mark.parametrize("reply", ["no idea", "{\"links\": 5}", "{\"nope\": []}", "{broken"])
def test_an_unreadable_model_reply_falls_back_to_the_rules(api, db, model, reply):
    add_night(db, NIGHT, (1, "A", "Drill", "drill"))
    model["reply"] = reply
    assert api.post("/training/links/suggest", headers=api.as_("staff")).json()["rules"] == 1


def test_suggesting_again_leaves_existing_links_alone(api, db):
    add_night(db, NIGHT, (1, "A", "Map Reading 2", "fieldcraft"), (1, "B", "Drill", "drill"))
    link(db, "Map Reading 2", NAV, 2)
    assert api.post("/training/links/suggest", headers=api.as_("staff")).json()["suggested"] == 1
    assert api.post("/training/links/suggest", headers=api.as_("staff")).json()["suggested"] == 0
    row = db.query(ProgrammeLessonLink).filter_by(activity="Map Reading 2").one()
    assert (row.subject_key, row.part, row.confirmed) == (NAV, 2, True)


def test_staff_link_an_activity_to_a_lesson(api, db):
    add_night(db, NIGHT, (1, "A", "Map Reading 2", "fieldcraft"))
    res = api.put("/training/links", headers=api.as_("staff"),
                  json={"activity": "Map Reading 2", "subject_key": NAV, "part": 2})
    assert res.status_code == 200
    assert res.json()["state"] == "linked" and res.json()["subject_name"] == "Basic Navigation using Map and Compass"
    row = db.query(ProgrammeLessonLink).one()
    assert (row.source, row.updated_by) == ("staff", "staff@317atc.co.uk")


def test_staff_can_mark_an_activity_not_a_lesson_over_a_suggestion(api, db):
    add_night(db, NIGHT, (1, "A", "Map Reading 2", "fieldcraft"))
    link(db, "Map Reading 2", None, 2, confirmed=False, new="Map Reading")
    res = api.put("/training/links", headers=api.as_("staff"),
                  json={"activity": "Map Reading 2", "subject_key": None, "part": None})
    assert res.json()["state"] == "not_lesson"
    assert db.query(ProgrammeLessonLink).one().new_subject is None


@pytest.mark.parametrize("body, code", [
    ({"activity": "Not in the programme", "subject_key": NAV, "part": 1}, 404),
    ({"activity": "Map Reading 2", "subject_key": "no_such_subject", "part": 1}, 404),
    ({"activity": "Map Reading 2", "subject_key": NAV, "part": 4}, 400),
    ({"activity": "Map Reading 2", "subject_key": NAV, "part": None}, 422),
    ({"activity": "Map Reading 2", "subject_key": None, "part": 2}, 422),
    ({"activity": "Map Reading 2", "subject_key": NAV, "part": 0}, 422),
    ({"activity": "", "subject_key": None, "part": None}, 422),
])
def test_a_link_to_nothing_real_is_refused(api, db, body, code):
    add_night(db, NIGHT, (1, "A", "Map Reading 2", "fieldcraft"))
    assert api.put("/training/links", headers=api.as_("staff"), json=body).status_code == code
    assert db.query(ProgrammeLessonLink).count() == 0


def test_forgetting_a_link_lets_it_be_suggested_again(api, db):
    add_night(db, NIGHT, (1, "A", "Drill", "drill"))
    link(db, "Drill", None, None)
    assert api.delete("/training/links?activity=Drill", headers=api.as_("staff")).status_code == 204
    assert api.delete("/training/links?activity=Drill", headers=api.as_("staff")).status_code == 404
    assert api.post("/training/links/suggest", headers=api.as_("staff")).json()["suggested"] == 1


def test_accepting_a_new_subject_adds_it_once_with_qr_codes_for_every_lesson(api, db):
    add_night(db, NIGHT, (1, "A", "Map Reading 1", "fieldcraft"), (1, "B", "Map Reading 3", "fieldcraft"),
              (2, "A", "Map reading 2", "fieldcraft"))
    link(db, "Map Reading 1", None, 1, confirmed=False, new="Map Reading")
    link(db, "Map Reading 3", None, 3, confirmed=False, new="Map Reading")
    link(db, "Map reading 2", None, 2, confirmed=False, new="map reading")
    res = api.post("/training/links/accept", headers=api.as_("staff"),
                   json={"activities": ["Map Reading 1", "Map Reading 3", "Map reading 2"]})
    assert res.json() == {"accepted": 3, "created_subjects": ["Map Reading"], "skipped": []}
    subject = db.query(TheorySubject).one()
    assert (subject.name, subject.parts, subject.category) == ("Map Reading", 3, "Squadron lessons")
    codes = [c for c in api.get("/cadets/theory/qr-codes", headers=api.as_("staff")).json()
             if c["key"] == subject.key]
    assert [c["part"] for c in codes] == [1, 2, 3]
    assert {r.subject_key for r in db.query(ProgrammeLessonLink)} == {subject.key}
    assert all(r.confirmed and r.new_subject is None for r in db.query(ProgrammeLessonLink))


def test_accepting_a_new_subject_that_now_exists_uses_and_grows_it(api, db):
    add_night(db, NIGHT, (1, "A", "Map Reading 2", "fieldcraft"))
    made = api.post("/cadets/theory/subjects", headers=api.as_("staff"),
                    json={"name": "Map Reading", "section": "Badges", "parts": 1}).json()
    link(db, "Map Reading 2", None, 2, confirmed=False, new="Map Reading")
    res = api.post("/training/links/accept", headers=api.as_("staff"), json={"activities": ["Map Reading 2"]})
    assert res.json()["created_subjects"] == [] and res.json()["accepted"] == 1
    assert db.query(TheorySubject).one().parts == 2
    assert db.query(ProgrammeLessonLink).one().subject_key == made["key"]


def test_a_built_in_subject_isnt_grown_to_fit_a_suggestion(api, db):
    add_night(db, NIGHT, (1, "A", "Blue Radio 2", "stem"))
    link(db, "Blue Radio 2", None, 2, confirmed=False, new="Blue Radio")
    res = api.post("/training/links/accept", headers=api.as_("staff"), json={"activities": ["Blue Radio 2"]}).json()
    assert res["accepted"] == 0 and res["skipped"][0]["reason"] == "Blue Radio only has 1 lessons"
    assert db.query(ProgrammeLessonLink).one().confirmed is False


def test_accepting_skips_and_names_what_it_cant_accept(api, db):
    add_night(db, NIGHT, (1, "A", "Old Course 1", "lessons"), (1, "B", "Drill", "drill"))
    retired = api.post("/cadets/theory/subjects", headers=api.as_("staff"),
                       json={"name": "Old Course", "section": "Badges", "parts": 1, "active": False}).json()
    link(db, "Old Course 1", retired["key"], 1, confirmed=False)
    link(db, "Drill", None, None, confirmed=True)
    res = api.post("/training/links/accept", headers=api.as_("staff"),
                   json={"activities": ["Old Course 1", "Drill", "Never suggested"]}).json()
    assert res["accepted"] == 0
    assert {s["activity"] for s in res["skipped"]} == {"Old Course 1", "Drill", "Never suggested"}


def test_accept_needs_at_least_one_activity(api):
    assert api.post("/training/links/accept", headers=api.as_("staff"), json={"activities": []}).status_code == 422


def test_a_link_to_a_retired_subject_shows_as_broken(api, db):
    add_night(db, NIGHT, (1, "A", "Old Course 1", "lessons"))
    retired = api.post("/cadets/theory/subjects", headers=api.as_("staff"),
                       json={"name": "Old Course", "section": "Badges", "parts": 1, "active": False}).json()
    link(db, "Old Course 1", retired["key"], 1)
    [row] = api.get("/training/links", headers=api.as_("staff")).json()
    assert row["broken"] is True and row["subject_name"] is None


# ─── Nights ──────────────────────────────────────────────────────────────────

def add_session(api, body, night=NIGHT):
    res = api.post(f"/training/nights/{night}/sessions", headers=api.as_("staff"), json=body)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_an_empty_night_has_nothing_to_show(api):
    body = api.get(f"/training/nights/{NIGHT}", headers=api.as_("staff")).json()
    assert body == {"date": "2026-11-02", "checked_in": 0, "programme": [], "sessions": []}


def test_a_lesson_session_groups_whoever_is_in_by_need_and_counts_tonights_scans(api, db):
    add_cadets(db, (1, "A"), (2, "A"), (3, "B"), (4, "B"), (5, "C"))
    had(db, 1, NAV, 1)
    had(db, 2, NAV, 1)
    had(db, 2, NAV, 2)                                     # had it before tonight
    had(db, 3, NAV, 1)
    had(db, 3, NAV, 2, when=datetime(2026, 11, 2, 19, 45))  # scanned in tonight
    check_in(db, 1, 2, 3, 4, 5)
    add_session(api, {"title": "Map Reading 2", "subject_key": NAV, "part": 2, "flights": ["A", "b"],
                      "grouping": "need"})
    night = api.get(f"/training/nights/{NIGHT}", headers=api.as_("staff")).json()
    [s] = night["sessions"]
    assert night["checked_in"] == 5
    assert s["flights"] == ["A", "B"] and s["planning_with"] == "checked_in"
    assert s["lesson"]["name"] == "Basic Navigation using Map and Compass" and s["lesson"]["code"]
    assert s["counts"] == {"cadets": 4, "needs": 1, "behind": 1, "had": 2, "scanned": 1}
    groups = {g["label"]: [c["cin"] for c in g["cadets"]] for g in s["groups"]}
    assert groups == {"Needs this lesson": [1], "Missing an earlier lesson": [4], "Already had it": [2, 3]}
    tonight = next(c for g in s["groups"] for c in g["cadets"] if c["cin"] == 3)
    assert tonight["scanned"] is True and tonight["status"] == "had"


def test_before_the_door_opens_a_night_plans_with_who_is_expected_and_flags_the_unlikely(api, db):
    add_cadets(db, (1, "A"), (2, "A"))
    # Cadet 2 has missed the last six parade nights.
    for w in range(1, 7):
        db.add(CadetAttendance(cadet_id=2, date=datetime.combine(NIGHT - timedelta(weeks=w), datetime.min.time()),
                               status="Absent", register_type="Parade Night"))
        db.add(CadetAttendance(cadet_id=1, date=datetime.combine(NIGHT - timedelta(weeks=w), datetime.min.time()),
                               status="Present", register_type="Parade Night"))
    db.commit()
    add_session(api, {"title": "Drill"})
    [s] = api.get(f"/training/nights/{NIGHT}", headers=api.as_("staff")).json()["sessions"]
    assert s["planning_with"] == "expected"
    unlikely = {c["cin"]: c["unlikely"] for g in s["groups"] for c in g["cadets"]}
    assert unlikely == {1: False, 2: True}


def test_a_need_only_lesson_and_drill_in_one_period_split_a_flight(api, db):
    add_cadets(db, (1, "A"), (2, "A"))
    had(db, 1, NAV, 1)
    had(db, 1, NAV, 2)
    check_in(db, 1, 2)
    add_session(api, {"title": "Drill", "period": 1, "flights": ["A"]})
    add_session(api, {"title": "Map Reading 2", "period": 1, "subject_key": NAV, "part": 2, "flights": ["A"],
                      "include": "need"})
    drill, nav = api.get(f"/training/nights/{NIGHT}", headers=api.as_("staff")).json()["sessions"]
    assert [c["cin"] for g in drill["groups"] for c in g["cadets"]] == [1]
    assert [c["cin"] for g in nav["groups"] for c in g["cadets"]] == [2]


def test_sessions_list_by_period_with_free_standing_ones_last(api):
    add_session(api, {"title": "Anytime"})
    add_session(api, {"title": "Second", "period": 2})
    add_session(api, {"title": "First", "period": 1})
    titles = [s["title"] for s in api.get(f"/training/nights/{NIGHT}", headers=api.as_("staff")).json()["sessions"]]
    assert titles == ["First", "Second", "Anytime"]


def test_a_session_whose_subject_was_retired_still_shows_and_says_so(api, db):
    add_cadets(db, (1, "A"))
    made = api.post("/cadets/theory/subjects", headers=api.as_("staff"),
                    json={"name": "Knots", "section": "Badges", "parts": 2}).json()
    add_session(api, {"title": "Knots", "subject_key": made["key"], "part": 2, "grouping": "need"})
    api.put(f"/cadets/theory/subjects/{made['key']}", headers=api.as_("staff"),
            json={"name": "Knots", "section": "Badges", "parts": 2, "active": False})
    [s] = api.get(f"/training/nights/{NIGHT}", headers=api.as_("staff")).json()["sessions"]
    assert s["lesson"] is None
    assert any("isn't in the catalogue" in w for w in s["warnings"])
    assert [c["cin"] for g in s["groups"] for c in g["cadets"]] == [1]


def test_editing_a_session_changes_its_inputs_and_keeps_its_night(api, db):
    sid = add_session(api, {"title": "Drill"})
    res = api.put(f"/training/sessions/{sid}", headers=api.as_("staff"), json={
        "title": "  Leadership   tasks ", "period": 2, "grouping": "mixed", "group_count": 3, "max_group": 6,
        "pool": "checked_in", "instructor": "  ", "notes": "On the field"})
    assert res.status_code == 200
    row = db.get(TrainingNightSession, sid)
    db.refresh(row)
    assert (row.title, row.period, row.grouping, row.group_count, row.max_group, row.pool) == (
        "Leadership tasks", 2, "mixed", 3, 6, "checked_in")
    assert (row.instructor, row.notes, row.date) == (None, "On the field", NIGHT)


def test_deleting_a_session_removes_it(api, db):
    sid = add_session(api, {"title": "Drill"})
    assert api.delete(f"/training/sessions/{sid}", headers=api.as_("staff")).status_code == 204
    assert db.query(TrainingNightSession).count() == 0


@pytest.mark.parametrize("method", ["put", "delete"])
def test_a_session_that_doesnt_exist_is_404(api, method):
    res = call(api, method, "/training/sessions/12345", {"title": "x"} if method == "put" else None, "staff")
    assert res.status_code == 404


@pytest.mark.parametrize("body", [
    {"title": "   "},
    {"title": "x" * 121},
    {"title": "Drill", "include": "need"},
    {"title": "Drill", "grouping": "need"},
    {"title": "Drill", "subject_key": NAV},
    {"title": "Drill", "part": 1},
    {"title": "Drill", "pool": "everyone"},
    {"title": "Drill", "grouping": "random"},
    {"title": "Drill", "include": "some"},
    {"title": "Drill", "period": 0},
    {"title": "Drill", "group_count": 0},
    {"title": "Drill", "max_group": 1},
    {"title": "Drill", "flights": ["A Flight Plus"]},
])
def test_a_session_with_bad_inputs_is_refused(api, db, body):
    assert api.post(f"/training/nights/{NIGHT}/sessions", headers=api.as_("staff"), json=body).status_code == 422
    assert db.query(TrainingNightSession).count() == 0


@pytest.mark.parametrize("body, code", [
    ({"title": "Nav", "subject_key": "no_such", "part": 1}, 404),
    ({"title": "Nav", "subject_key": NAV, "part": 4}, 400),
])
def test_a_session_for_a_lesson_that_doesnt_exist_is_refused(api, body, code):
    assert api.post(f"/training/nights/{NIGHT}/sessions", headers=api.as_("staff"), json=body).status_code == code
    sid = add_session(api, {"title": "Drill"})
    assert api.put(f"/training/sessions/{sid}", headers=api.as_("staff"), json=body).status_code == code


def test_a_night_starts_from_its_programme(api, db):
    add_night(db, NIGHT,
              (1, "A", "Map Reading 2", "fieldcraft", True), (1, "A", "Drill", "drill", True),
              (1, "B", "Map Reading 2", "fieldcraft"), (1, "C", "Intro to Drill", "drill"),
              (2, "A", "Flying Ops 1", "lessons"))
    link(db, "Map Reading 2", NAV, 2)
    link(db, "Flying Ops 1", None, 1, confirmed=False, new="Flying Ops")  # a suggestion isn't a lesson yet
    res = api.post(f"/training/nights/{NIGHT}/from-programme", headers=api.as_("staff"))
    assert res.status_code == 201 and res.json() == {"sessions": 4}
    night = api.get(f"/training/nights/{NIGHT}", headers=api.as_("staff")).json()
    by_title = {s["title"]: s for s in night["sessions"]}
    nav = by_title["Map Reading 2"]
    assert (nav["flights"], nav["subject_key"], nav["part"], nav["grouping"], nav["include"]) == (
        ["A", "B"], NAV, 2, "need", "need")  # A was split, so it takes only who needs it
    assert (by_title["Drill"]["grouping"], by_title["Drill"]["subject_key"]) == ("one", None)
    assert by_title["Flying Ops 1"]["subject_key"] is None
    assert by_title["Intro to Drill"]["instructor"] == "Sgt Davies"
    assert len(night["programme"]) == 5 and night["programme"][0]["link"]["state"] in ("linked", "none")


def test_starting_a_night_twice_or_without_a_programme_is_refused(api, db):
    assert api.post(f"/training/nights/{NIGHT}/from-programme", headers=api.as_("staff")).status_code == 404
    add_night(db, NIGHT, (1, "A", "Drill", "drill"))
    assert api.post(f"/training/nights/{NIGHT}/from-programme", headers=api.as_("staff")).status_code == 201
    assert api.post(f"/training/nights/{NIGHT}/from-programme", headers=api.as_("staff")).status_code == 409
    assert db.query(TrainingNightSession).count() == 1


def test_a_bad_date_is_422(api):
    assert api.get("/training/nights/not-a-date", headers=api.as_("staff")).status_code == 422


# ─── Catch-ups ───────────────────────────────────────────────────────────────

def test_no_linked_lessons_means_no_catch_ups(api, db):
    add_night(db, date.today() - timedelta(days=7), (1, "A", "Map Reading 2", "fieldcraft"))
    link(db, "Map Reading 2", NAV, 2, confirmed=False)
    assert api.get("/training/catch-ups", headers=api.as_("staff")).json() == []


def test_catch_ups_list_who_in_the_flights_it_ran_for_has_no_record(api, db):
    ran = date.today() - timedelta(days=14)
    add_cadets(db, (1, "A"), (2, "A"), (3, "B"), (4, "C"), (5, "a"))
    add_night(db, ran, (1, "A", "Map Reading 2", "fieldcraft"), (1, "B", "Map Reading 2", "fieldcraft", True),
              (1, "C", "Drill", "drill"))
    add_night(db, date.today() + timedelta(days=7), (1, "C", "Map Reading 2", "fieldcraft"))  # not run yet
    link(db, "Map Reading 2", NAV, 2)
    had(db, 2, NAV, 2)
    db.add(CadetAttendance(cadet_id=1, date=datetime.combine(ran, datetime.min.time()), status="Present",
                           register_type="Parade Night"))
    db.commit()
    check_in(db, 5, day=ran)
    [row] = api.get("/training/catch-ups", headers=api.as_("staff")).json()
    assert (row["subject_key"], row["part"], row["parts"]) == (NAV, 2, 3) and row["code"]
    assert row["ran"] == [{"date": ran.isoformat(), "flights": ["A", "B"], "split": True}]
    assert {c["cin"]: c["was_in"] for c in row["missing"]} == {1: True, 3: False, 5: True}


def test_catch_ups_look_back_only_as_far_as_asked(api, db):
    add_cadets(db, (1, "A"))
    add_night(db, date.today() - timedelta(days=60), (1, "A", "Map Reading 2", "fieldcraft"))
    link(db, "Map Reading 2", NAV, 2)
    assert api.get("/training/catch-ups?days=30", headers=api.as_("staff")).json() == []
    assert len(api.get("/training/catch-ups?days=90", headers=api.as_("staff")).json()) == 1
    assert api.get("/training/catch-ups?days=1", headers=api.as_("staff")).status_code == 422
