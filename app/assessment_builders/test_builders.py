"""The assessment-sheet builders stamp marks onto the official RAFAC forms.

Each builder falls back to returning just the overlay if the template merge
fails — which would hand out a sheet with marks floating on a blank page. These
check the template is really underneath, and that bad input (a corrupt
signature, overlong text) degrades rather than raises."""

import io
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from assessment_builders import leadership, moi, radio, space
from assessment_builders.inspection_pdf import build_inspection_pdf
from assessment_builders.pdf_utils import decode_pdf_data_url

CADET = SimpleNamespace(first_name="Amy", last_name="Able", rank="Cpl")
BAD_SIG = "data:image/png;base64,bm90IGFuIGltYWdl"  # valid base64, not an image


def _text(pdf: bytes) -> str:
    # Whitespace is collapsed because how pypdf lays out extracted text changes
    # between releases (newer ones put every word on its own line), and these
    # tests care which words are on the page, not how the extractor spaces them.
    pages = (p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    return " ".join(" ".join(pages).split())


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


@pytest.mark.parametrize("sig", ["", "Typed Name", BAD_SIG])
def test_leadership_sheet_sits_on_the_template(sig):
    data = leadership.process_assessment_data({"scores": {str(i): 3 for i in range(1, 11)}, "date": "2026-03-04",
                                               "cadet_name": "Amy Able", "assessor_signature": sig,
                                               "debriefing_notes": "word " * 400})
    pdf = leadership.generate_leadership_pdf(data)
    assert _pages(pdf) == 1
    text = _text(pdf)
    assert "Leadership Blue Badge" in text and "Amy Able" in text and "04/03/26" in text


def test_radio_sheet_sits_on_the_template():
    data = radio.process_radio_data({"criteria": {"callsigns": True, "nonsense": True}, "date": "2026-03-04",
                                     "cyber_sec_date": "2026-01-02", "assessor_initials": "NC",
                                     "assessor_signature": BAD_SIG}, CADET)
    assert data["cadet_surname"] == "ABLE" and data["cyber_sec_date"] == "02/01/26"
    text = _text(radio.generate_radio_pdf(data))
    assert "BASIC RADIO OPERATOR AWARD" in text and "ABLE" in text


def test_space_sheet_sits_on_the_template():
    data = space.process_space_data({"checklist": {"pts": True, "made_up": True}, "pts_date": "bad date",
                                     "experiments": "x" * 2000, "date": ""}, CADET)
    # Unknown checklist keys are ignored; a bad date is printed as typed.
    assert "made_up" not in data["checklist"] and data["passed"] is False
    assert data["pts_date"] == "bad date" and data["date"] == ""
    text = _text(space.generate_space_pdf(data))
    assert "Blue Space workbook Checklist" in text and "Amy ABLE" in text


def test_moi_sheet_keeps_both_template_pages():
    data = moi.process_assessment_data({"scores": {"1": 3}, "cadet_surname": "Able",
                                        "section_comments": {"identifying": "Clear aim", "unknown": "ignored"},
                                        "cadet_signature": BAD_SIG})
    pdf = moi.generate_moi_pdf(data)
    assert _pages(pdf) == 2
    assert "AIR  CADET" in _text(pdf) or "AIR CADET" in _text(pdf)


@pytest.mark.parametrize("uniform", ["blues", "mtp", "something-new"])
def test_inspection_pdf(uniform):
    flights = [{
        "flight": "A", "present": 1, "awol": 1, "penalty": 5, "average": 7.5, "total": 2.5,
        "cadets": [
            {"cin": 1, "first_name": "Amy", "last_name": "Able", "rank": "Cpl", "flight": "A", "score": 7.5,
             "absent": False, "awol": False, "faults": [{"region": "Shoes", "text": "dull " * 60}],
             "positives": [{"region": None, "text": None}]},
            {"cin": 2, "first_name": "Bob", "last_name": "Baker", "rank": None, "flight": "A", "score": None,
             "absent": True, "awol": True, "faults": [], "positives": []},
        ],
    }]
    pdf = build_inspection_pdf("2026-03-04", flights, uniform)
    assert pdf.startswith(b"%PDF") and "Able" in _text(pdf)


def test_inspection_pdf_with_no_flights():
    assert build_inspection_pdf("2026-03-04", [], "blues").startswith(b"%PDF")


def test_decode_rejects_non_base64():
    # Non-base64 decodes to nothing, which callers treat as "no file sent".
    assert not decode_pdf_data_url("data:application/pdf;base64,%%%")
