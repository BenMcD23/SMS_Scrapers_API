"""Checking in on the night: the cadet's own QR/NFC check-in, NCOs ticking in
cadets without a phone, and the squadron code staff hand out."""

from datetime import date, datetime

import pytest

from core import recruit_form
from database.models import Cadet, CadetAttendance, CadetCheckIn, CheckInSettings
from routers import check_ins

TONIGHT = date(2026, 11, 9)
CODE = "door-code-123"


@pytest.fixture(autouse=True)
def tonight(monkeypatch):
    """Pin the UK clock: the night must never depend on when the suite runs."""
    monkeypatch.setattr(check_ins, "london_today", lambda: TONIGHT)
    monkeypatch.setattr(check_ins, "london_now", lambda: datetime(2026, 11, 9, 19, 5))


@pytest.fixture
def roster(db):
    db.add_all([
        Cadet(cin=1, first_name="Cara", last_name="Cadet", email="cadet@317atc.co.uk", rank="Cadet", flight="A"),
        Cadet(cin=2, first_name="Zoë", last_name="Ó Briain", rank="Corporal", flight="B"),
        Cadet(cin=3, first_name="Ann", last_name="Archer", flight=None, rank=None),
    ])
    db.add(CheckInSettings(code=CODE, changed_at=datetime(2026, 11, 1)))
    db.commit()


def scan(api, code=CODE, persona="cadet"):
    return api.post(f"/cadets/me/check-in/{code}", headers=api.as_(persona))


def rows(db):
    return sorted((r.cadet_id, r.date, r.checked_in_by) for r in db.query(CadetCheckIn))


# ── the cadet's own check-in ──────────────────────────────────────────────────

def test_scanning_the_code_checks_the_cadet_in_for_tonight(api, db, roster):
    res = scan(api)
    assert res.status_code == 200
    assert res.json() == {"date": "2026-11-09", "checked_in_at": "2026-11-09T19:05:00",
                          "already": False, "first_name": "Cara"}
    assert rows(db) == [(1, TONIGHT, None)]


def test_scanning_twice_is_harmless_and_says_so(api, db, roster):
    scan(api)
    again = scan(api).json()
    assert again["already"] is True
    assert len(rows(db)) == 1


@pytest.mark.parametrize("code", ["nope", CODE.upper(), CODE + "x", "x" * 300])
def test_a_wrong_code_checks_nobody_in(api, db, roster, code):
    res = scan(api, code)
    assert res.status_code == 404 and "isn't the squadron's check-in code" in res.json()["detail"]
    assert rows(db) == []


def test_an_old_code_stops_working_once_staff_make_a_new_one(api, db, roster):
    api.post("/check-ins/code", headers=api.as_("staff"))
    assert scan(api).status_code == 404
    new = api.get("/check-ins/code", headers=api.as_("staff")).json()["code"]
    assert scan(api, new).status_code == 200


def test_before_any_code_exists_a_guess_still_fails(api, db):
    # The first request makes a random code; the guess can't be it.
    db.add(Cadet(cin=1, first_name="Cara", last_name="Cadet", email="cadet@317atc.co.uk"))
    db.commit()
    assert scan(api, "anything").status_code == 404
    assert db.query(CheckInSettings).count() == 1


def test_scanning_needs_a_sign_in(api, roster):
    assert api.post(f"/cadets/me/check-in/{CODE}").status_code == 401


def test_someone_with_no_cadet_record_is_told_so(api, db, roster):
    res = scan(api, persona="staff")
    assert res.status_code == 404 and "not registered" in res.json()["detail"]
    assert rows(db) == []


def test_the_night_is_the_uk_date_not_the_servers(api, db, roster, monkeypatch):
    # A server on UTC reads 30 June at 23:30 when it's already 1 July in the UK.
    monkeypatch.setattr(check_ins, "london_today", recruit_form.london_today)
    monkeypatch.setattr(recruit_form, "datetime", _FrozenDatetime)
    monkeypatch.setattr(check_ins, "london_now", recruit_form.london_now)
    scan(api)
    assert rows(db)[0][1] == date(2026, 7, 1)


class _FrozenDatetime(datetime):
    """23:30 UTC on 30 June is 00:30 BST on 1 July."""

    @classmethod
    def now(cls, tz=None):
        from datetime import timezone
        return datetime(2026, 6, 30, 23, 30, tzinfo=timezone.utc).astimezone(tz)


def test_a_check_in_never_touches_the_scraped_register(api, db, roster):
    db.add(CadetAttendance(cadet_id=1, date=datetime(2026, 11, 9), register_type="Parade Night", status="Absent"))
    db.commit()
    scan(api)
    api.post("/check-ins", json={"cin": 2}, headers=api.as_("nco"))
    api.delete("/check-ins/1", headers=api.as_("nco"))
    assert [(r.cadet_id, r.status) for r in db.query(CadetAttendance)] == [(1, "Absent")]


# ── the door list ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("persona", ["staff", "owner", "oc", "snco", "nco"])
def test_ncos_and_staff_see_the_door_list(api, roster, persona):
    assert api.get("/check-ins", headers=api.as_(persona)).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("get", "/check-ins", None),
    ("post", "/check-ins", {"cin": 1}),
    ("delete", "/check-ins/1", None),
])
def test_the_door_list_needs_a_sign_in_and_isnt_for_cadets(api, roster, method, path, body):
    kw = {"json": body} if body else {}
    assert getattr(api, method)(path, **kw).status_code == 401
    assert getattr(api, method)(path, headers=api.as_("cadet"), **kw).status_code == 403


def test_the_door_list_shows_everyone_and_who_is_in(api, db, roster):
    scan(api)
    api.post("/check-ins", json={"cin": 2}, headers=api.as_("nco"))
    body = api.get("/check-ins", headers=api.as_("nco")).json()
    assert (body["date"], body["today"], body["checked_in"]) == ("2026-11-09", True, 2)
    by_cin = {c["cin"]: c for c in body["cadets"]}
    assert [c["cin"] for c in body["cadets"]] == [3, 1, 2]  # by surname
    assert by_cin[1]["checked_in_by"] is None and by_cin[1]["checked_in"] is True
    assert by_cin[2]["checked_in_by"] == "Nina Corporal"
    assert by_cin[3] == {"cin": 3, "first_name": "Ann", "last_name": "Archer", "rank": None, "flight": None,
                         "checked_in": False, "checked_in_at": None, "checked_in_by": None}
    assert not ({"email", "phone_number", "date_of_birth"} & set(by_cin[1]))


def test_another_nights_list_is_its_own(api, db, roster):
    db.add(CadetCheckIn(cadet_id=1, date=date(2026, 11, 2), checked_in_at=datetime(2026, 11, 2, 19)))
    db.commit()
    tonight = api.get("/check-ins", headers=api.as_("staff")).json()
    last_week = api.get("/check-ins?date=2026-11-02", headers=api.as_("staff")).json()
    assert tonight["checked_in"] == 0
    assert (last_week["checked_in"], last_week["today"]) == (1, False)


def test_an_empty_roster_is_an_empty_list(api):
    assert api.get("/check-ins", headers=api.as_("staff")).json()["cadets"] == []


def test_a_bad_date_is_a_validation_error(api):
    assert api.get("/check-ins?date=monday", headers=api.as_("staff")).status_code == 422


# ── ticking someone in ────────────────────────────────────────────────────────

@pytest.mark.parametrize("persona", ["nco", "snco", "staff"])
def test_an_nco_ticks_in_a_cadet_without_their_phone(api, db, roster, persona):
    res = api.post("/check-ins", json={"cin": 2}, headers=api.as_(persona))
    assert res.status_code == 200
    assert res.json()["checked_in"] is True and res.json()["already"] is False
    assert rows(db)[0][:2] == (2, TONIGHT)


def test_ticking_someone_already_in_keeps_their_own_check_in(api, db, roster):
    scan(api)
    res = api.post("/check-ins", json={"cin": 1}, headers=api.as_("nco")).json()
    assert res["already"] is True
    assert rows(db) == [(1, TONIGHT, None)]


def test_ticking_in_a_cadet_not_on_the_roster_is_404(api, db, roster):
    assert api.post("/check-ins", json={"cin": 999}, headers=api.as_("nco")).status_code == 404
    assert rows(db) == []


@pytest.mark.parametrize("body", [{}, {"cin": "abc"}, {"cin": 1, "date": "soon"}])
def test_a_bad_tick_is_a_validation_error(api, roster, body):
    assert api.post("/check-ins", json=body, headers=api.as_("nco")).status_code == 422


def test_nobody_is_checked_in_to_a_night_that_hasnt_happened(api, db, roster):
    res = api.post("/check-ins", json={"cin": 1, "date": "2026-11-10"}, headers=api.as_("staff"))
    assert res.status_code == 400
    assert rows(db) == []


@pytest.mark.parametrize("persona", ["nco", "snco"])
def test_ncos_only_do_tonights_door(api, db, roster, persona):
    res = api.post("/check-ins", json={"cin": 1, "date": "2026-11-02"}, headers=api.as_(persona))
    assert res.status_code == 403
    assert api.delete("/check-ins/1?date=2026-11-02", headers=api.as_(persona)).status_code == 403
    assert rows(db) == []


def test_staff_can_fill_in_a_night_already_gone(api, db, roster):
    res = api.post("/check-ins", json={"cin": 1, "date": "2026-11-02"}, headers=api.as_("staff"))
    assert res.status_code == 200
    assert rows(db) == [(1, date(2026, 11, 2), "Sam Staff")]


def test_a_tick_naming_tonight_explicitly_is_fine_for_an_nco(api, db, roster):
    res = api.post("/check-ins", json={"cin": 1, "date": "2026-11-09"}, headers=api.as_("nco"))
    assert res.status_code == 200


# ── taking a check-in off ─────────────────────────────────────────────────────

def test_an_nco_takes_off_a_wrong_check_in(api, db, roster):
    scan(api)
    assert api.delete("/check-ins/1", headers=api.as_("nco")).status_code == 204
    assert rows(db) == []


def test_taking_off_someone_not_checked_in_is_404(api, roster):
    assert api.delete("/check-ins/1", headers=api.as_("nco")).status_code == 404


def test_taking_off_tonight_leaves_other_nights(api, db, roster):
    db.add(CadetCheckIn(cadet_id=1, date=date(2026, 11, 2), checked_in_at=datetime(2026, 11, 2, 19)))
    db.commit()
    scan(api)
    api.delete("/check-ins/1", headers=api.as_("nco"))
    assert rows(db) == [(1, date(2026, 11, 2), None)]


def test_a_cadet_leaving_takes_their_check_ins_with_them(api, db, roster):
    scan(api)
    db.delete(db.get(Cadet, 1))
    db.commit()
    assert rows(db) == []


# ── the code ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["get", "post"])
def test_the_code_is_staff_only(api, roster, method):
    assert getattr(api, method)("/check-ins/code").status_code == 401
    for persona in ("snco", "nco", "cadet"):
        assert getattr(api, method)("/check-ins/code", headers=api.as_(persona)).status_code == 403


def test_the_code_is_made_once_and_kept(api, db):
    first = api.get("/check-ins/code", headers=api.as_("staff")).json()
    second = api.get("/check-ins/code", headers=api.as_("staff")).json()
    assert first == second
    assert len(first["code"]) >= 12 and first["changed_by"] is None
    assert db.query(CheckInSettings).count() == 1


def test_a_new_code_is_different_and_says_who_made_it(api, db, roster):
    res = api.post("/check-ins/code", headers=api.as_("staff")).json()
    assert res["code"] != CODE
    assert res["changed_by"] == "staff@317atc.co.uk"
    assert db.query(CheckInSettings).one().code == res["code"]
