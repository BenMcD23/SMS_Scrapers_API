"""Assessment drafts: each sheet saved on the server while it's being filled
in, so a reload or another phone doesn't lose the work."""

import pytest

import routers.assessment_drafts as drafts
from database.models import AssessmentDraft, InspectionSheet

INSPECTION = {
    "date": "2026-03-04",
    "uniform": "blues",
    "marks": {
        "101": {"score": "8.5", "comments": [
            {"id": "c1", "region": "Shoes", "type": "fault", "text": "Scuffed toes"},
        ]},
        "102": {"score": "", "comments": [], "absent": True},
    },
    "order": [102, 101],
}
RADIO = {"cadetCin": 101, "cadetName": "Zoë Ünal", "criteria": {"callsigns": True},
         "comments": "Good — clear and calm", "date": "2026-03-04"}
SAVED_AT = "2026-03-04T19:42:00.000Z"


def _path(kind):
    return f"/assessment-drafts/{kind}"


def _put(api, kind, headers, data=None, saved_at=SAVED_AT):
    return api.put(_path(kind), json={"data": data or RADIO, "saved_at": saved_at}, headers=headers)


def _call(api, method, kind, headers):
    if method == "put":
        return _put(api, kind, headers)
    return getattr(api, method)(_path(kind), headers=headers)


# ── access ────────────────────────────────────────────────────────────────────
# Each sheet's draft lets in the same people as the sheet's own submit endpoint.

ALLOWED = {
    "inspection": {"staff", "staff2", "owner", "oc", "snco"},
    "leadership": {"staff", "staff2", "owner", "oc", "snco", "nco", "nco2"},
    "radio":      {"staff", "staff2", "owner", "oc", "snco", "nco", "nco2"},
    "space":      {"staff", "staff2", "owner", "oc", "snco", "nco", "nco2"},
    "moi":        {"staff", "staff2", "owner", "oc"},
}
PERSONAS = ["staff", "staff2", "owner", "oc", "snco", "nco", "nco2", "cadet", "cadet2"]


def test_every_sheet_with_a_draft_is_covered_here():
    assert set(drafts.ACCESS) == set(ALLOWED)


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("kind", ALLOWED)
def test_no_token_is_401(api, method, kind):
    assert _call(api, method, kind, {}).status_code == 401


@pytest.mark.parametrize("method", ["get", "put", "delete"])
@pytest.mark.parametrize("kind", ALLOWED)
@pytest.mark.parametrize("persona", PERSONAS)
def test_only_the_people_who_can_submit_the_sheet_get_in(api, method, kind, persona):
    status = _call(api, method, kind, api.as_(persona)).status_code
    if persona in ALLOWED[kind]:
        assert status not in (401, 403)
    else:
        assert status == 403


def test_an_unknown_sheet_is_404(api):
    assert _put(api, "flying", api.as_("staff")).status_code == 404
    assert api.get(_path("flying"), headers=api.as_("staff")).status_code == 404


# ── saving and restoring ──────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,data", [("inspection", INSPECTION), ("radio", RADIO)])
def test_a_saved_sheet_comes_back_exactly(api, kind, data):
    headers = api.as_("staff")
    assert _put(api, kind, headers, data).status_code == 200
    res = api.get(_path(kind), headers=headers)
    assert res.status_code == 200
    assert res.json()["data"] == data
    assert res.json()["saved_at"] == SAVED_AT
    assert res.json()["updated_at"]


def test_nothing_saved_is_404(api):
    res = api.get(_path("radio"), headers=api.as_("nco"))
    assert res.status_code == 404
    assert res.json()["detail"] == "No draft saved"


def test_each_save_replaces_the_last(api, db):
    headers = api.as_("staff")
    _put(api, "inspection", headers, INSPECTION)
    # A cadet removed from the sheet must stay removed, so this is a
    # replacement, not a merge.
    smaller = {**INSPECTION, "marks": {"101": {"score": "9", "comments": []}}, "order": [101]}
    _put(api, "inspection", headers, smaller, saved_at="2026-03-04T19:50:00.000Z")

    got = api.get(_path("inspection"), headers=headers).json()
    assert got["data"] == smaller
    assert got["saved_at"] == "2026-03-04T19:50:00.000Z"
    assert db.query(AssessmentDraft).count() == 1


def test_a_save_from_an_older_page_has_no_saved_at(api):
    headers = api.as_("staff")
    api.put(_path("radio"), json={"data": RADIO}, headers=headers)
    assert api.get(_path("radio"), headers=headers).json()["saved_at"] is None


def test_each_assessor_has_their_own_draft(api):
    # Two inspectors marking different flights on the same night mustn't
    # overwrite each other's sheet.
    _put(api, "inspection", api.as_("staff"), INSPECTION)
    _put(api, "inspection", api.as_("snco"), {**INSPECTION, "uniform": "mtp", "marks": {}, "order": []})

    assert api.get(_path("inspection"), headers=api.as_("staff")).json()["data"]["uniform"] == "blues"
    assert api.get(_path("inspection"), headers=api.as_("snco")).json()["data"]["uniform"] == "mtp"
    assert api.get(_path("inspection"), headers=api.as_("staff2")).status_code == 404


def test_each_kind_of_sheet_is_kept_apart(api):
    # A radio assessment in progress mustn't replace the inspection sheet.
    headers = api.as_("staff")
    _put(api, "inspection", headers, INSPECTION)
    _put(api, "radio", headers, RADIO)
    assert api.get(_path("inspection"), headers=headers).json()["data"] == INSPECTION
    assert api.get(_path("radio"), headers=headers).json()["data"] == RADIO


def test_a_draft_never_counts_as_a_submitted_inspection(api, db):
    # History and flight scores read Inspection_Sheets. A half-marked night
    # must not appear there, or every unmarked cadet would look AWOL.
    _put(api, "inspection", api.as_("staff"), INSPECTION)
    assert db.query(InspectionSheet).count() == 0
    assert api.get("/inspections/sheets", headers=api.as_("staff")).json() == []


def test_an_empty_sheet_can_be_saved(api):
    headers = api.as_("staff")
    assert _put(api, "inspection", headers, {"date": "2026-03-04", "marks": {}}).status_code == 200


# ── rejections ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"data": "nope"},
    {"data": ["a", "list"]},
    {"saved_at": SAVED_AT},
    {"data": RADIO, "saved_at": 12},
])
def test_malformed_saves_are_422(api, body):
    assert api.put(_path("radio"), json=body, headers=api.as_("staff")).status_code == 422


def test_an_oversized_draft_is_refused_and_keeps_the_previous_one(api, monkeypatch):
    headers = api.as_("staff")
    _put(api, "radio", headers, RADIO)
    monkeypatch.setattr(drafts, "MAX_DRAFT_BYTES", 200)
    res = _put(api, "radio", headers, {**RADIO, "comments": "x" * 500})
    assert res.status_code == 413
    assert api.get(_path("radio"), headers=headers).json()["data"] == RADIO


# ── clearing ──────────────────────────────────────────────────────────────────

def test_clearing_removes_only_your_own_draft_of_that_sheet(api):
    _put(api, "radio", api.as_("staff"))
    _put(api, "leadership", api.as_("staff"))
    _put(api, "radio", api.as_("nco"))

    assert api.delete(_path("radio"), headers=api.as_("staff")).json() == {"deleted": True}
    assert api.get(_path("radio"), headers=api.as_("staff")).status_code == 404
    assert api.get(_path("leadership"), headers=api.as_("staff")).status_code == 200
    assert api.get(_path("radio"), headers=api.as_("nco")).status_code == 200


def test_clearing_with_nothing_saved_is_fine(api):
    res = api.delete(_path("space"), headers=api.as_("nco"))
    assert res.status_code == 200
    assert res.json() == {"deleted": False}
