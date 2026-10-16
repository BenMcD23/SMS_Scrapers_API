"""Drawing the joining form onto the official 3822A and 3822H templates."""

import io
from datetime import datetime

import pytest
from PIL import Image
from pypdf import PdfReader

from core.recruit_form import RecruitFormData
from core.test_recruit_form import COMPLETE, complete_form
from form_generators import recruit_pdf
from form_generators.recruit_pdf import TEMPLATE_A, TEMPLATE_H, _latin, _widgets, build_pdf

SIGNED = datetime(2026, 10, 16, 19, 30)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (300, 80), (0, 0, 80, 255)).save(buf, "PNG")
    return buf.getvalue()


def _pdf(data: dict, which: str = "both") -> PdfReader:
    form = RecruitFormData.model_validate(data)
    return PdfReader(io.BytesIO(build_pdf(form, which, _png(), _png(), SIGNED)))


def _text(reader: PdfReader) -> str:
    return "\n".join(p.extract_text() for p in reader.pages)


MEDICAL = {
    "has": True, "conditions": ["asthma", "other"], "ehcPlan": False,
    "details": [{"name": f"Condition {i}", "severity": "mild", "soughtAdvice": True} for i in range(1, 6)],
    "allergies": {"nut": {"autoInjector": True, "severity": "severe"}, "medicines": {"severity": "mild"},
                  "other": {"severity": "mild"}},
    "allergyOther": "Kiwi", "diet": ["vegan", "gluten_free", "other"], "dietOther": "No mushrooms",
}


def _all_mapped_fields() -> dict[str, list[tuple[str, int]]]:
    """Every (field, occurrence) the filler can be asked for, per template."""
    a = [(f, i) for f, i in recruit_pdf.ETHNICITY_FIELDS.values()]
    a += [(f, 0) for f in (*recruit_pdf.GENDER_FIELDS.values(), *recruit_pdf.PRONOUN_FIELDS.values(),
                           *recruit_pdf.CADET_CONTACT_FIELDS, *recruit_pdf.NOK_FIELDS, *recruit_pdf.EXTRA_FIELDS,
                           "Full Name of Child", "DOB_af_date", "Religion", "Other Youth Organisation", "Title",
                           "Full Name", "Relationship", "Additional_Contact_Title", "Full Name_2",
                           "Relationship_2", "School", "Where did you hear about the unit",
                           "Reason for joining", "Forename", "Surname", "Signature", "Date4_af_date")]
    for prefix in (*recruit_pdf.ORGANISATION_FIELDS.values(), *recruit_pdf.CONSENT_FIELDS.values(),
                   "External_Agency", "PR", "Medical_Allergies_Diet"):
        a += [(f"{prefix}_Yes", 0), (f"{prefix}_No", 0)]

    h = [(f, 0) for f in (*recruit_pdf.CONDITION_FIELDS.values(), *recruit_pdf.DIET_FIELDS.values(),
                          recruit_pdf.H_OTHER_ALLERGY, recruit_pdf.H_OTHER_DIET, "EHC Yes", "EHC No",
                          "EHC Provide Copy Yes", "EHC Provide Copy No", "DD", "MM", "YYYY")]
    for key, prefix in recruit_pdf.ALLERGY_FIELDS.items():
        h += [(recruit_pdf.ALLERGY_TICK.get(key, prefix), 0), (recruit_pdf.ALLERGY_AUTO.get(key, f"{prefix} - Auto"), 0)]
        h += [(f"{prefix} - {s}", 0) for s in ("Mild", "Moderate", "Severe")]
    for slot, suffix in ((0, ""), (1, "_2")):
        h += [(f + suffix, 0) for f in recruit_pdf._COND_TEXT.values()]
        h += [(f"Cond{slot + 1}{s}", 0) for s in ("Mild", "Moderate", "Severe")]
        h += [("Yes Sought Advice", slot), ("No Not Sought Advice", slot)]
    for who, (fore, sur) in (("Parent", ("Forename", "Surname")), ("Cadet", ("Forename_2", "Surname_2"))):
        h += [(fore, 0), (sur, 0), (f"{who} Signature", 0)] + [(f"{who}_{p}", 0) for p in ("DD", "MM", "YYYY")]
    return {str(TEMPLATE_A): a, str(TEMPLATE_H): h}


@pytest.mark.parametrize("template", [str(TEMPLATE_A), str(TEMPLATE_H)])
def test_every_field_the_filler_uses_exists_on_the_template(template):
    # Guards a new RAFAC version of the form: a renamed field would otherwise
    # only show up as a KeyError on the first submitted recruit.
    widgets = _widgets(template)
    absent = [(f, i) for f, i in _all_mapped_fields()[template] if len(widgets.get(f, [])) <= i]
    assert absent == []


def test_the_shared_ethnicity_names_are_told_apart_by_position():
    # Ethnicity_11 is both "African" (Black column, higher up) and "Arab"
    # (Other group, lower down); ticking one must not tick the other.
    widgets = _widgets(str(TEMPLATE_A))["Ethnicity_11"]
    african, arab = widgets
    assert african[1][1] > arab[1][1]           # African sits above Arab
    assert african[1][0] > arab[1][0]           # and in the right-hand column


def test_the_3822a_is_flat_five_pages_with_the_answers_on_it():
    reader = _pdf(COMPLETE, "3822A")
    assert len(reader.pages) == 5
    assert not reader.get_fields()              # no fillable fields left to alter
    text = _text(reader)
    for answer in ("Alex Smith", "01/05/2013", "LE1 2AB", "jane@example.com", "City Academy", "16/10/2026"):
        assert answer in text


def test_both_without_a_medical_declaration_is_just_the_3822a():
    assert len(_pdf(COMPLETE, "both").pages) == 5


def test_more_than_two_conditions_get_extra_copies_of_the_condition_page():
    reader = _pdf(complete_form(medical=MEDICAL), "3822H")
    # page 1 (conditions 1-2), two more copies (3-4, 5), then the allergies page
    assert len(reader.pages) == 4
    pages = [p.extract_text() for p in reader.pages]
    assert "Condition 1" in pages[0] and "Condition 2" in pages[0]
    assert "Condition 3" in pages[1] and "Condition 4" in pages[1]
    assert "Condition 5" in pages[2]
    assert "Kiwi" in pages[3] and "No mushrooms" in pages[3]
    assert len(_pdf(complete_form(medical=MEDICAL)).pages) == 9


def test_under_sixteen_the_parent_signs_the_health_declaration():
    text = _pdf(complete_form(medical=MEDICAL), "3822H").pages[-1].extract_text()
    assert "Jane" in text and "Smith" in text


def test_at_sixteen_the_cadet_signs_it_under_their_own_name():
    cadet = {**COMPLETE["cadet"], "fullName": "Alex Jordan Smith", "dob": "2010-10-16"}
    text = _pdf(complete_form(cadet=cadet, medical=MEDICAL), "3822H").pages[-1].extract_text()
    assert "Alex" in text and "Jordan Smith" in text and "Jane" not in text


def test_a_long_answer_wraps_instead_of_running_off_the_box():
    about = {"school": "City Academy", "reason": "Wants to learn to fly and shoot. " * 12}
    assert "learn to fly" in _text(_pdf(complete_form(about=about), "3822A"))


@pytest.mark.parametrize("name, drawn", [
    ("Zoë Siân", "Zoë Siân"),               # Latin-1: kept
    ("Łukasz Dvořák", "Lukasz Dvorák"),     # folded rather than lost; á is Latin-1
    ("李雷", "??"),                          # nothing to fold to; never a crash
])
def test_names_outside_the_pdf_fonts_are_folded_not_dropped(name, drawn):
    assert _latin(name) == drawn
    cadet = {**COMPLETE["cadet"], "fullName": name}
    assert drawn in _text(_pdf(complete_form(cadet=cadet), "3822A"))


def test_a_signature_is_cropped_to_its_ink_so_it_fills_the_box():
    # A pad's image is mostly blank; scaled whole into a 20pt-high box the
    # signature came out a few points tall.
    from PIL import ImageDraw

    pad = Image.new("RGBA", (600, 160), (0, 0, 0, 0))
    ImageDraw.Draw(pad).line([(100, 70), (200, 90)], fill=(0, 0, 80, 255), width=4)
    buf = io.BytesIO()
    pad.save(buf, "PNG")
    ink = recruit_pdf._ink_only(buf.getvalue())
    assert ink.width < 120 and ink.height < 40
    # A blank pad stays as it is rather than failing.
    blank = io.BytesIO()
    Image.new("RGBA", (50, 20), (0, 0, 0, 0)).save(blank, "PNG")
    assert recruit_pdf._ink_only(blank.getvalue()).size == (50, 20)


def test_every_ethnicity_ticks_its_own_box():
    # Ethnicity_11 and _12 each name two different boxes (African/Arab,
    # Caribbean/Other); if two options resolved to the same box, ticking one
    # would tick the other.
    widgets = _widgets(str(TEMPLATE_A))
    boxes = {(widgets[name][i][0], widgets[name][i][1]) for name, i in recruit_pdf.ETHNICITY_FIELDS.values()}
    assert len(boxes) == len(recruit_pdf.ETHNICITY_FIELDS) == 19


def test_each_allergys_boxes_sit_on_one_row_with_severities_in_order():
    # Row membership was checked against the printed labels when the mapping
    # was written; this fails if a new template version reshuffles the rows.
    widgets = _widgets(str(TEMPLATE_H))
    for key, prefix in recruit_pdf.ALLERGY_FIELDS.items():
        tick = recruit_pdf.ALLERGY_TICK.get(key, prefix)
        auto = recruit_pdf.ALLERGY_AUTO.get(key, f"{prefix} - Auto")
        severities = [f"{prefix} - {s}" for s in ("Mild", "Moderate", "Severe")]
        rects = [widgets[n][0][1] for n in (tick, auto, *severities)]
        centres = [(r[1] + r[3]) / 2 for r in rects]
        assert max(centres) - min(centres) <= 3, key
        xs = [r[0] for r in rects[2:]]
        assert xs == sorted(xs), key


def test_the_yes_and_no_boxes_of_every_question_share_a_row_yes_on_the_left():
    widgets = _widgets(str(TEMPLATE_A))
    for prefix in (*recruit_pdf.ORGANISATION_FIELDS.values(), *recruit_pdf.CONSENT_FIELDS.values(),
                   "External_Agency", "PR", "Medical_Allergies_Diet"):
        (_, yes), (_, no) = widgets[f"{prefix}_Yes"][0], widgets[f"{prefix}_No"][0]
        assert abs((yes[1] + yes[3]) / 2 - (no[1] + no[3]) / 2) <= 3, prefix
        assert yes[0] < no[0], prefix
