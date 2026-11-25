"""HTTP tests for /parade-reports: who can read them, who can manage the list,
and every way an add, remove or build is refused."""

from datetime import date, datetime, timedelta

import pytest

# Imported up front so the autouse outbox fixture swaps this module's send_email.
import core.parade_report  # noqa: F401
from database.models import (
    BaderCredentials,
    Cadet,
    CadetAttendance,
    ParadeReport,
    ParadeReportRecipient,
    ParadeReportSettings,
    User,
)

DOMAIN = "317atc.co.uk"
WED = date(2026, 11, 25)


def _signed_in(db, *personas):
    """User rows for personas who have signed in to SMS at least once."""
    for p in personas:
        db.add(User(google_id=f"sub-{p}", email=f"{p}@{DOMAIN}", first_name=p.title(), last_name="Person"))
    db.commit()


def _list(db, *personas):
    for p in personas:
        db.add(ParadeReportRecipient(email=f"{p}@{DOMAIN}", added_at=datetime.now()))
    db.commit()


def _register(db, night=WED):
    for cin, status in ((1, "Present Correctly Dressed"), (2, "Absent"), (3, "Authorised Absence")):
        if not db.get(Cadet, cin):
            db.add(Cadet(cin=cin, first_name=f"Cadet{cin}", last_name="Test", rank="Cadet", flight="A"))
        db.add(CadetAttendance(cadet_id=cin, date=datetime(night.year, night.month, night.day, 19),
                               register_type="Parade Night", status=status))
    db.commit()


@pytest.fixture
def report(api, db):
    _register(db)
    res = api.post("/parade-reports", json={"date": WED.isoformat()}, headers=api.as_("staff"))
    assert res.status_code == 200
    return res.json()


# ── Reading ────────────────────────────────────────────────────────────────────

def test_no_token_is_401(api):
    assert api.get("/parade-reports").status_code == 401
    assert api.get(f"/parade-reports/{WED}").status_code == 401


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_staff_read_reports_without_being_on_the_list(api, report, persona):
    body = api.get("/parade-reports", headers=api.as_(persona)).json()
    assert body["canManage"] is True
    assert [r["date"] for r in body["reports"]] == [WED.isoformat()]
    assert body["reports"][0]["counts"] == {"present": 1, "authorised": 1, "absent": 1}


@pytest.mark.parametrize("persona", ["snco", "nco"])
def test_a_listed_snco_or_nco_reads_reports_but_cannot_manage_them(api, db, report, persona):
    _list(db, persona)
    body = api.get("/parade-reports", headers=api.as_(persona)).json()
    assert body["canManage"] is False and body["onList"] is True and len(body["reports"]) == 1
    detail = api.get(f"/parade-reports/{WED}", headers=api.as_(persona)).json()
    assert [c["state"] for c in detail["cadets"]] == ["absent", "authorised", "present"]
    assert detail["emailedTo"] is None  # who it went to is staff's business
    assert api.get("/parade-reports/settings", headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["snco", "nco", "nco2"])
def test_an_snco_or_nco_not_on_the_list_is_told_so_and_sees_no_reports(api, db, report, persona):
    _list(db, "nco" if persona != "nco" else "snco")  # someone else is listed
    res = api.get("/parade-reports", headers=api.as_(persona))
    assert res.status_code == 200
    assert res.json() == {"canManage": False, "onList": False, "reports": []}
    assert api.get(f"/parade-reports/{WED}", headers=api.as_(persona)).status_code == 403


def test_a_cadet_is_refused_even_if_someone_put_them_on_the_list(api, db, report):
    _list(db, "cadet")
    assert api.get("/parade-reports", headers=api.as_("cadet")).status_code == 403
    assert api.get(f"/parade-reports/{WED}", headers=api.as_("cadet")).status_code == 403


def test_taking_someone_off_the_list_takes_their_access_away(api, db, report):
    _list(db, "nco")
    assert len(api.get("/parade-reports", headers=api.as_("nco")).json()["reports"]) == 1
    assert api.delete(f"/parade-reports/recipients/nco@{DOMAIN}", headers=api.as_("staff")).status_code == 200
    assert api.get("/parade-reports", headers=api.as_("nco")).json()["onList"] is False
    assert api.get(f"/parade-reports/{WED}", headers=api.as_("nco")).status_code == 403


def test_a_night_without_a_report_is_404(api):
    assert api.get(f"/parade-reports/{WED}", headers=api.as_("staff")).status_code == 404


def test_a_date_that_isnt_one_is_422(api):
    assert api.get("/parade-reports/next-wednesday", headers=api.as_("staff")).status_code == 422


def test_no_reports_yet_is_an_empty_list(api):
    assert api.get("/parade-reports", headers=api.as_("staff")).json()["reports"] == []


def test_reports_are_newest_first(api, db):
    _register(db, WED - timedelta(days=5))
    _register(db)
    for night in (WED - timedelta(days=5), WED):
        api.post("/parade-reports", json={"date": night.isoformat()}, headers=api.as_("staff"))
    dates = [r["date"] for r in api.get("/parade-reports", headers=api.as_("staff")).json()["reports"]]
    assert dates == [WED.isoformat(), (WED - timedelta(days=5)).isoformat()]


# ── Building by hand ──────────────────────────────────────────────────────────

def test_building_a_night_with_no_register_is_404(api):
    res = api.post("/parade-reports", json={"date": WED.isoformat()}, headers=api.as_("staff"))
    assert res.status_code == 404 and res.json()["detail"] == "No parade night register for that date"


def test_a_future_night_cannot_be_built(api):
    future = (date.today() + timedelta(days=2)).isoformat()
    assert api.post("/parade-reports", json={"date": future}, headers=api.as_("staff")).status_code == 400


def test_building_by_hand_only_emails_when_asked(api, db, outbox):
    _register(db)
    _list(db, "nco")
    api.post("/parade-reports", json={"date": WED.isoformat()}, headers=api.as_("staff"))
    assert outbox == []
    body = api.post("/parade-reports", json={"date": WED.isoformat(), "email": True},
                    headers=api.as_("staff")).json()
    assert [m["to"] for m in outbox] == [f"nco@{DOMAIN}"]
    assert body["emailedTo"] == [f"nco@{DOMAIN}"] and body["generatedBy"] == f"staff@{DOMAIN}"


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_can_build_a_report(api, db, persona):
    _register(db)
    _list(db, persona)
    res = api.post("/parade-reports", json={"date": WED.isoformat()}, headers=api.as_(persona))
    assert res.status_code == 403 and db.query(ParadeReport).count() == 0


def test_building_needs_a_date(api):
    assert api.post("/parade-reports", json={}, headers=api.as_("staff")).status_code == 422


# ── The list ──────────────────────────────────────────────────────────────────

def test_settings_offer_signed_in_ncos_and_staff_but_not_cadets_or_people_already_listed(api, db):
    _signed_in(db, "staff2", "snco", "nco", "nco2", "cadet")
    _list(db, "nco")
    body = api.get("/parade-reports/settings", headers=api.as_("staff")).json()
    assert [r["email"] for r in body["recipients"]] == [f"nco@{DOMAIN}"]
    assert body["recipients"][0] == {"email": f"nco@{DOMAIN}", "name": "Nco Person", "role": "nco"}
    offered = {c["email"] for c in body["candidates"]}
    assert offered == {f"staff2@{DOMAIN}", f"snco@{DOMAIN}", f"nco2@{DOMAIN}"}
    assert body["enabled"] is False and body["runsAs"] is None
    assert body["schedule"] == {"days": ["wed", "fri"], "hour": 21, "minute": 30}


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_see_or_change_the_list(api, db, persona):
    _signed_in(db, "nco2")
    staff_only = [
        api.get("/parade-reports/settings", headers=api.as_(persona)),
        api.put("/parade-reports/settings", json={"enabled": True}, headers=api.as_(persona)),
        api.post("/parade-reports/recipients", json={"email": f"nco2@{DOMAIN}"}, headers=api.as_(persona)),
        api.delete(f"/parade-reports/recipients/nco2@{DOMAIN}", headers=api.as_(persona)),
    ]
    assert [r.status_code for r in staff_only] == [403, 403, 403, 403]
    assert db.query(ParadeReportRecipient).count() == 0


def test_settings_need_a_token(api):
    assert api.get("/parade-reports/settings").status_code == 401


def test_staff_add_an_nco_by_email_in_any_case(api, db):
    _signed_in(db, "nco")
    res = api.post("/parade-reports/recipients", json={"email": f"  NCO@{DOMAIN.upper()} "},
                   headers=api.as_("staff"))
    assert res.status_code == 201
    assert [r["email"] for r in res.json()["recipients"]] == [f"nco@{DOMAIN}"]
    assert db.query(ParadeReportRecipient).one().added_by == f"staff@{DOMAIN}"


@pytest.mark.parametrize("email, status, detail", [
    ("not an email", 400, "Enter an email address"),
    (f"stranger@{DOMAIN}", 404, "No one has signed in to SMS with that email yet"),
    (f"cadet@{DOMAIN}", 400, "Only staff, SNCOs and NCOs can get the parade report"),
    (f"nco@{DOMAIN}", 409, "They're already on the list"),
])
def test_adding_someone_who_cant_be_added_says_why(api, db, email, status, detail):
    _signed_in(db, "nco", "cadet")
    _list(db, "nco")
    res = api.post("/parade-reports/recipients", json={"email": email}, headers=api.as_("staff"))
    assert (res.status_code, res.json()["detail"]) == (status, detail)
    assert db.query(ParadeReportRecipient).count() == 1


def test_adding_needs_an_email(api):
    assert api.post("/parade-reports/recipients", json={}, headers=api.as_("staff")).status_code == 422


def test_removing_someone_not_on_the_list_is_404(api):
    res = api.delete(f"/parade-reports/recipients/nco@{DOMAIN}", headers=api.as_("staff"))
    assert res.status_code == 404


# ── Switching it on ───────────────────────────────────────────────────────────

def test_switching_it_on_without_a_bader_login_says_to_save_one(api, db):
    res = api.put("/parade-reports/settings", json={"enabled": True}, headers=api.as_("staff"))
    assert res.status_code == 400 and "Settings" in res.json()["detail"]
    assert db.query(ParadeReportSettings).count() == 0


def test_switching_it_on_runs_it_with_your_bader_login_and_off_keeps_whose_it_was(api, db):
    _signed_in(db, "staff", "staff2")
    staff = db.query(User).filter(User.email == f"staff@{DOMAIN}").one()
    db.add(BaderCredentials(user_id=staff.id, role_username="u", role_password="p"))
    db.commit()

    on = api.put("/parade-reports/settings", json={"enabled": True}, headers=api.as_("staff")).json()
    assert on["enabled"] is True and on["runsAs"] == f"staff@{DOMAIN}"

    # Someone without a Bader login can still switch it off.
    off = api.put("/parade-reports/settings", json={"enabled": False}, headers=api.as_("staff2")).json()
    assert off["enabled"] is False and off["runsAs"] == f"staff@{DOMAIN}" and off["updatedBy"] == f"staff2@{DOMAIN}"
    assert db.query(ParadeReportSettings).count() == 1
