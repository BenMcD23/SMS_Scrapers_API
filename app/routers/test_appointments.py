"""Staff cadet and uniformed adult staff applications over HTTP: who can get
in, who's listed as nearing 18, starting, ticking and removing applications.
The checklists and date maths are pinned in core/test_appointments.py."""

from datetime import datetime

import pytest

import routers.appointments as appointments
from core.appointments import CFC, SNCO, UNIFORMED, step_keys
from database.models import AppointmentApplication, Cadet

NOW = datetime(2026, 10, 24, 19, 30)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(appointments, "london_now", lambda: NOW)


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Seventeen", last_name="Andahalf", rank="Sgt", flight="A",
              date_of_birth=datetime(2009, 4, 1)),               # 17½ last month
        Cadet(cin=2, first_name="Too", last_name="Young", rank="Cadet", date_of_birth=datetime(2010, 1, 1)),
        Cadet(cin=3, first_name="Already", last_name="Eighteen", rank="CWO",
              date_of_birth=datetime(2008, 9, 1)),               # birthday passed: overdue
        Cadet(cin=4, first_name="Already", last_name="Staff", rank="Staff Cadet",
              date_of_birth=datetime(2007, 1, 1)),
        Cadet(cin=5, first_name="No", last_name="Birthday", rank="Cadet"),
        Cadet(cin=6, first_name="Left", last_name="Sms", rank="Cadet", date_of_birth=datetime(2008, 12, 1),
              missing_since=datetime(2026, 9, 1)),
        Cadet(cin=7, first_name="Siobhán", last_name="Ó Briain", rank="FS",
              date_of_birth=datetime(2008, 11, 3)),               # 18 in ten days
    ])
    db.commit()


def _staff_cadets(api):
    return api.get("/applications/staff-cadets", headers=api.as_("staff")).json()


def _start_adult(api, name="Jo Bloggs", route=CFC):
    res = api.post("/applications/uniformed", headers=api.as_("staff"), json={"name": name, "route": route})
    assert res.status_code == 201, res.text
    return res.json()


def _tick(api, app_id, key, done=True, persona="staff"):
    return api.put(f"/applications/{app_id}/steps/{key}", headers=api.as_(persona), json={"done": done})


# ---- access ----------------------------------------------------------------

@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_can_see_or_change_applications(api, db, squadron, persona):
    db.add(AppointmentApplication(id=9, kind=UNIFORMED, applicant_name="X", route=SNCO, steps={},
                                  started_at=NOW))
    db.commit()
    h = api.as_(persona)
    assert api.get("/applications/staff-cadets", headers=h).status_code == 403
    assert api.get("/applications/uniformed", headers=h).status_code == 403
    assert api.post("/applications/staff-cadets", headers=h, json={"cin": 1}).status_code == 403
    assert api.post("/applications/uniformed", headers=h, json={"name": "A", "route": SNCO}).status_code == 403
    assert api.patch("/applications/9", headers=h, json={"notes": "x"}).status_code == 403
    assert _tick(api, 9, "pers_1_10", persona=persona).status_code == 403
    assert api.delete("/applications/9", headers=h).status_code == 403


def test_no_token_is_401(api):
    assert api.get("/applications/staff-cadets").status_code == 401
    assert api.get("/applications/uniformed").status_code == 401
    assert api.put("/applications/1/steps/bpss", json={"done": True}).status_code == 401


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_member_can_use_it(api, squadron, persona):
    assert api.get("/applications/staff-cadets", headers=api.as_(persona)).status_code == 200
    assert api.post("/applications/staff-cadets", headers=api.as_(persona), json={"cin": 1}).status_code == 201


# ---- staff cadets: listing -------------------------------------------------

def test_nearing_18_lists_cadets_17_and_a_half_or_over_soonest_first(api, squadron):
    body = _staff_cadets(api)
    # 2 too young, 4 already a staff cadet, 5 no date of birth, 6 gone from SMS.
    assert [c["cin"] for c in body["nearing18"]] == [3, 7, 1]
    overdue, soon, _ = body["nearing18"]
    assert overdue["turns18On"] == "2026-09-01" and overdue["daysTo18"] < 0
    assert soon["daysTo18"] == 10 and soon["name"] == "Siobhán Ó Briain"
    assert body["applications"] == []
    assert [s["key"] for s in body["stages"]] == ["dbs_bpss", "application", "appointment"]


def test_an_empty_squadron_lists_nothing(api):
    body = _staff_cadets(api)
    assert body["nearing18"] == [] and body["applications"] == [] and body["stages"]


def test_starting_moves_a_cadet_from_nearing_18_to_in_progress(api, squadron):
    res = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 7})
    assert res.status_code == 201
    app = res.json()
    assert app["cadet"]["cin"] == 7 and app["startedBy"] == "staff@317atc.co.uk"
    assert (app["done"], app["complete"], app["nextStep"]) == (0, False, "bpss")

    body = _staff_cadets(api)
    assert 7 not in [c["cin"] for c in body["nearing18"]]
    assert [a["cadet"]["cin"] for a in body["applications"]] == [7]


def test_a_cadet_can_only_have_one_application(api, squadron):
    h = api.as_("staff")
    assert api.post("/applications/staff-cadets", headers=h, json={"cin": 1}).status_code == 201
    res = api.post("/applications/staff-cadets", headers=h, json={"cin": 1})
    assert res.status_code == 409 and "Seventeen Andahalf" in res.json()["detail"]


def test_a_younger_cadet_can_still_be_started_early(api, squadron):
    assert api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 2}).status_code == 201


@pytest.mark.parametrize("body, status", [({"cin": 999}, 404), ({}, 422), ({"cin": "abc"}, 422),
                                          ({"cin": 1, "extra": 1}, 422)])
def test_starting_for_a_bad_cadet_is_rejected(api, squadron, body, status):
    assert api.post("/applications/staff-cadets", headers=api.as_("staff"), json=body).status_code == status


def test_applications_are_listed_soonest_birthday_first(api, squadron):
    h = api.as_("staff")
    for cin in (1, 3, 7):
        api.post("/applications/staff-cadets", headers=h, json={"cin": cin})
    assert [a["cadet"]["cin"] for a in _staff_cadets(api)["applications"]] == [3, 7, 1]


def test_removing_the_cadet_from_sms_removes_their_application(api, db, squadron):
    api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1})
    db.delete(db.get(Cadet, 1))
    db.commit()
    assert db.query(AppointmentApplication).count() == 0


# ---- ticking steps ---------------------------------------------------------

def test_ticking_records_who_and_when_and_unticking_clears_it(api, db, squadron):
    app_id = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1}).json()["id"]
    res = _tick(api, app_id, "bpss")
    assert res.status_code == 200
    assert res.json()["steps"] == {"bpss": {"at": "2026-10-24T19:30:00", "by": "staff@317atc.co.uk"}}
    assert res.json()["done"] == 1 and res.json()["nextStep"] == "dbs_requested"

    # Stored, not just echoed.
    db.expire_all()
    assert "bpss" in db.get(AppointmentApplication, app_id).steps

    res = _tick(api, app_id, "bpss", done=False)
    assert res.json()["steps"] == {} and res.json()["done"] == 0


def test_a_second_tick_keeps_the_first_ones_time_and_person(api, squadron, monkeypatch):
    app_id = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1}).json()["id"]
    _tick(api, app_id, "bpss")
    monkeypatch.setattr(appointments, "london_now", lambda: datetime(2026, 11, 1))
    res = _tick(api, app_id, "bpss", persona="staff2")
    assert res.json()["steps"]["bpss"] == {"at": "2026-10-24T19:30:00", "by": "staff@317atc.co.uk"}


def test_unticking_a_step_that_was_never_ticked_is_harmless(api, squadron):
    app_id = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1}).json()["id"]
    assert _tick(api, app_id, "moi", done=False).status_code == 200


def test_ticking_every_step_completes_a_staff_cadet_application(api, squadron):
    app = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1}).json()
    for stage in _staff_cadets(api)["stages"]:
        for step in stage["steps"]:
            app = _tick(api, app["id"], step["key"]).json()
    assert app["complete"] is True and app["nextStep"] is None and app["done"] == app["total"] == 15


def test_bad_ticks_are_rejected(api, squadron):
    adult = _start_adult(api, route=SNCO)
    cadet_app = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1}).json()
    assert _tick(api, 999, "bpss").status_code == 404
    assert _tick(api, cadet_app["id"], "nonsense").status_code == 404
    # A step from the other process doesn't exist here.
    assert _tick(api, cadet_app["id"], "pers_1_10").status_code == 404
    # A Region step on the SNCO route exists but doesn't apply.
    assert _tick(api, adult["id"], "region_board").status_code == 409
    assert api.put(f"/applications/{adult['id']}/steps/pers_1_10", headers=api.as_("staff"),
                   json={}).status_code == 422


# ---- uniformed adults ------------------------------------------------------

def test_adding_an_adult_applicant(api):
    app = _start_adult(api, name="  Priya Patel  ", route=SNCO)
    assert (app["name"], app["route"], app["kind"]) == ("Priya Patel", SNCO, UNIFORMED)
    assert app["total"] == len(step_keys(UNIFORMED, SNCO)) and app["cicDueBy"] is None
    body = api.get("/applications/uniformed", headers=api.as_("staff")).json()
    assert [a["name"] for a in body["applications"]] == ["Priya Patel"]
    assert [s["key"] for s in body["stages"]] == ["application", "wing", "region", "appointment", "training"]
    assert body["stages"][2]["routes"] == [CFC]


@pytest.mark.parametrize("body", [{"name": "", "route": SNCO}, {"name": "   ", "route": SNCO},
                                  {"name": "A", "route": "officer"}, {"name": "A"}, {"route": CFC},
                                  {"name": "x" * 201, "route": SNCO}])
def test_an_adult_applicant_needs_a_name_and_a_real_route(api, body):
    assert api.post("/applications/uniformed", headers=api.as_("staff"), json=body).status_code == 422


def test_appointment_on_jpa_starts_the_12_month_cic_clock(api):
    app = _start_adult(api)
    assert _tick(api, app["id"], "jpa").json()["cicDueBy"] == "2027-10-24"


def test_switching_route_keeps_the_region_ticks(api):
    app = _start_adult(api, route=CFC)
    _tick(api, app["id"], "region_board")
    snco = api.patch(f"/applications/{app['id']}", headers=api.as_("staff"), json={"route": SNCO}).json()
    assert snco["route"] == SNCO and snco["done"] == 0 and "region_board" in snco["steps"]
    back = api.patch(f"/applications/{app['id']}", headers=api.as_("staff"), json={"route": CFC}).json()
    assert back["done"] == 1


def test_editing_notes_and_name(api):
    app = _start_adult(api)
    h = api.as_("staff")
    res = api.patch(f"/applications/{app['id']}", headers=h, json={"notes": "Board on 3 Nov", "name": "Jo Smith"})
    assert (res.json()["notes"], res.json()["name"]) == ("Board on 3 Nov", "Jo Smith")
    # Blank notes clear them rather than storing "".
    assert api.patch(f"/applications/{app['id']}", headers=h, json={"notes": "  "}).json()["notes"] is None
    # Sending nothing changes nothing.
    assert api.patch(f"/applications/{app['id']}", headers=h, json={}).json()["name"] == "Jo Smith"


def test_a_staff_cadet_application_has_no_name_or_route(api, squadron):
    app = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1}).json()
    h = api.as_("staff")
    assert api.patch(f"/applications/{app['id']}", headers=h, json={"route": CFC}).status_code == 400
    assert api.patch(f"/applications/{app['id']}", headers=h, json={"notes": "MOI booked"}).status_code == 200
    assert api.patch("/applications/999", headers=h, json={"notes": "x"}).status_code == 404
    assert api.patch(f"/applications/{app['id']}", headers=h, json={"notes": "x" * 5001}).status_code == 422


# ---- removing --------------------------------------------------------------

def test_removing_a_staff_cadet_application_puts_them_back_on_the_list(api, squadron):
    app = api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 7}).json()
    assert api.delete(f"/applications/{app['id']}", headers=api.as_("staff")).status_code == 204
    assert api.delete(f"/applications/{app['id']}", headers=api.as_("staff")).status_code == 404
    assert 7 in [c["cin"] for c in _staff_cadets(api)["nearing18"]]


def test_the_two_lists_are_kept_apart(api, squadron):
    api.post("/applications/staff-cadets", headers=api.as_("staff"), json={"cin": 1})
    _start_adult(api)
    assert len(api.get("/applications/uniformed", headers=api.as_("staff")).json()["applications"]) == 1
    assert len(_staff_cadets(api)["applications"]) == 1
