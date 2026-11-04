"""Inspection drafts: the sheet saved on the server while it is being marked,
so a reload or another phone doesn't lose a night's marking."""

import pytest

from database.models import InspectionDraft, InspectionSheet

PATH = "/inspections/draft"


def _sheet(**over):
    sheet = {
        "date": "2026-03-04",
        "uniform": "blues",
        "marks": {
            "101": {"score": "8.5", "comments": [
                {"id": "c1", "region": "Shoes", "type": "fault", "text": "Scuffed toes"},
            ]},
            "102": {"score": "", "comments": [], "absent": True},
        },
        "order": [102, 101],
        "saved_at": "2026-03-04T19:42:00.000Z",
    }
    return {**sheet, **over}


def _call(api, method, headers):
    if method == "put":
        return api.put(PATH, json=_sheet(), headers=headers)
    return getattr(api, method)(PATH, headers=headers)


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_no_token_is_401(api, method):
    assert _call(api, method, {}).status_code == 401


# Same people as the sheet itself: SNCOs run inspections, NCOs and cadets don't.
@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("persona", ["nco", "nco2", "cadet"])
def test_ncos_and_cadets_are_turned_away(api, method, persona):
    assert _call(api, method, api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "snco", "owner", "oc"])
def test_staff_and_snco_can_save_and_get_back_their_sheet(api, persona):
    headers = api.as_(persona)
    assert api.put(PATH, json=_sheet(), headers=headers).status_code == 200
    res = api.get(PATH, headers=headers)
    assert res.status_code == 200
    data = res.json()["data"]
    # Stored as validated: a mark that left "absent" out comes back as None
    # (follow the absence log), which the page reads the same way.
    expected = _sheet()
    expected["marks"]["101"]["absent"] = None
    assert data == expected


# ── saving and restoring ──────────────────────────────────────────────────────

def test_nothing_saved_is_404(api):
    res = api.get(PATH, headers=api.as_("staff"))
    assert res.status_code == 404
    assert res.json()["detail"] == "No inspection in progress"


def test_each_save_replaces_the_last(api, db):
    headers = api.as_("staff")
    api.put(PATH, json=_sheet(), headers=headers)
    # A cadet removed from the sheet must stay removed, so this is a
    # replacement, not a merge.
    smaller = _sheet(marks={"101": {"score": "9", "comments": []}}, order=[101])
    api.put(PATH, json=smaller, headers=headers)

    assert api.get(PATH, headers=headers).json()["data"]["marks"] == {
        "101": {"score": "9", "comments": [], "absent": None},
    }
    assert db.query(InspectionDraft).count() == 1


def test_each_marker_has_their_own_draft(api):
    # Two inspectors marking different flights on the same night mustn't
    # overwrite each other's sheet.
    api.put(PATH, json=_sheet(), headers=api.as_("staff"))
    api.put(PATH, json=_sheet(uniform="mtp", marks={}, order=[]), headers=api.as_("snco"))

    assert api.get(PATH, headers=api.as_("staff")).json()["data"]["uniform"] == "blues"
    assert api.get(PATH, headers=api.as_("snco")).json()["data"]["uniform"] == "mtp"
    assert api.get(PATH, headers=api.as_("staff2")).status_code == 404


def test_a_draft_never_counts_as_a_submitted_sheet(api, db):
    # History and flight scores read Inspection_Sheets. A half-marked night
    # must not appear there, or every unmarked cadet would look AWOL.
    api.put(PATH, json=_sheet(), headers=api.as_("staff"))
    assert db.query(InspectionSheet).count() == 0
    assert api.get("/inspections/sheets", headers=api.as_("staff")).json() == []


def test_non_ascii_comments_come_back_unchanged(api):
    headers = api.as_("staff")
    comment = {"id": "c9", "region": "Hair / Face", "type": "positive", "text": "Zoë's beret — spotless"}
    api.put(PATH, json=_sheet(marks={"101": {"score": "10", "comments": [comment]}}), headers=headers)
    assert api.get(PATH, headers=headers).json()["data"]["marks"]["101"]["comments"] == [comment]


def test_an_empty_sheet_can_be_saved(api):
    # Removing the last recorded cadet saves an empty sheet; that's valid.
    headers = api.as_("staff")
    res = api.put(PATH, json=_sheet(marks={}, order=[]), headers=headers)
    assert res.status_code == 200
    assert api.get(PATH, headers=headers).json()["data"]["marks"] == {}


def test_save_returns_when_it_was_stored(api):
    res = api.put(PATH, json=_sheet(), headers=api.as_("staff"))
    assert res.json()["updated_at"]


# ── rejections ────────────────────────────────────────────────────────────────

def test_a_bad_date_is_400_and_keeps_the_previous_draft(api):
    headers = api.as_("staff")
    api.put(PATH, json=_sheet(), headers=headers)
    res = api.put(PATH, json=_sheet(date="04/03/2026", marks={}), headers=headers)
    assert res.status_code == 400
    assert api.get(PATH, headers=headers).json()["data"]["marks"] != {}


@pytest.mark.parametrize("bad", [
    {"uniform": "no1s"},
    {"marks": {"not-a-cin": {"score": "5"}}},
    {"marks": "nope"},
    {"order": ["x"]},
    {"date": None},
])
def test_malformed_sheets_are_422(api, bad):
    assert api.put(PATH, json=_sheet(**bad), headers=api.as_("staff")).status_code == 422


# ── clearing ──────────────────────────────────────────────────────────────────

def test_clearing_removes_only_your_own_draft(api):
    api.put(PATH, json=_sheet(), headers=api.as_("staff"))
    api.put(PATH, json=_sheet(), headers=api.as_("snco"))

    assert api.delete(PATH, headers=api.as_("staff")).json() == {"deleted": True}
    assert api.get(PATH, headers=api.as_("staff")).status_code == 404
    assert api.get(PATH, headers=api.as_("snco")).status_code == 200


def test_clearing_with_nothing_saved_is_fine(api):
    res = api.delete(PATH, headers=api.as_("staff"))
    assert res.status_code == 200
    assert res.json() == {"deleted": False}
