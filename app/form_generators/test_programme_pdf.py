"""The programme PDF's layout rules: the doc's date wording, which cells merge,
and the uniform colours."""

import io
from datetime import date

import pytest
from pypdf import PdfReader

from form_generators.programme_pdf import _columns, _cover, _runs, _uniform_fill, build_programme_pdf, when_label

FLIGHTS = [{"key": "C", "label": "Probationary Flight", "apart": True}, {"key": "A", "label": "A Flight"},
           {"key": "B", "label": "B Flight"}]


@pytest.mark.parametrize("start,end,label", [
    (date(2026, 3, 1), None, "1st March"),
    (date(2026, 3, 22), date(2026, 3, 22), "22nd March"),
    (date(2026, 3, 13), None, "13th March"),
    (date(2026, 3, 21), date(2026, 3, 22), "21-22nd March"),
    (date(2026, 3, 30), date(2026, 4, 2), "30th March - 2nd April"),
])
def test_dates_read_as_the_doc_wrote_them(start, end, label):
    assert when_label(start, end) == label


def slot(id, period, flights, **kw):
    return {"id": id, "period": period, "flights": flights, "title": f"S{id}", "running": "", "room": None, **kw}


def test_a_whole_night_session_for_everyone_runs_across_the_break():
    cover = _cover({"slots": [slot(1, None, ["C", "A", "B"])]}, _columns(FLIGHTS))
    assert _runs(cover) == [(0, 6)]


def test_a_whole_night_session_for_some_flights_leaves_the_break():
    # The doc's "Mystery Session" for A and B all night: each period merged, the break between.
    cover = _cover({"slots": [slot(1, None, ["A", "B"]), slot(2, 1, ["C"])]}, _columns(FLIGHTS))
    assert cover[3] == "break" and _runs(cover) == [(0, 0), (1, 2), (3, 3), (4, 4), (5, 6)]


def test_empty_cells_never_merge():
    assert _runs(_cover({"slots": []}, _columns(FLIGHTS))) == [(i, i) for i in range(7)]


@pytest.mark.parametrize("uniform,fill", [("No.3 SD", "#93c47d"), ("No.2c SD", "#073763"), ("No. 2c", "#073763"),
                                          ("Sports Kit", "#ff0000"), ("No.2a SD", "#a4c2f4"), ("Fancy dress", None),
                                          (None, None)])
def test_uniforms_are_coloured_as_in_the_doc(uniform, fill):
    got = _uniform_fill(uniform)
    assert (got and "#" + got.hexval()[2:]) == fill if fill else got is None


def test_a_month_with_one_flight_and_no_rooms_or_dates_still_prints():
    pdf = build_programme_pdf({"title": "May 2027", "flights": [FLIGHTS[1]], "rooms": [], "key_dates": [],
                               "nights": [{"date": date(2027, 5, 5), "uniform": None, "duty_nco": None,
                                           "slots": [slot(1, 1, ["A"], title="Fish & <Chips>", colour="#4a86e8")]}]})
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "Fish & <Chips>" in text and "Important Dates" not in text
