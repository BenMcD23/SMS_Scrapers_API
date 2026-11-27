"""The programme builder over HTTP: who can do what, every rejection, the
Google Docs style comment emails, the PDF and publishing."""

import io
from datetime import date, datetime

import pytest
from pypdf import PdfReader

import routers.programme as website
import routers.programme_builder as router  # binds core.push before the fixtures patch sessions
from core.config import GITHUB_REPO
from database.models import (
    Cadet,
    Event317,
    Location,
    NcoHoliday,
    ProgrammeBankItem,
    ProgrammeNight,
    ProgrammeRoom,
    ProgrammeSession,
    Staff,
    User,
)

BASE = "/programme-builder"
MONTH = "2026-12"


@pytest.fixture
def roster(db):
    db.add_all([
        Staff(cin=3000, first_name="Sam", last_name="Staff", rank="CI", email="staff@317atc.co.uk"),
        Staff(cin=3001, first_name="Sue", last_name="Second", rank="Sgt", email="staff2@317atc.co.uk"),
        Cadet(cin=1, first_name="Nina", last_name="Corporal", rank="Cpl", flight="A", email="nco@317atc.co.uk"),
        Cadet(cin=2, first_name="Ned", last_name="Other", rank="Sgt", flight="B", email="nco2@317atc.co.uk"),
        Cadet(cin=3, first_name="Sid", last_name="Senior", rank="FS", flight="C", email="snco@317atc.co.uk"),
        Cadet(cin=4, first_name="Cara", last_name="Cadet", rank="Cadet", flight="C", email="cadet@317atc.co.uk"),
        *[Cadet(cin=10 + i, first_name=f"Zoë{i}", last_name="Ó Briain", rank="Cadet", flight="ABC"[i % 3])
          for i in range(9)],
    ])
    # The record has Mondays; a new month still gets every Wednesday and Friday.
    for d in (date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)):
        db.add(ProgrammeNight(date=d, imported_at=datetime.now()))
    db.commit()


def staff(api):
    return api.as_("staff")


def new_month(api, month=MONTH, **body):
    res = api.post(f"{BASE}/months", headers=staff(api), json={"month": month, **body})
    assert res.status_code == 201, res.text
    return res.json()


def add(api, night_id, title="Drill", period=1, flights=None, month=MONTH, **kw):
    res = api.post(f"{BASE}/months/{month}/slots", headers=staff(api),
                   json={"night_id": night_id, "title": title, "category": kw.pop("category", "drill"),
                         "period": period, "flights": flights or [], **kw})
    assert res.status_code == 201, res.text
    return res.json()


def slot_named(programme, title):
    return next(s for n in programme["nights"] for s in n["slots"] if s["title"] == title)


def mention(name, key):
    return f"@[{name}]({key})"


# ── access ────────────────────────────────────────────────────────────────────

STAFF_ONLY = [
    ("post", f"{BASE}/months", {"month": MONTH}),
    ("patch", f"{BASE}/months/{MONTH}", {"notes": "x"}),
    ("delete", f"{BASE}/months/{MONTH}", None),
    ("post", f"{BASE}/months/{MONTH}/nights", {"date": "2026-12-03"}),
    ("patch", f"{BASE}/nights/1", {"uniform": "x"}),
    ("delete", f"{BASE}/nights/1", None),
    ("post", f"{BASE}/months/{MONTH}/slots", {"night_id": 1, "title": "x", "category": "drill"}),
    ("patch", f"{BASE}/slots/1", {"title": "x"}),
    ("delete", f"{BASE}/slots/1", None),
    ("post", f"{BASE}/slots/1/copy", {"night_ids": [2]}),
    ("get", f"{BASE}/months/{MONTH}/suggestions", None),
    ("post", f"{BASE}/months/{MONTH}/fill", {}),
    ("post", f"{BASE}/months/{MONTH}/publish", {}),
    ("post", f"{BASE}/bank", {"title": "x", "category": "drill"}),
    ("put", f"{BASE}/bank/1", {"title": "x", "category": "drill"}),
    ("delete", f"{BASE}/bank/1", None),
    ("get", f"{BASE}/bank/history", None),
    ("post", f"{BASE}/bank/import", {"titles": ["x"]}),
    ("post", f"{BASE}/rooms", {"name": "x"}),
    ("put", f"{BASE}/rooms/1", {"name": "x"}),
    ("delete", f"{BASE}/rooms/1", None),
    ("post", f"{BASE}/kinds", {"label": "x"}),
    ("put", f"{BASE}/kinds/drill", {"label": "x"}),
    ("delete", f"{BASE}/kinds/x", None),
]
READ_AND_COMMENT = [
    ("get", f"{BASE}/months", None),
    ("get", f"{BASE}/months/{MONTH}", None),
    ("get", f"{BASE}/months/{MONTH}/pdf", None),
    ("get", f"{BASE}/bank", None),
    ("get", f"{BASE}/rooms", None),
    ("get", f"{BASE}/kinds", None),
    ("get", f"{BASE}/people", None),
    ("post", f"{BASE}/months/{MONTH}/comments", {"body": "x"}),
    ("patch", f"{BASE}/comments/1", {"body": "x"}),
    ("delete", f"{BASE}/comments/1", None),
    ("post", f"{BASE}/comments/1/resolve", None),
    ("post", f"{BASE}/comments/1/reopen", None),
]


def _call(api, method, path, body, headers=None):
    kwargs = {"headers": headers or {}}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)(path, **kwargs)


@pytest.mark.parametrize("method,path,body", STAFF_ONLY + READ_AND_COMMENT)
def test_every_endpoint_needs_a_token(api, method, path, body):
    assert _call(api, method, path, body).status_code == 401


@pytest.mark.parametrize("persona", ["cadet", "cadet2"])
@pytest.mark.parametrize("method,path,body", STAFF_ONLY + READ_AND_COMMENT)
def test_cadets_cant_reach_the_programme_builder(api, method, path, body, persona):
    assert _call(api, method, path, body, api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["nco", "snco"])
@pytest.mark.parametrize("method,path,body", STAFF_ONLY)
def test_ncos_and_sncos_cant_change_it(api, method, path, body, persona):
    assert _call(api, method, path, body, api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "nco", "snco"])
def test_staff_and_ncos_can_read_it_and_only_staff_are_told_they_can_edit(api, roster, persona):
    new_month(api)
    res = api.get(f"{BASE}/months/{MONTH}", headers=api.as_(persona))
    assert res.status_code == 200
    assert res.json()["can_edit"] is (persona in ("staff", "owner"))
    assert api.get(f"{BASE}/months", headers=api.as_(persona)).json()["programmes"][0]["month"] == MONTH


def test_a_month_with_no_programme_is_a_404(api, roster):
    for path in (f"{BASE}/months/2026-12", f"{BASE}/months/2026-12/pdf"):
        res = api.get(path, headers=api.as_("nco"))
        assert res.status_code == 404 and "no programme" in res.json()["detail"]


# ── months ────────────────────────────────────────────────────────────────────

def test_a_new_month_gets_its_parade_nights_and_a_history_line(api, roster):
    programme = new_month(api)
    assert [n["date"] for n in programme["nights"]] == [f"2026-12-{d:02}" for d in (2, 4, 9, 11, 16, 18, 23, 25, 30)]
    assert programme["status"] == "draft" and programme["name"] == "December 2026 programme"
    assert [f["key"] for f in programme["flights"]] == ["C", "A", "B"]
    assert programme["history"][0]["summary"] == "Started the programme"
    assert programme["history"][0]["by"] == "Sam Staff"
    # Four empty nights: one warning each, not one per flight per half.
    assert sum(i["code"] == "empty_night" for i in programme["issues"]) == 9


@pytest.mark.parametrize("body,status", [
    ({"month": "2026-13"}, 422), ({"month": "December"}, 422), ({"month": MONTH, "start": "copy"}, 422),
    ({"month": MONTH, "start": "copy", "copy_from": "2026-01"}, 404), ({"month": MONTH, "start": "record"}, 404),
    ({"month": MONTH, "start": "magic"}, 422),
])
def test_starting_a_month_rejects_what_cant_work(api, roster, body, status):
    assert api.post(f"{BASE}/months", headers=staff(api), json=body).status_code == status


def test_a_month_can_only_be_started_once(api, roster):
    new_month(api)
    res = api.post(f"{BASE}/months", headers=staff(api), json={"month": MONTH})
    assert res.status_code == 409


def test_a_month_starts_from_what_the_record_has_merging_flights(api, db, roster):
    night = ProgrammeNight(date=date(2026, 12, 7), uniform="No.2", duty_nco="Cpl X", imported_at=datetime.now())
    for flight in "CAB":
        night.sessions.append(ProgrammeSession(period=1, flight=flight, activity="Drill", category="drill",
                                               instructor="Sgt H", raw="Drill"))
    night.sessions.append(ProgrammeSession(period=2, flight="A", activity="Band", category="music", raw="Band"))
    db.add(night)
    db.commit()
    assert "2026-12" in api.get(f"{BASE}/months", headers=staff(api)).json()["record_months"]
    programme = new_month(api, start="record")
    [n] = programme["nights"]
    assert n["uniform"] == "No.2" and n["duty_nco"] == "Cpl X"
    drill = slot_named(programme, "Drill")
    assert drill["flights"] == ["A", "B", "C"] and drill["instructor"] == "Sgt H" and drill["period"] == 1
    assert slot_named(programme, "Band")["flights"] == ["A"]
    assert "2026-12" not in api.get(f"{BASE}/months", headers=staff(api)).json()["record_months"]


def test_a_month_can_copy_another_week_by_week(api, roster):
    source = new_month(api, "2026-11")
    add(api, source["nights"][0]["id"], "Drill", month="2026-11", people=["staff:3001"], room_id=None)
    copy = new_month(api, start="copy", copy_from="2026-11")
    first = copy["nights"][0]
    assert [s["title"] for s in first["slots"]] == ["Drill"]
    assert first["slots"][0]["people"][0]["name"] == "Sgt Sue Second"
    assert copy["history"][0]["summary"] == "Started as a copy of November 2026 programme"


def test_deleting_a_month_leaves_the_record_alone(api, db, roster):
    new_month(api)
    assert api.delete(f"{BASE}/months/{MONTH}", headers=staff(api)).json() == {"ok": True}
    assert api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).status_code == 404
    assert db.query(ProgrammeNight).count() == 3


# ── nights ────────────────────────────────────────────────────────────────────

def test_nights_are_added_edited_and_removed_with_their_sessions(api, roster):
    programme = new_month(api)
    res = api.post(f"{BASE}/months/{MONTH}/nights", headers=staff(api), json={"date": "2026-12-03"})
    assert res.status_code == 201
    thursday = next(n for n in res.json()["nights"] if n["date"] == "2026-12-03")
    add(api, thursday["id"], "Drill")
    res = api.patch(f"{BASE}/nights/{thursday['id']}", headers=staff(api),
                    json={"uniform": " MTP ", "duty_nco": "Cpl Ó Briain", "notes": "Parents' evening"})
    night = next(n for n in res.json()["nights"] if n["id"] == thursday["id"])
    assert (night["uniform"], night["duty_nco"], night["notes"]) == ("MTP", "Cpl Ó Briain", "Parents' evening")
    assert res.json()["history"][0]["summary"].startswith("Thu 3 Dec: uniform set to MTP")
    res = api.delete(f"{BASE}/nights/{thursday['id']}", headers=staff(api))
    assert len(res.json()["nights"]) == len(programme["nights"])
    assert res.json()["history"][0]["summary"] == "Removed Thu 3 Dec and its 1 session(s)"


@pytest.mark.parametrize("day,status", [("2027-01-04", 422), ("2026-12-02", 409), (None, 422)])
def test_a_night_must_be_a_new_date_in_the_month(api, roster, day, status):
    new_month(api)
    res = api.post(f"{BASE}/months/{MONTH}/nights", headers=staff(api), json={"date": day})
    assert res.status_code == status


def test_moving_a_night_onto_another_or_out_of_the_month_is_refused(api, roster):
    programme = new_month(api)
    night = programme["nights"][0]["id"]
    for day, status in (("2026-12-04", 409), ("2027-01-01", 422)):
        assert api.patch(f"{BASE}/nights/{night}", headers=staff(api), json={"date": day}).status_code == status
    res = api.patch(f"{BASE}/nights/{night}", headers=staff(api), json={"date": "2026-12-08"})
    assert res.status_code == 200 and res.json()["nights"][1]["date"] == "2026-12-08"
    assert api.patch(f"{BASE}/nights/999", headers=staff(api), json={}).status_code == 404


# ── sessions ──────────────────────────────────────────────────────────────────

def test_a_session_from_the_bank_copies_the_bank_and_can_be_changed(api, roster):
    room = api.post(f"{BASE}/rooms", headers=staff(api), json={"name": "Classroom 1", "capacity": 20}).json()
    item = api.post(f"{BASE}/bank", headers=staff(api), json={
        "title": "Map Reading 1", "category": "lessons", "room_id": room["id"], "instructor": "Sgt H",
        "subject_key": "acp_32_2", "part": 1}).json()
    night = new_month(api)["nights"][0]["id"]
    res = api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api),
                   json={"night_id": night, "bank_item_id": item["id"], "flights": ["A"]})
    assert res.status_code == 201
    slot = slot_named(res.json(), "Map Reading 1")
    assert (slot["category"], slot["room_id"], slot["instructor"], slot["subject_key"], slot["part"], slot["period"]) \
        == ("lessons", room["id"], "Sgt H", "acp_32_2", 1, 1)
    assert res.json()["history"][0]["summary"] == "Added Map Reading 1 for A Flight, 1st half, Wed 2 Dec"


def test_who_usually_runs_a_bank_session_goes_on_it_wherever_its_placed(api, roster):
    # Dragged, placed with the form, or filled from a suggestion, the session
    # gets the bank's people, so the clash and holiday checks see them.
    item = api.post(f"{BASE}/bank", headers=staff(api), json={
        "title": "Drill", "category": "drill", "people": ["staff:3001", "cadet:1", "staff:3001"]})
    assert item.status_code == 201
    assert item.json()["people"] == [{"key": "staff:3001", "name": "Sgt Sue Second"},
                                     {"key": "cadet:1", "name": "Cpl Nina Corporal"}]
    nights = new_month(api)["nights"]
    dragged = api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api),
                       json={"night_id": nights[0]["id"], "bank_item_id": item.json()["id"], "flights": ["A"]}).json()
    assert [p["key"] for p in slot_named(dragged, "Drill")["people"]] == ["staff:3001", "cadet:1"]
    # The form sends who it ended up with, which wins.
    changed = api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api), json={
        "night_id": nights[1]["id"], "bank_item_id": item.json()["id"], "flights": ["B"], "people": []}).json()
    assert [s["people"] for n in changed["nights"] if n["id"] == nights[1]["id"] for s in n["slots"]] == [[]]
    filled = api.post(f"{BASE}/months/{MONTH}/fill", headers=staff(api),
                      json={"cells": [{"night_id": nights[2]["id"], "period": 1, "flight": "C"}]}).json()
    [slot] = [s for n in filled["programme"]["nights"] if n["id"] == nights[2]["id"] for s in n["slots"]]
    assert [p["key"] for p in slot["people"]] == ["staff:3001", "cadet:1"]


def test_a_whole_night_bank_session_goes_in_all_night(api, roster):
    item = api.post(f"{BASE}/bank", headers=staff(api), json={
        "title": "Sports night", "category": "sport", "whole_night": True}).json()
    night = new_month(api)["nights"][0]["id"]
    res = api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api), json={"night_id": night,
                                                                          "bank_item_id": item["id"]})
    assert slot_named(res.json(), "Sports night")["period"] is None


@pytest.mark.parametrize("change,detail", [
    ({"period": 3}, "1st half, the 2nd half or all night"),
    ({"flights": ["Z"]}, "There's no Z Flight"),
    ({"category": "parties"}, "kind of session"),
    ({"people": ["staff:999"]}, "isn't on the roster"),
    ({"room_id": 999}, "room isn't in the list"),
    ({"title": "   "}, "Give the session a name"),
    ({"bank_item_id": 999}, "isn't in the bank"),
    ({"night_id": None}, "which night"),
])
def test_a_session_that_cant_work_is_refused_with_why(api, roster, change, detail):
    night = new_month(api)["nights"][0]["id"]
    body = {"night_id": night, "title": "Drill", "category": "drill", "period": 1, **change}
    res = api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api), json=body)
    assert res.status_code == 422 and detail in res.json()["detail"]


def test_a_session_cant_go_on_another_months_night(api, roster):
    other = new_month(api, "2026-11")["nights"][0]["id"]
    new_month(api)
    res = api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api),
                   json={"night_id": other, "title": "Drill", "category": "drill"})
    assert res.status_code == 422 and "isn't in this programme" in res.json()["detail"]


def test_moving_a_session_changes_only_what_was_sent(api, roster):
    programme = new_month(api)
    first, second = programme["nights"][0]["id"], programme["nights"][1]["id"]
    slot = slot_named(add(api, first, "Drill", flights=["A"], instructor="Sgt H", people=["cadet:1"]), "Drill")
    res = api.patch(f"{BASE}/slots/{slot['id']}", headers=staff(api),
                    json={"night_id": second, "period": 2, "flights": ["A", "B"]})
    moved = slot_named(res.json(), "Drill")
    assert moved["night_id"] == second and moved["period"] == 2 and moved["flights"] == ["A", "B"]
    assert moved["instructor"] == "Sgt H" and [p["key"] for p in moved["people"]] == ["cadet:1"]
    assert res.json()["history"][0]["summary"] == \
        "Moved Drill from A Flight, 1st half, Wed 2 Dec to A and B Flights, 2nd half, Fri 4 Dec"
    res = api.patch(f"{BASE}/slots/{slot['id']}", headers=staff(api), json={"instructor": "", "people": None})
    cleared = slot_named(res.json(), "Drill")
    assert cleared["instructor"] is None and cleared["people"] == []
    assert api.patch(f"{BASE}/slots/999", headers=staff(api), json={}).status_code == 404


def test_a_session_is_copied_to_other_nights_and_removed(api, roster):
    nights = [n["id"] for n in new_month(api)["nights"]]
    slot = slot_named(add(api, nights[0], "Drill", flights=["A"]), "Drill")
    res = api.post(f"{BASE}/slots/{slot['id']}/copy", headers=staff(api),
                   json={"night_ids": [nights[0], nights[1], nights[2], nights[2]]})
    assert [len(n["slots"]) for n in res.json()["nights"]] == [1, 1, 1] + [0] * 6
    assert res.json()["history"][0]["summary"] == "Copied Drill to 2 more nights"
    other = new_month(api, "2026-11")["nights"][0]["id"]
    assert api.post(f"{BASE}/slots/{slot['id']}/copy", headers=staff(api),
                    json={"night_ids": [other]}).status_code == 422
    res = api.delete(f"{BASE}/slots/{slot['id']}", headers=staff(api))
    assert [len(n["slots"]) for n in res.json()["nights"]] == [0, 1, 1] + [0] * 6


def test_the_checks_come_back_with_every_change(api, roster):
    night = new_month(api)["nights"][0]["id"]
    add(api, night, "Drill", flights=["A"], people=["staff:3001"])
    programme = add(api, night, "Band", flights=["B"], people=["staff:3001"])
    clash = next(i for i in programme["issues"] if i["code"] == "person_double_booked")
    assert clash["severity"] == "clash" and "Sgt Sue Second" in clash["message"]


def test_an_nco_on_holiday_shows_as_away_and_as_a_clash(api, db, roster):
    user = User(google_id="g-nco", email="nco@317atc.co.uk")
    db.add(user)
    db.flush()
    db.add(NcoHoliday(user_id=user.id, date_from=datetime(2026, 12, 2), date_to=datetime(2026, 12, 2),
                      booked_by_name="N", booked_by_email="nco@317atc.co.uk", created_at=datetime.now()))
    db.commit()
    night = new_month(api)["nights"][0]["id"]
    programme = add(api, night, "Drill", flights=["A"], people=["cadet:1"])
    assert any(i["code"] == "person_away" for i in programme["issues"])
    people = api.get(f"{BASE}/people?month={MONTH}", headers=staff(api)).json()
    assert next(p for p in people if p["key"] == "cadet:1")["away"] == [["2026-12-02", "2026-12-02"]]


# ── suggestions and filling ───────────────────────────────────────────────────

def test_gaps_get_suggestions_and_fill_puts_each_in_once(api, roster):
    for title, category in (("Drill", "drill"), ("Volleyball", "sport"), ("First Aid", "first_aid")):
        api.post(f"{BASE}/bank", headers=staff(api), json={"title": title, "category": category})
    programme = new_month(api)
    found = api.get(f"{BASE}/months/{MONTH}/suggestions", headers=staff(api)).json()
    assert len(found) == 9 * 2 * 3  # every night, half and flight is a gap
    assert all(f["options"] and f["options"][0]["reason"] for f in found)
    night = programme["nights"][0]
    res = api.post(f"{BASE}/months/{MONTH}/fill", headers=staff(api),
                   json={"cells": [{"night_id": night["id"], "period": p, "flight": "A"} for p in (1, 2)]})
    assert res.json()["filled"] == 2
    titles = [s["title"] for s in res.json()["programme"]["nights"][0]["slots"]]
    assert len(set(titles)) == 2  # not the same thing twice
    res = api.post(f"{BASE}/months/{MONTH}/fill", headers=staff(api), json={})
    # Every gap filled, repeating only once each flight has had all three.
    assert res.json()["filled"] == 9 * 2 * 3 - 2
    filled = res.json()["programme"]
    for flight in "CAB":
        titles = [s["title"] for n in filled["nights"] for s in n["slots"] if flight in s["flights"]]
        assert set(titles) == {"Drill", "Volleyball", "First Aid"}
    assert res.json()["programme"]["history"][0]["summary"].startswith("Filled")


def test_fill_with_an_empty_bank_fills_nothing_and_says_so(api, roster):
    new_month(api)
    res = api.post(f"{BASE}/months/{MONTH}/fill", headers=staff(api), json={})
    assert res.status_code == 200 and res.json()["filled"] == 0


@pytest.mark.parametrize("cells", [[{"night_id": 1}], [{"night_id": "x", "period": 1, "flight": "A"}],
                                   [{"night_id": 999, "period": 1, "flight": "A"}]])
def test_fill_rejects_gaps_it_cant_read(api, roster, cells):
    new_month(api)
    assert api.post(f"{BASE}/months/{MONTH}/fill", headers=staff(api), json={"cells": cells}).status_code == 422


# ── PDF ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("persona", ["staff", "nco"])
def test_the_pdf_downloads_with_awkward_names(api, roster, persona):
    nights = new_month(api)["nights"]
    add(api, nights[0]["id"], "Fish & <Chips> Quiz — Zoë's", period=None)
    add(api, nights[1]["id"], "Drill", flights=["C", "A"])
    add(api, nights[1]["id"], "Band", flights=["A"])
    res = api.get(f"{BASE}/months/{MONTH}/pdf", headers=api.as_(persona))
    assert res.status_code == 200 and res.content[:4] == b"%PDF"
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="Programme December 2026.pdf"' in res.headers["content-disposition"]


def test_an_empty_month_still_makes_a_pdf(api, db, roster):
    new_month(api)
    for night in api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json()["nights"]:
        api.delete(f"{BASE}/nights/{night['id']}", headers=staff(api))
    assert api.get(f"{BASE}/months/{MONTH}/pdf", headers=staff(api)).content[:4] == b"%PDF"


def _pdf_text(api) -> list[str]:
    res = api.get(f"{BASE}/months/{MONTH}/pdf", headers=staff(api))
    assert res.status_code == 200
    return [page.extract_text() for page in PdfReader(io.BytesIO(res.content)).pages]


def test_the_pdf_is_laid_out_like_the_old_programme_doc(api, db, roster):
    # The old doc: the month as the title (no other squadron's name), each
    # session over who runs it by rank and surname, the duty NCO, the
    # important dates, and a second page of roomings with a room key.
    db.add(ProgrammeRoom(name="Drill Hall", position=0))
    db.commit()
    nights = new_month(api)["nights"]
    api.patch(f"{BASE}/nights/{nights[0]['id']}", headers=staff(api),
              json={"uniform": "No.2c SD", "duty_nco": "Cpl Ó Briain / Fg Off Barker"})
    add(api, nights[0]["id"], "Drill", flights=["A", "B"], people=["staff:3001"], room_id=1)
    api.patch(f"{BASE}/months/{MONTH}", headers=staff(api),
              json={"key_dates": [{"when": "13th December", "what": "Carol Service"}]})
    page1, page2 = _pdf_text(api)
    assert page1.startswith("December 2026") and "Ely" not in page1
    for text in ("Probationary Flight", "1st Period", "Break", "Duty NCO", "Wednesday", "02/12/26", "Drill",
                 "Sgt Second", "No.2c SD", "Cpl Ó Briain", "Fg Off Barker", "Important Dates", "13th December",
                 "Carol Service"):
        assert text in page1, text
    assert "Drill Hall" in page2 and "Room Key" in page2


# ── publishing ────────────────────────────────────────────────────────────────

class FakePage:
    def save(self, buf, format):
        buf.write(b"RIFFwebp")


def _website(github):
    github.files[GITHUB_REPO] = {"src/pages/programme.jsx": b'<a href="/programme/11_26_programme.pdf">',
                                 "public/programme/11_26_programme.pdf": b"%PDF old"}


def test_publishing_puts_the_pdf_on_the_website_and_the_month_in_the_record(api, db, roster, github, monkeypatch):
    monkeypatch.setattr(website, "convert_from_bytes", lambda b, dpi, fmt: [FakePage(), FakePage()])
    _website(github)
    night = new_month(api)["nights"][0]["id"]
    db.add(ProgrammeNight(date=date(2026, 12, 3), imported_at=datetime.now()))  # an old doc import
    db.commit()
    add(api, night, "Drill", flights=["A", "B"], instructor="Sgt H")
    res = api.post(f"{BASE}/months/{MONTH}/publish", headers=staff(api), json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["website"]["pdf"] == "12_26_programme.pdf"
    assert body["record"] == {"month": "2026-12", "nights": 9, "sessions": 2}
    assert body["programme"]["status"] == "published"
    site = github.files[GITHUB_REPO]
    assert site["public/programme/12_26_programme.pdf"][:4] == b"%PDF"
    assert "public/programme/11_26_programme.pdf" not in site
    # The import is replaced by the built month; other months are untouched.
    december = db.query(ProgrammeNight).filter(ProgrammeNight.date >= date(2026, 12, 1)).all()
    assert sorted(n.date.day for n in december) == [2, 4, 9, 11, 16, 18, 23, 25, 30]
    rows = db.query(ProgrammeSession).all()
    assert {(r.flight, r.activity, r.instructor) for r in rows} == {("A", "Drill", "Sgt H"), ("B", "Drill", "Sgt H")}
    # An edit after publishing shows there's something the website hasn't had.
    api.patch(f"{BASE}/months/{MONTH}", headers=staff(api), json={"notes": "Changed"})
    assert api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json()["status"] == "changed"


def test_a_website_failure_publishes_nothing(api, db, roster, github, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("poppler missing")

    monkeypatch.setattr(website, "convert_from_bytes", boom)
    _website(github)
    night = new_month(api)["nights"][0]["id"]
    add(api, night, "Drill")
    res = api.post(f"{BASE}/months/{MONTH}/publish", headers=staff(api), json={})
    assert res.status_code == 500 and "PDF conversion failed" in res.json()["detail"]
    assert github.commits == []
    assert db.query(ProgrammeNight).filter(ProgrammeNight.date >= date(2026, 12, 1)).count() == 0
    assert api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json()["status"] == "draft"


def test_publishing_only_the_record_never_touches_github_and_can_tell_everyone(api, roster, github, pushes, db):
    from database.models import PushSubscription

    db.add(PushSubscription(endpoint="https://push.example/nco", p256dh="k", auth="a", email="nco@317atc.co.uk",
                            topics=["programme"], created_at=datetime.now()))
    db.commit()
    night = new_month(api)["nights"][0]["id"]
    add(api, night, "Drill")
    res = api.post(f"{BASE}/months/{MONTH}/publish", headers=staff(api),
                   json={"website": False, "record": True, "notify": True})
    assert res.status_code == 200 and "website" not in res.json()
    assert github.commits == []
    [sent] = pushes.to("https://push.example/nco")
    assert sent["title"] == "The December programme is out" and sent["url"] == "/programme/2026-12"


@pytest.mark.parametrize("body,status", [({"website": False, "record": False}, 422), ({}, 409)])
def test_publishing_needs_somewhere_to_go_and_something_to_send(api, roster, body, status):
    new_month(api)
    assert api.post(f"{BASE}/months/{MONTH}/publish", headers=staff(api), json=body).status_code == status


# ── bank ──────────────────────────────────────────────────────────────────────

def test_the_bank_is_listed_with_how_often_each_ran(api, db, roster):
    night = db.query(ProgrammeNight).first()
    night.sessions.append(ProgrammeSession(period=1, flight="A", activity="drill", category="drill", raw="x"))
    db.commit()
    created = api.post(f"{BASE}/bank", headers=staff(api), json={"title": " Drill ", "category": "drill"})
    assert created.status_code == 201 and created.json()["title"] == "Drill"
    [item] = api.get(f"{BASE}/bank", headers=api.as_("nco")).json()
    assert item["times_run"] == 1 and item["last_run"] == "2026-11-02"


@pytest.mark.parametrize("body,status", [
    ({"title": "drill", "category": "drill"}, 409),
    ({"title": "New", "category": "parties"}, 422),
    ({"title": "New", "category": "lessons", "subject_key": "acp_32_2"}, 422),
    ({"title": "New", "category": "drill", "room_id": 999}, 422),
    ({"title": "", "category": "drill"}, 422),
    ({"title": "New", "category": "drill", "people": ["staff:999"]}, 422),
    # A cadet takes part in a session; they don't usually run one.
    ({"title": "New", "category": "drill", "people": ["cadet:4"]}, 422),
])
def test_a_bank_session_that_cant_be_saved_is_refused(api, roster, body, status):
    api.post(f"{BASE}/bank", headers=staff(api), json={"title": "Drill", "category": "drill"})
    assert api.post(f"{BASE}/bank", headers=staff(api), json=body).status_code == status


def test_editing_the_bank_never_rewrites_a_placed_session(api, roster):
    item = api.post(f"{BASE}/bank", headers=staff(api), json={"title": "Drill", "category": "drill"}).json()
    night = new_month(api)["nights"][0]["id"]
    api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api), json={"night_id": night, "bank_item_id": item["id"]})
    res = api.put(f"{BASE}/bank/{item['id']}", headers=staff(api), json={"title": "Squad Drill", "category": "drill"})
    assert res.json()["title"] == "Squad Drill"
    assert slot_named(api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json(), "Drill")
    assert api.delete(f"{BASE}/bank/{item['id']}", headers=staff(api)).status_code == 200
    assert slot_named(api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json(), "Drill")["bank_item_id"] is None
    for method in ("put", "delete"):
        kw = {"json": {"title": "x", "category": "drill"}} if method == "put" else {}
        assert getattr(api, method)(f"{BASE}/bank/{item['id']}", headers=staff(api), **kw).status_code == 404


def test_the_record_offers_what_isnt_in_the_bank_and_imports_it(api, db, roster):
    nights = db.query(ProgrammeNight).all()
    for i, n in enumerate(nights):
        n.sessions.append(ProgrammeSession(period=1, flight="A", activity="Volleyball", category="sport", raw="x"))
        if i:
            n.sessions.append(ProgrammeSession(period=2, flight="B", activity="Drill", category="drill", raw="x"))
    db.commit()
    api.post(f"{BASE}/bank", headers=staff(api), json={"title": "drill", "category": "drill"})
    history = api.get(f"{BASE}/bank/history", headers=staff(api)).json()
    assert history == [{"title": "Volleyball", "category": "sport", "times": 3, "last_run": "2026-11-16"}]
    res = api.post(f"{BASE}/bank/import", headers=staff(api), json={"titles": ["Volleyball", "volleyball", "Nope"]})
    assert res.json() == {"added": 1}
    assert db.query(ProgrammeBankItem).filter(ProgrammeBankItem.title == "Volleyball").one().category == "sport"


# ── rooms ─────────────────────────────────────────────────────────────────────

def test_rooms_are_kept_in_order_and_a_deleted_one_leaves_its_sessions_without(api, roster):
    hall = api.post(f"{BASE}/rooms", headers=staff(api), json={"name": "Hall", "capacity": 80}).json()
    api.post(f"{BASE}/rooms", headers=staff(api), json={"name": "Classroom", "notes": " Upstairs "})
    assert [r["name"] for r in api.get(f"{BASE}/rooms", headers=api.as_("nco")).json()] == ["Hall", "Classroom"]
    assert api.post(f"{BASE}/rooms", headers=staff(api), json={"name": "hall"}).status_code == 409
    assert api.put(f"{BASE}/rooms/{hall['id']}", headers=staff(api),
                   json={"name": "Classroom"}).status_code == 409
    assert api.put(f"{BASE}/rooms/{hall['id']}", headers=staff(api),
                   json={"name": "Drill hall", "capacity": 70, "active": False}).json()["active"] is False
    night = new_month(api)["nights"][0]["id"]
    add(api, night, "Drill", room_id=hall["id"])
    api.delete(f"{BASE}/rooms/{hall['id']}", headers=staff(api))
    programme = api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json()
    assert slot_named(programme, "Drill")["room_id"] is None
    assert api.delete(f"{BASE}/rooms/{hall['id']}", headers=staff(api)).status_code == 404


# ── people ────────────────────────────────────────────────────────────────────

def test_ncos_get_only_who_they_can_mention_and_no_addresses(api, roster):
    people = api.get(f"{BASE}/people", headers=api.as_("nco")).json()
    assert {p["kind"] for p in people} == {"staff", "nco"}
    assert all(p["email"] is None for p in people)
    everyone = api.get(f"{BASE}/people", headers=staff(api)).json()
    assert any(p["kind"] == "cadet" for p in everyone)
    assert next(p for p in everyone if p["key"] == "staff:3001")["email"] == "staff2@317atc.co.uk"
    assert api.get(f"{BASE}/people?month=soon", headers=staff(api)).status_code == 422


# ── comments ──────────────────────────────────────────────────────────────────

def _comment(api, persona, body, **kw):
    res = api.post(f"{BASE}/months/{MONTH}/comments", headers=api.as_(persona), json={"body": body, **kw})
    assert res.status_code == 201, res.text
    return res.json()


def _thread(programme, text):
    return next(c for c in programme["comments"] if text in c["body"])


def test_mentioning_an_nco_on_a_session_emails_them_a_link_and_the_thread(api, roster, outbox):
    night = new_month(api)["nights"][0]["id"]
    slot = slot_named(add(api, night, "Drill", flights=["A"]), "Drill")
    programme = _comment(api, "staff", f"{mention('Cpl Nina Corporal', 'cadet:1')} can you take this?",
                         slot_id=slot["id"])
    thread = programme["comments"][0]
    assert thread["quote"] == "Wed 2 Dec · 1st half · A Flight: Drill"
    assert thread["mentions"] == [{"key": "cadet:1", "name": "Cpl Nina Corporal"}]
    assert thread["author"]["name"] == "CI Sam Staff"
    assert slot_named(programme, "Drill")["open_comments"] == 1
    [email] = outbox
    assert email["to"] == "nco@317atc.co.uk"
    assert email["subject"] == "CI Sam Staff mentioned you in a comment on the December 2026 programme"
    assert f"/programme/{MONTH}?comment={thread['id']}" in email["html"]
    assert "Wed 2 Dec · 1st half · A Flight: Drill" in email["html"]
    assert "@Cpl Nina Corporal can you take this?" in email["html"]
    assert "Reply" in email["html"] and "reply=1" in email["html"]


def test_assigning_a_thread_emails_an_action_item_and_needs_a_mention(api, roster, outbox):
    new_month(api)
    res = api.post(f"{BASE}/months/{MONTH}/comments", headers=staff(api), json={"body": "do this", "assign": True})
    assert res.status_code == 422 and "@ someone" in res.json()["detail"]
    programme = _comment(api, "staff", f"{mention('Sgt Ned Other', 'cadet:2')} and "
                                       f"{mention('Cpl Nina', 'cadet:1')} sort the duty rota", assign=True)
    thread = programme["comments"][0]
    assert thread["assignee"] == {"key": "cadet:2", "name": "Sgt Ned Other"}
    assert thread["quote"] == "December 2026 programme"
    kinds = {e["to"]: e["subject"] for e in outbox}
    assert "assigned you an action item" in kinds["nco2@317atc.co.uk"]
    assert "mentioned you" in kinds["nco@317atc.co.uk"]
    assert "Assigned to Sgt Ned Other" in outbox[0]["html"]


def test_a_reply_emails_everyone_in_the_thread_but_the_replier(api, roster, outbox):
    night = new_month(api)["nights"][0]["id"]
    programme = _comment(api, "staff", f"{mention('Sgt Sue Second', 'staff:3001')} rooms?", night_id=night)
    root = programme["comments"][0]
    assert root["quote"] == "Wednesday 2 December"
    outbox.clear()
    programme = _comment(api, "nco", "The hall is free", parent_id=root["id"])
    assert {e["to"] for e in outbox} == {"staff@317atc.co.uk", "staff2@317atc.co.uk"}
    assert all(e["subject"] == "Cpl Nina Corporal replied to a comment on the December 2026 programme" for e in outbox)
    # The thread, oldest first, with the new reply in it.
    html = outbox[0]["html"]
    assert html.index("@Sgt Sue Second rooms?") < html.index("The hall is free")
    assert programme["comments"][0]["replies"][0]["author"]["name"] == "Cpl Nina Corporal"


def test_replying_to_a_reply_joins_the_thread_and_reopens_it(api, roster, outbox):
    new_month(api)
    root = _comment(api, "staff", "First")["comments"][0]
    reply = _comment(api, "nco", "Second", parent_id=root["id"])["comments"][0]["replies"][0]
    api.post(f"{BASE}/comments/{root['id']}/resolve", headers=api.as_("nco"))
    programme = _comment(api, "nco2", "Third", parent_id=reply["id"])
    thread = programme["comments"][0]
    assert [r["body"] for r in thread["replies"]] == ["Second", "Third"]
    assert thread["resolved_at"] is None


def test_anyone_who_can_comment_resolves_and_reopens_and_the_thread_hears(api, roster, outbox):
    new_month(api)
    root = _comment(api, "nco", f"{mention('CI Sam Staff', 'staff:3000')} uniform?")["comments"][0]
    outbox.clear()
    res = api.post(f"{BASE}/comments/{root['id']}/resolve", headers=staff(api))
    thread = res.json()["comments"][0]
    assert thread["resolved_at"] and thread["resolved_by"] == "CI Sam Staff"
    assert [(e["to"], e["subject"]) for e in outbox] == [
        ("nco@317atc.co.uk", "CI Sam Staff resolved a comment on the December 2026 programme")]
    assert api.post(f"{BASE}/comments/{root['id']}/resolve", headers=staff(api)).status_code == 409
    outbox.clear()
    res = api.post(f"{BASE}/comments/{root['id']}/reopen", headers=api.as_("snco"))
    assert res.json()["comments"][0]["resolved_at"] is None
    assert {e["to"] for e in outbox} == {"nco@317atc.co.uk", "staff@317atc.co.uk"}
    assert api.post(f"{BASE}/comments/{root['id']}/reopen", headers=staff(api)).status_code == 409


def test_a_reply_cant_be_resolved_on_its_own(api, roster):
    new_month(api)
    root = _comment(api, "staff", "First")["comments"][0]
    reply = _comment(api, "nco", "Second", parent_id=root["id"])["comments"][0]["replies"][0]
    assert api.post(f"{BASE}/comments/{reply['id']}/resolve", headers=staff(api)).status_code == 422
    assert api.post(f"{BASE}/comments/999/resolve", headers=staff(api)).status_code == 404


def test_only_the_author_edits_and_only_new_mentions_are_emailed(api, roster, outbox):
    new_month(api)
    root = _comment(api, "nco", f"{mention('Sue', 'staff:3001')} hi")["comments"][0]
    assert api.patch(f"{BASE}/comments/{root['id']}", headers=staff(api), json={"body": "x"}).status_code == 403
    outbox.clear()
    res = api.patch(f"{BASE}/comments/{root['id']}", headers=api.as_("nco"),
                    json={"body": f"{mention('Sue', 'staff:3001')} {mention('Sam', 'staff:3000')} hi"})
    thread = res.json()["comments"][0]
    assert thread["edited_at"] and len(thread["mentions"]) == 2
    assert [e["to"] for e in outbox] == ["staff@317atc.co.uk"]
    assert api.patch(f"{BASE}/comments/{root['id']}", headers=api.as_("nco"), json={"body": " "}).status_code == 422


def test_ncos_delete_only_their_own_comments_and_staff_any(api, roster):
    new_month(api)
    mine = _comment(api, "nco", "Mine")["comments"][0]
    theirs = _comment(api, "nco2", "Theirs")["comments"][0]
    assert api.delete(f"{BASE}/comments/{theirs['id']}", headers=api.as_("nco")).status_code == 403
    assert api.delete(f"{BASE}/comments/{mine['id']}", headers=api.as_("nco")).status_code == 200
    res = api.delete(f"{BASE}/comments/{theirs['id']}", headers=staff(api))
    assert res.json()["comments"] == []


def test_a_forged_or_cadet_mention_emails_no_one(api, roster, outbox):
    new_month(api)
    _comment(api, "nco", "@[The OC](staff:777) and @[Cara](cadet:4) and @[x](cadet:abc)")
    assert outbox == []


def test_comments_can_only_be_on_this_months_sessions_and_nights(api, roster):
    other = new_month(api, "2026-11")
    slot = slot_named(add(api, other["nights"][0]["id"], "Drill", month="2026-11"), "Drill")
    new_month(api)
    for body in ({"slot_id": slot["id"]}, {"night_id": other["nights"][0]["id"]}, {"parent_id": 999}):
        res = api.post(f"{BASE}/months/{MONTH}/comments", headers=api.as_("nco"), json={"body": "x", **body})
        assert res.status_code == 404
    root = _comment(api, "nco", "x")["comments"][0]
    res = api.post(f"{BASE}/months/2026-11/comments", headers=api.as_("nco"), json={"body": "y", "parent_id": root["id"]})
    assert res.status_code == 404
    assert api.post(f"{BASE}/months/{MONTH}/comments", headers=api.as_("nco"), json={"body": ""}).status_code == 422


def test_ncos_see_names_but_never_addresses_on_comments(api, roster):
    new_month(api)
    _comment(api, "staff", "Hello")
    nco_view = api.get(f"{BASE}/months/{MONTH}", headers=api.as_("nco")).json()["comments"][0]
    staff_view = api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json()["comments"][0]
    assert nco_view["author"] == {"name": "CI Sam Staff", "key": "staff:3000", "email": None}
    assert staff_view["author"]["email"] == "staff@317atc.co.uk"
    assert (nco_view["can_edit"], nco_view["can_delete"]) == (False, False)


def test_open_threads_come_first_and_counts_show_on_the_month_list(api, roster):
    new_month(api)
    old = _comment(api, "staff", "Old")["comments"][0]
    _comment(api, "staff", "Newer")
    api.post(f"{BASE}/comments/{old['id']}/resolve", headers=staff(api))
    _comment(api, "staff", "Newest")
    programme = api.get(f"{BASE}/months/{MONTH}", headers=staff(api)).json()
    assert [c["body"] for c in programme["comments"]] == ["Newest", "Newer", "Old"]
    listed = api.get(f"{BASE}/months", headers=staff(api)).json()["programmes"][0]
    assert listed["open_comments"] == 2


def test_a_mention_also_pushes_to_the_persons_phone(api, db, roster, pushes):
    from database.models import PushSubscription

    db.add(PushSubscription(endpoint="https://push.example/sue", p256dh="k", auth="a", email="staff2@317atc.co.uk",
                            topics=["programme"], created_at=datetime.now()))
    db.commit()
    new_month(api)
    root = _comment(api, "nco", f"{mention('Sue', 'staff:3001')} can you check the rooms?")["comments"][0]
    [sent] = pushes.to("https://push.example/sue")
    assert sent["title"] == "Cpl Nina Corporal mentioned you on the December 2026 programme"
    assert sent["body"] == "@Sue can you check the rooms?"
    assert sent["url"] == f"/programme/{MONTH}?comment={root['id']}"


def test_a_failing_email_never_fails_the_comment(api, roster, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Gmail down")

    monkeypatch.setattr(router, "send_email", boom)
    new_month(api)
    res = api.post(f"{BASE}/months/{MONTH}/comments", headers=staff(api),
                   json={"body": f"{mention('Nina', 'cadet:1')} hi"})
    assert res.status_code == 201 and res.json()["comments"][0]["body"].endswith("hi")


# ── important dates ───────────────────────────────────────────────────────────

def test_a_new_month_starts_with_the_squadrons_events_as_its_important_dates(api, db, roster):
    # Only events touching the month, in date order, in the doc's wording.
    loc = Location(first_line="x", postcode="x")
    db.add(loc)
    db.flush()
    for title, start, end in (("Wing Training Weekend", datetime(2026, 12, 12), datetime(2026, 12, 13)),
                              ("Carol Service", datetime(2026, 12, 6), datetime(2026, 12, 6)),
                              ("Last month", datetime(2026, 11, 1), datetime(2026, 11, 2))):
        db.add(Event317(title=title, reference="r", adult_ic="x", contact_number=1, date_from=start, date_to=end,
                        location_id=loc.id, cost=0, dress="x", description="x"))
    db.commit()
    assert new_month(api)["key_dates"] == [{"when": "6th December", "what": "Carol Service"},
                                           {"when": "12-13th December", "what": "Wing Training Weekend"}]


def test_important_dates_are_edited_without_touching_the_notes(api, roster):
    new_month(api)
    api.patch(f"{BASE}/months/{MONTH}", headers=staff(api), json={"notes": "Keep"})
    res = api.patch(f"{BASE}/months/{MONTH}", headers=staff(api),
                    json={"key_dates": [{"when": " 1st Dec ", "what": " Zoë's parade "}]})
    assert res.status_code == 200
    body = res.json()
    assert body["key_dates"] == [{"when": "1st Dec", "what": "Zoë's parade"}] and body["notes"] == "Keep"
    assert body["history"][0]["summary"] == "Changed the important dates"
    assert api.patch(f"{BASE}/months/{MONTH}", headers=staff(api), json={"key_dates": []}).json()["key_dates"] == []


@pytest.mark.parametrize("dates", [[{"when": "", "what": "x"}], [{"when": "x"}], [{"when": "x" * 41, "what": "x"}],
                                   [{"when": "x", "what": "y"}] * 16])
def test_important_dates_that_wont_print_are_refused(api, roster, dates):
    new_month(api)
    assert api.patch(f"{BASE}/months/{MONTH}", headers=staff(api), json={"key_dates": dates}).status_code == 422


def test_important_dates_on_a_missing_month_are_a_404(api, roster):
    assert api.patch(f"{BASE}/months/{MONTH}", headers=staff(api), json={"key_dates": []}).status_code == 404


# ── kinds ─────────────────────────────────────────────────────────────────────

def _kind(kinds, key):
    return next((k for k in kinds if k["key"] == key), None)


def test_staff_add_a_kind_and_use_it_for_sessions_and_the_bank(api, db, roster):
    res = api.post(f"{BASE}/kinds", headers=staff(api), json={"label": " Shooting & Range ", "colour": "#4a86e8"})
    assert res.status_code == 201
    assert _kind(res.json(), "shooting_range") == {"key": "shooting_range", "label": "Shooting & Range",
                                                   "colour": "#4a86e8", "hidden": False, "builtin": False}
    night = new_month(api)["nights"][0]
    programme = add(api, night["id"], "Air Rifle", category="shooting_range")
    assert slot_named(programme, "Air Rifle")["category"] == "shooting_range"
    assert api.post(f"{BASE}/bank", headers=staff(api),
                    json={"title": "Air Rifle", "category": "shooting_range"}).status_code == 201
    # Published, the record's stats label it by name rather than failing on an unknown key.
    api.post(f"{BASE}/months/{MONTH}/publish", headers=staff(api), json={"website": False})
    assert {"key": "shooting_range", "label": "Shooting & Range"} in api.get(
        "/programme/stats", headers=staff(api)).json()["categories_all"]


@pytest.mark.parametrize("body,status", [({"label": "Drill and inspection"}, 409), ({"label": "drill"}, 409),
                                         ({"label": "Shooting"}, 409),
                                         ({"label": "!!!"}, 422), ({"label": ""}, 422),
                                         ({"label": "x", "colour": "blue"}, 422)])
def test_a_kind_that_would_clash_or_cant_be_keyed_is_refused(api, roster, body, status):
    assert api.post(f"{BASE}/kinds", headers=staff(api), json=body).status_code == status


def test_a_built_in_kind_is_renamed_coloured_and_hidden_but_never_deleted(api, roster):
    res = api.put(f"{BASE}/kinds/first_aid", headers=staff(api),
                  json={"label": "Courses", "colour": "#4a86e8", "hidden": True})
    assert res.status_code == 200
    assert _kind(res.json(), "first_aid") == {"key": "first_aid", "label": "Courses", "colour": "#4a86e8",
                                              "hidden": True, "builtin": True}
    # Built-ins stay in their place in the list.
    assert [k["key"] for k in res.json()][:5] == ["drill", "classifications", "lessons", "fieldcraft", "first_aid"]
    # Hidden, a session can still have it: old sessions keep their kind.
    add(api, new_month(api)["nights"][0]["id"], "First aid", category="first_aid")
    assert api.delete(f"{BASE}/kinds/first_aid", headers=staff(api)).status_code == 409


def test_renaming_a_kind_to_anothers_name_is_refused(api, roster):
    assert api.put(f"{BASE}/kinds/drill", headers=staff(api), json={"label": "band"}).status_code == 409
    assert api.put(f"{BASE}/kinds/drill", headers=staff(api), json={"label": "DRILL AND INSPECTION"}).status_code == 200


def test_editing_or_deleting_a_missing_kind_is_a_404(api, roster):
    assert api.put(f"{BASE}/kinds/nope", headers=staff(api), json={"label": "x"}).status_code == 404
    assert api.delete(f"{BASE}/kinds/nope", headers=staff(api)).status_code == 404


def test_a_kind_is_only_deleted_while_nothing_has_it(api, db, roster):
    api.post(f"{BASE}/kinds", headers=staff(api), json={"label": "Gliding"})
    api.post(f"{BASE}/kinds", headers=staff(api), json={"label": "Unused"})
    db.add(ProgrammeSession(night=ProgrammeNight(date=date(2026, 1, 5), imported_at=datetime.now()),
                            period=1, flight="A", activity="Viking", category="gliding", split=False, raw="Viking"))
    db.commit()
    assert api.delete(f"{BASE}/kinds/gliding", headers=staff(api)).status_code == 409
    assert api.delete(f"{BASE}/kinds/shooting", headers=staff(api)).status_code == 409  # built in
    res = api.delete(f"{BASE}/kinds/unused", headers=staff(api))
    assert res.status_code == 200 and _kind(res.json(), "unused") is None


def test_ncos_read_the_kinds(api, roster):
    res = api.get(f"{BASE}/kinds", headers=api.as_("nco"))
    assert res.status_code == 200 and _kind(res.json(), "drill")["label"] == "Drill and inspection"


def test_a_session_with_a_kind_that_doesnt_exist_is_refused(api, roster):
    night = new_month(api)["nights"][0]
    res = api.post(f"{BASE}/months/{MONTH}/slots", headers=staff(api),
                   json={"night_id": night["id"], "title": "x", "category": "nope"})
    assert res.status_code == 422
