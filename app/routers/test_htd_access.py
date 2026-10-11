"""HTD claims: staff can only see and generate their own; the OC, anyone's."""

import pytest

from database.models import Staff


@pytest.fixture
def roster(db):
    db.add_all([
        Staff(cin=10, first_name="Sam", last_name="Staff", email="STAFF@317atc.co.uk",
              address="1 Home Rd", attendance={"2026-01": 4}),
        Staff(cin=11, first_name="Sue", last_name="Second", email="staff2@317atc.co.uk",
              address="2 Other Rd", attendance={"2026-01": 2}),
    ])
    db.commit()


def claim(cin):
    return {"cin": cin, "surname": "Staff", "distance": 10, "months": [{"label": "01/26", "journeys": 4}]}


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_htd_is_staff_only(api, roster, persona):
    assert api.get("/form-generators/htd/staff", headers=api.as_(persona)).status_code == 403
    assert api.post("/form-generators/htd", headers=api.as_(persona), json=claim(10)).status_code == 403


def test_htd_needs_signing_in(api):
    assert api.get("/form-generators/htd/staff").status_code == 401
    assert api.post("/form-generators/htd", json=claim(10)).status_code == 401


def test_staff_only_get_themselves_matched_by_email_case_insensitively(api, roster):
    rows = api.get("/form-generators/htd/staff", headers=api.as_("staff")).json()
    assert [(r["cin"], r["address"], r["attendance"]) for r in rows] == [(10, "1 Home Rd", {"2026-01": 4})]


def test_the_oc_gets_everyone_with_their_details(api, roster):
    rows = api.get("/form-generators/htd/staff", headers=api.as_("oc")).json()
    assert sorted((r["cin"], r["address"]) for r in rows) == [(10, "1 Home Rd"), (11, "2 Other Rd")]


def test_someone_not_on_the_roster_gets_nobody(api, roster):
    assert api.get("/form-generators/htd/staff", headers=api.as_("owner")).json() == []


def test_staff_can_generate_their_own_claim(api, roster):
    res = api.post("/form-generators/htd", headers=api.as_("staff"), json=claim(10))
    assert res.status_code == 200 and "HTD_STAFF.docx" in res.headers["content-disposition"]


@pytest.mark.parametrize("body", [claim(11), claim(None), {}])
def test_staff_cannot_generate_anyone_elses(api, roster, body):
    res = api.post("/form-generators/htd", headers=api.as_("staff"), json=body)
    assert res.status_code == 403 and res.json()["detail"] == "You can only generate your own HTD claim"


def test_staff_off_the_roster_cannot_generate_at_all(api, roster):
    res = api.post("/form-generators/htd", headers=api.as_("owner"), json=claim(10))
    assert res.status_code == 403 and "not on the staff roster" in res.json()["detail"]


@pytest.mark.parametrize("cin", [10, 11, None])
def test_the_oc_can_generate_anyones(api, roster, cin):
    assert api.post("/form-generators/htd", headers=api.as_("oc"), json=claim(cin)).status_code == 200


def test_a_non_numeric_cin_is_a_validation_error(api, roster):
    assert api.post("/form-generators/htd", headers=api.as_("oc"), json=claim("abc")).status_code == 422
