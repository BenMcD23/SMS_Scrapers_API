"""Reading DBS, BPSS and mandatory training out of the portal's raw copies, and
deciding what is due. The shapes are guesses from Dashy, so odd ones matter."""

from datetime import date

import pytest

from core import staff_compliance as sc

TODAY = date(2026, 11, 13)


# ── DBS ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    {"dateValidFrom": "2024-03-01T00:00:00", "disclosureNumber": "001234567890"},
    [{"dateValidFrom": "2024-03-01T00:00:00"}],
    {"items": [{"dateOfCheck": "2024-03-01T00:00:00"}]},
    {"value": [{"issueDate": "2024-03-01"}]},
])
def test_dbs_is_read_from_any_of_the_portals_shapes(raw):
    item = sc.dbs(raw, TODAY)
    assert item["achieved"] == date(2024, 3, 1)
    # No expiry on the record: three years from issue.
    assert item["expires"] == date(2027, 3, 1)
    assert item["status"] == sc.OK


def test_the_disclosure_number_never_leaves_the_parser():
    item = sc.dbs({"dateValidFrom": "2024-03-01", "disclosureNumber": "001234567890"}, TODAY)
    assert "001234567890" not in str(item)


def test_the_newest_dbs_wins():
    raw = [{"dateValidFrom": "2020-01-01"}, {"dateValidFrom": "2025-06-01"}, {"dateValidFrom": "2022-01-01"}]
    assert sc.dbs(raw, TODAY)["achieved"] == date(2025, 6, 1)


def test_an_expiry_on_the_record_beats_the_three_year_rule():
    item = sc.dbs({"dateValidFrom": "2024-03-01", "dateExpires": "2026-12-01"}, TODAY)
    assert item["expires"] == date(2026, 12, 1)
    assert item["status"] == sc.EXPIRING


def test_a_dbs_issued_on_29_february_lapses_on_28_february():
    assert sc.dbs({"dateValidFrom": "2024-02-29"}, TODAY)["expires"] == date(2027, 2, 28)


@pytest.mark.parametrize("raw", [None, [], {}, "nonsense", [{"dateValidFrom": "0001-01-01T00:00:00"}],
                                 [{"dateValidFrom": "not a date"}], {"items": None}])
def test_no_usable_dbs_is_missing_not_an_error(raw):
    assert sc.dbs(raw, TODAY)["status"] == sc.MISSING


# ── BPSS ──────────────────────────────────────────────────────────────────────

def test_bpss_is_held_once_done():
    item = sc.bpss({"bpss": [{"checkDate": "2019-05-01"}]}, TODAY)
    assert (item["achieved"], item["expires"], item["status"]) == (date(2019, 5, 1), None, sc.OK)


def test_no_bpss_is_missing():
    assert sc.bpss([], TODAY)["status"] == sc.MISSING


# ── mandatory training ────────────────────────────────────────────────────────

TRACKER_ROW = {
    "personnelWebId": "abc", "serviceNumber": "3000",
    "mandatoryTraining": [
        {"criteriaId": 1, "displayName": "Safeguarding", "mandatory": True,
         "dateAchieved": "2024-01-01T00:00:00", "dateExpires": "2026-12-20T00:00:00"},
        {"criteriaId": 2, "displayName": "Prevent", "mandatory": True,
         "dateAchieved": "2023-01-01T00:00:00", "dateExpires": "2026-10-01T00:00:00"},
        {"criteriaId": 3, "displayName": "Data protection", "mandatory": True,
         "dateAchieved": "0001-01-01T00:00:00", "dateExpires": "0001-01-01T00:00:00"},
        {"criteriaId": 4, "displayName": "Fire safety", "mandatory": True,
         "dateAchieved": "2025-01-01T00:00:00", "dateExpires": "2028-01-01T00:00:00"},
        {"criteriaId": 5, "displayName": "Optional extra", "mandatory": False,
         "dateAchieved": None, "dateExpires": None},
        {"criteriaId": 6, "displayName": "", "mandatory": True},
        "not a row",
    ],
}


def test_each_mandatory_module_gets_a_status():
    items = {i["name"]: i["status"] for i in sc.training(TRACKER_ROW, TODAY)}
    assert items == {
        "Data protection": sc.MISSING,
        "Fire safety": sc.OK,
        "Prevent": sc.EXPIRED,
        "Safeguarding": sc.EXPIRING,
    }


def test_modules_are_keyed_by_criteria_so_a_rename_doesnt_resend():
    keys = {i["key"] for i in sc.training(TRACKER_ROW, TODAY)}
    assert "training:1" in keys


@pytest.mark.parametrize("raw", [None, [], {"mandatoryTraining": None}, {"mandatoryTraining": "x"}])
def test_an_unexpected_tracker_row_has_no_modules(raw):
    assert sc.training(raw, TODAY) == []


# ── the window, the person and what to chase ──────────────────────────────────

@pytest.mark.parametrize("expires, status", [
    (date(2026, 11, 12), sc.EXPIRED),
    (date(2026, 11, 13), sc.EXPIRING),   # lapses at the end of today
    (date(2027, 1, 12), sc.EXPIRING),    # exactly 60 days
    (date(2027, 1, 13), sc.OK),
    (None, sc.OK),
])
def test_the_60_day_window(expires, status):
    assert sc.status_for(expires, TODAY) == status


def test_someone_never_synced_has_no_items_rather_than_everything_missing():
    assert sc.items_for({}, TODAY) is None
    assert sc.worst(None) is None


def test_a_person_is_as_bad_as_their_worst_item():
    items = sc.items_for({"dbs": {"dateValidFrom": "2025-01-01"}, "training": TRACKER_ROW}, TODAY)
    assert sc.worst(items) == sc.EXPIRED


def test_chasing_lists_lapsed_then_missing_then_soonest_due():
    items = sc.items_for({"dbs": {"dateValidFrom": "2023-12-01"}, "bpss": [], "training": TRACKER_ROW}, TODAY)
    assert [(i["name"], i["status"]) for i in sc.due(items)] == [
        ("Prevent", sc.EXPIRED),
        ("BPSS", sc.MISSING),
        ("Data protection", sc.MISSING),
        ("DBS", sc.EXPIRING),          # 1 Dec 2026
        ("Safeguarding", sc.EXPIRING),  # 20 Dec 2026
    ]
