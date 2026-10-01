"""Programme doc parsing — built from Docs-API-shaped JSON, so the merged-cell
rules (whole squadron, both flights, split flights, continuation rows) are
checked without touching Google."""

from datetime import datetime

import pytest

import texts.programme_parser as pp

COLS = 12
# Date | Probationary | 1st Period (A | B) | Break | Probationary | 2nd Period (A | A | B) | Uniform | Duty NCO | Notes
# Period 2 has A Flight split across two sub-columns.


def cell(text="", span=1):
    c = {"content": [{"paragraph": {"elements": [{"textRun": {"content": text}}]}}]}
    if span > 1:
        c["tableCellStyle"] = {"columnSpan": span}
    return c


def row(*cells):
    """Pad a row out to COLS with the empty cells a merge leaves behind."""
    cells = list(cells)
    return {"tableCells": cells + [cell() for _ in range(COLS - len(cells))]}


HEADER_TOP = row(cell("Date"), cell("Probationary"), cell("1st Period", 2), cell(), cell("Break"),
                 cell("Probationary"), cell("2nd Period", 3), cell(), cell(), cell("Uniform"), cell("Duty NCO"),
                 cell("Notes"))
HEADER_SUB = row(cell(), cell("C Flight"), cell("A Flight"), cell("B Flight"), cell(), cell("C Flight"),
                 cell("A Flight", 2), cell(), cell("B Flight"))


def table(*nights):
    return {"columns": COLS, "tableRows": [HEADER_TOP, HEADER_SUB, *nights]}


def night(date, p1=("", "", ""), p2=("", "", "", ""), uniform="", dnco="", spans=None):
    spans = spans or {}
    cells = [cell(date)]
    for i, text in enumerate(p1, start=1):
        cells.append(cell(text, spans.get(i, 1)))
    cells.append(cell("Break"))
    for i, text in enumerate(p2, start=5):
        cells.append(cell(text, spans.get(i, 1)))
    cells += [cell(uniform), cell(dnco)]
    return row(*cells)


@pytest.fixture
def doc(monkeypatch):
    holder = {}

    class Docs:
        def documents(self):
            return self

        def get(self, documentId):
            holder["asked"] = documentId
            return self

        def execute(self):
            return {"body": {"content": [{"paragraph": {}}, {"table": holder["table"]}]}}

    monkeypatch.setattr(pp, "_docs_clients", lambda: (object(), Docs()))
    monkeypatch.setattr(pp, "_find_programme_doc_id", lambda drive, m, y: "doc-1")
    return holder


def test_separate_flights_and_c_flight(doc):
    doc["table"] = table(night("Wednesday 2nd", p1=("Intro", "Drill", "Radio"), p2=("Kit", "Nav", "", "First Aid"),
                               uniform="Blues", dnco="Cpl X"))
    [n] = pp.parse_programme(9, 2026)
    assert n["date"] == datetime(2026, 9, 2)
    assert (n["uniform"], n["dnco"]) == ("Blues", "Cpl X")
    assert n["c_flight"] == "1st Period:\nIntro\n\n2nd Period:\nKit"
    assert n["main_body"] == ("1st Period\nA Flight:\nDrill\n\nB Flight:\nRadio\n\n"
                              "2nd Period\nA Flight:\nNav\n\nB Flight:\nFirst Aid")


def test_whole_squadron_and_both_flights(doc):
    doc["table"] = table(night("4/9", p1=("Sqn parade", "", ""), p2=("", "Games", "", ""),
                               spans={1: 3, 6: 3}))
    [n] = pp.parse_programme(9, 2026)
    assert "1st Period\nWhole Squadron:\nSqn parade" in n["main_body"]
    assert "2nd Period\nBoth Flights:\nGames" in n["main_body"]
    # Whole-squadron text also reaches C Flight's message.
    assert n["c_flight"] == "1st Period:\nSqn parade"


def test_a_split_flight_is_joined_with_slashes(doc):
    doc["table"] = table(night("9th", p2=("", "Shooting\nRange 1", "Swim", "")))
    [n] = pp.parse_programme(9, 2026)
    assert "A Flight:\nShooting, Range 1 / Swim" in n["main_body"]


def test_continuation_rows_merge_into_the_night_above(doc):
    doc["table"] = table(
        night("Wed 2nd", uniform="No.2a SD", dnco=""),
        night("", uniform="Civvies", dnco="Sgt Y"),
        night("Fri 4th", uniform="MTP"),
    )
    a, b = pp.parse_programme(9, 2026)
    assert (a["uniform"], a["dnco"]) == ("No.2a SD, Civvies", "Sgt Y")
    assert b["uniform"] == "MTP"


def test_soft_line_breaks_become_newlines(doc):
    doc["table"] = table(night("2", p1=("", "Drill\x0bPractice", "")))
    assert "Drill\nPractice" in pp.parse_programme(9, 2026)[0]["main_body"]


def test_rows_with_unreadable_dates_are_skipped(doc):
    doc["table"] = table(night("TBC"), night("Friday 4th"))
    assert [n["date"].day for n in pp.parse_programme(9, 2026)] == [4]


def test_an_impossible_date_skips_that_night_instead_of_failing_the_month(doc):
    doc["table"] = table(night("31/02"), night("Friday 4th"))
    assert [n["date"] for n in pp.parse_programme(2, 2026)] == [datetime(2026, 2, 4)]


def test_no_table_and_too_short_and_changed_layout(doc):
    doc["table"] = None

    class NoTable:
        pass

    with pytest.raises((ValueError, TypeError)):
        pp.parse_programme(9, 2026)
    doc["table"] = {"columns": COLS, "tableRows": [HEADER_TOP, HEADER_SUB]}
    with pytest.raises(ValueError, match="too short"):
        pp.parse_programme(9, 2026)
    doc["table"] = {"columns": COLS, "tableRows": [row(cell("Something else")), HEADER_SUB, night("2")]}
    with pytest.raises(ValueError, match="layout changed"):
        pp.parse_programme(9, 2026)


def test_first_table_none():
    assert pp._first_table({"body": {"content": [{"paragraph": {}}]}}) is None
    assert pp._first_table({}) is None


@pytest.mark.parametrize("raw,expected", [
    ("14/01/26", datetime(2026, 1, 14)),
    ("14/01/2026", datetime(2026, 1, 14)),
    ("14-01", datetime(2026, 9, 14).replace(month=1)),
    ("14.1", datetime(2026, 1, 14)),
    ("14th", datetime(2026, 9, 14)),
    ("1st September", datetime(2026, 9, 1)),
    ("3rd Sept", datetime(2026, 9, 3)),
    ("22nd Jan 27", datetime(2027, 1, 22)),
    ("5 Smarch", datetime(2026, 9, 5)),  # unknown month name falls back to the doc's month
    ("", None),
    ("TBC", None),
    ("31/02", None),
    ("0/9", None),
    ("32", None),
])
def test_parse_date(raw, expected):
    assert pp._parse_date(raw, 9, 2026) == expected


def test_find_programme_doc_id(monkeypatch):
    listed = []

    def children(drive, parent, name, mime):
        listed.append((parent, name))
        return {"2026": [{"id": "year-folder"}], "09_26": [{"id": "the-doc"}]}.get(name, [])

    monkeypatch.setattr(pp, "_list_children", children)
    assert pp._find_programme_doc_id(None, 9, 2026) == "the-doc"
    assert listed[1] == ("year-folder", "09_26")
    with pytest.raises(FileNotFoundError, match="'2027' folder"):
        pp._find_programme_doc_id(None, 9, 2027)
    with pytest.raises(FileNotFoundError, match="'10_26'"):
        pp._find_programme_doc_id(None, 10, 2026)


@pytest.mark.parametrize("raw", ["Wednesday 2nd", "Wed 2nd", "Wed. 2nd", "WEDNESDAY 2", "wed, 2nd", "2nd"])
def test_weekday_prefixes_are_stripped(doc, raw):
    doc["table"] = table(night(raw))
    assert [n["date"] for n in pp.parse_programme(9, 2026)] == [datetime(2026, 9, 2)]


def test_month_names_are_not_mistaken_for_weekdays():
    assert pp.WEEKDAY_RE.sub("", "3rd Sept").strip() == "3rd Sept"
    assert pp.WEEKDAY_RE.sub("", "Monday 7th March").strip() == "7th March"
