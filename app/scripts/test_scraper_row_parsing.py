"""Row parsing for the scrapers that now read their tables in one evaluate().

The extraction moved into JS; these pin the Python side that turns what comes
back into records, which is where the old per-cell logic lived.
"""
from datetime import datetime

from scripts.quali_scraper import _parse_qual_rows
from scripts.alergies import _parse_allergy_rows, _parse_dietary_rows
from scripts.absence_scraper import _parse_absence_rows


def qual_row(cells, class_name="", attachment=False):
    return {"className": class_name, "cells": cells, "hasAttachment": attachment}


def test_qual_row_dates_are_day_first():
    """Bader renders dd/mm/yyyy — a US parse would move an expiry by months."""
    [qual] = _parse_qual_rows([qual_row(["First Aid", "05/03/2026", "05/03/2029"])], set())
    assert qual["date_achieved"] == datetime(2026, 3, 5)
    assert qual["date_expires"] == datetime(2026, 3, 5).replace(year=2029)
    assert qual["qual_type"] == "First Aid"
    assert qual["status"] == "true"


def test_blank_and_missing_dates_are_none_not_an_error():
    [qual] = _parse_qual_rows([qual_row(["Radio Operator", "", "n/a"])], set())
    assert qual["date_achieved"] is None and qual["date_expires"] is None

    [short] = _parse_qual_rows([qual_row(["Radio Operator"])], set())
    assert short["date_achieved"] is None and short["date_expires"] is None


def test_hidden_sibling_rows_are_not_qualifications():
    rows = [
        qual_row(["First Aid", "05/03/2026", "05/03/2029"]),
        qual_row(["proof.pdf"], class_name="sibling collapse"),
    ]
    assert [q["qual_type"] for q in _parse_qual_rows(rows, set())] == ["First Aid"]


def test_padding_rows_are_dropped():
    rows = [qual_row([]), qual_row(["   ", "", ""]), qual_row(["Shooting", "", ""])]
    assert [q["qual_type"] for q in _parse_qual_rows(rows, set())] == ["Shooting"]


def test_newlines_in_the_name_cell_collapse_to_spaces():
    [qual] = _parse_qual_rows([qual_row(["Blue\nWings", "", ""])], set())
    assert qual["qual_type"] == "Blue Wings"


def test_attachment_is_only_reported_for_quals_we_were_asked_about():
    """None means "not checked" and False means "checked, none attached" — the
    caller stores them differently, so they must not collapse together."""
    rows = [
        qual_row(["First Aid", "", ""], attachment=True),
        qual_row(["Shooting", "", ""], attachment=False),
        qual_row(["Radio", "", ""], attachment=True),
    ]
    quals = _parse_qual_rows(rows, {"first aid", "shooting"})
    by_name = {q["qual_type"]: q["has_attachment"] for q in quals}
    assert by_name == {"First Aid": True, "Shooting": False, "Radio": None}


def test_attachment_check_matches_case_insensitively():
    [qual] = _parse_qual_rows([qual_row(["FIRST AID", "", ""], attachment=True)], {"first aid"})
    assert qual["has_attachment"] is True


def test_allergy_rows_read_the_injector_checkbox_not_a_cell():
    rows = [
        {"cells": ["Peanuts", "", "Severe", "Carries EpiPen"], "injector": True},
        {"cells": ["Pollen", "", "Mild", ""], "injector": False},
    ]
    assert _parse_allergy_rows(rows) == [
        {"allergy": "Peanuts", "auto_injector": "Yes", "severity": "Severe", "details": "Carries EpiPen"},
        {"allergy": "Pollen", "auto_injector": "No", "severity": "Mild", "details": ""},
    ]


def test_allergy_placeholder_and_short_rows_are_dropped():
    rows = [
        {"cells": ["No allergies recorded"], "injector": False},
        {"cells": ["", "", "", ""], "injector": False},
    ]
    assert _parse_allergy_rows(rows) == []


def test_dietary_rows_need_a_name():
    rows = [["Vegetarian", "No meat"], ["", "orphaned detail"], ["Halal"]]
    assert _parse_dietary_rows(rows) == [{"name": "Vegetarian", "details": "No meat"}]


def test_absence_rows_parse_the_window_day_first():
    rows = [["Alex", "Smith", "1234567", "09/07/2026", "31/07/2026", "Holiday"]]
    assert _parse_absence_rows(rows) == [{
        "first_name": "Alex",
        "last_name": "Smith",
        "date_from": datetime(2026, 7, 9),
        "date_to": datetime(2026, 7, 31),
        "reason": "Holiday",
    }]


def test_absence_rows_without_a_usable_window_are_dropped():
    """"No data available in table" renders as a single wide cell, and a row
    missing either date would give the AWOL check an open-ended window."""
    rows = [
        ["No data available in table"],
        ["Alex", "Smith", "1234567", "", "31/07/2026", "Holiday"],
        ["", "Smith", "1234567", "09/07/2026", "31/07/2026", "Holiday"],
    ]
    assert _parse_absence_rows(rows) == []
