"""GET /cadets/audit/contacts and its Excel export: staff only, every cadet
still on SMS, and a sheet that says Good or Bad for each check."""

import io
from datetime import datetime

import openpyxl
import pytest

from database.models import AuditEvent, Cadet, CadetContact

URL = "/cadets/audit/contacts"
EXPORT = "/cadets/audit/contacts/export"


def _seed(db):
    read = datetime(2026, 11, 20)
    db.add_all([
        Cadet(cin=1, first_name="Amy", last_name="Able", flight="A", contacts_checked_at=read,
              contact_methods=[{"method": "Mobile Phone", "detail": "07700900001"},
                               {"method": "Primary Email", "detail": "amy@317atc.co.uk"}]),
        CadetContact(cadet_id=1, position=0, name="Sarah Able", relation="Mother", next_of_kin=True,
                     contact_methods=[{"method": "Mobile Phone", "detail": "07700900111"},
                                      {"method": "Primary Email", "detail": "sarah@example.com"}]),
        # Shares mum's phone and has nobody else.
        Cadet(cin=2, first_name="Ben", last_name="Zed", flight="B", contacts_checked_at=read,
              contact_methods=[{"method": "Mobile Phone", "detail": "07700 900222"}]),
        CadetContact(cadet_id=2, position=0, name="Zoë Zed", relation="Mother", next_of_kin=True,
                     contact_methods=[{"method": "Mobile Phone", "detail": "+447700900222"}]),
        Cadet(cin=3, first_name="Cal", last_name="Cole"),  # never scraped
        Cadet(cin=4, first_name="Dee", last_name="Gone", missing_since=read),  # left SMS
    ])
    db.commit()


def test_no_token_is_401(api):
    assert api.get(URL).status_code == 401
    assert api.get(EXPORT).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_may_see_contact_details(api, persona):
    assert api.get(URL, headers=api.as_(persona)).status_code == 403
    assert api.get(EXPORT, headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_each_cadet_on_sms_is_checked_in_surname_order(api, db, persona):
    _seed(db)
    res = api.get(URL, headers=api.as_(persona))
    assert res.status_code == 200
    body = res.json()
    assert [c["key"] for c in body["checks"]] == [
        "cadet_email", "cadet_phone", "email_differs", "phone_differs", "has_contact", "contacts_complete"]
    cadets = {c["cin"]: c for c in body["cadets"]}
    assert [c["cin"] for c in body["cadets"]] == [1, 3, 2]  # Able, Cole, Zed; Gone has left
    assert cadets[1]["result"] == "good"
    assert cadets[2]["result"] == "bad"
    assert cadets[2]["problems"] == ["No email for the cadet", "Cadet phone is the same as Zoë Zed's",
                                     "Zoë Zed has no email"]
    assert cadets[3]["result"] == "not_checked"


def test_reading_and_exporting_parents_details_is_recorded(api, db):
    _seed(db)
    api.get(URL, headers=api.as_("staff"))
    api.get(EXPORT, headers=api.as_("staff"))
    assert [(e.action, e.resource) for e in db.query(AuditEvent).all()] == [
        ("read", "cadet_contacts"), ("export", "cadet_contacts")]


def test_no_cadets_is_an_empty_list_not_an_error(api):
    res = api.get(URL, headers=api.as_("staff"))
    assert res.status_code == 200 and res.json()["cadets"] == []
    assert api.get(EXPORT, headers=api.as_("staff")).status_code == 200


def test_the_export_is_a_sheet_of_good_and_bad_per_cadet(api, db):
    _seed(db)
    res = api.get(EXPORT, headers=api.as_("staff"))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "Contact audit" in res.headers["content-disposition"]
    ws = openpyxl.load_workbook(io.BytesIO(res.content)).active
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[0]
    assert headers[-2:] == ("Result", "Problems")
    by_surname = {r[0]: dict(zip(headers, r, strict=True)) for r in rows[1:]}
    assert set(by_surname) == {"Able", "Zed", "Cole"}
    assert by_surname["Able"]["Result"] == "Good"
    assert by_surname["Able"]["Parent 1 email"] == "sarah@example.com"
    assert by_surname["Zed"]["Cadet phone not a parent's"] == "Bad"
    assert by_surname["Zed"]["Cadet has email"] == "Bad"
    assert by_surname["Cole"]["Result"] == "Not checked"
    # Coloured so the sheet reads at a glance.
    result_col = headers.index("Result") + 1
    able_row = next(i for i, r in enumerate(rows, start=1) if r[0] == "Able")
    zed_row = next(i for i, r in enumerate(rows, start=1) if r[0] == "Zed")
    assert ws.cell(able_row, result_col).fill.fgColor.rgb.endswith("D1FAE5")
    assert ws.cell(zed_row, result_col).fill.fgColor.rgb.endswith("FEE2E2")
