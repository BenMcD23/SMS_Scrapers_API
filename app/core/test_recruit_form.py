"""The joining form's rules: what counts as done, who signs the health
declaration, and what the public schema refuses. The HTTP side is in
routers/test_recruits.py and the PDFs in form_generators/test_recruit_pdf.py."""

import copy
from datetime import date

import pytest
from pydantic import ValidationError

from core import recruit_form
from core.recruit_form import RecruitFormData, age_on, cadet_signs_health_form, missing, progress

_ADDRESS = {"property": "12", "street": "Long Lane", "town": "Leicester", "postcode": "LE1 2AB",
            "mobile": "07700 900123"}

# A form with every required answer, no medical declaration. Shared with the
# router and PDF tests so they all start from the same known-good answers.
COMPLETE = {
    "cadet": {"fullName": "Alex Smith", "dob": "2013-05-01", "gender": "female", "pronoun": "she",
              "ethnicity": "white_british", "externalAgency": False},
    "contact": {**_ADDRESS, "email": "alex@example.com"},
    "history": {k: False for k, _ in recruit_form.ORGANISATIONS},
    "nok": {**_ADDRESS, "hasParentalResponsibility": True, "title": "Mrs", "fullName": "Jane Smith",
            "relationship": "Mother", "email": "jane@example.com"},
    "consent": {**{k: True for k, _ in recruit_form.CONSENTS}, "accommodationTypes": ["female"]},
    "medical": {"has": False},
    "about": {"school": "City Academy"},
    "signer": {"forename": "Jane", "surname": "Smith"},
}


def complete_form(**changes) -> dict:
    """COMPLETE with some sections replaced, e.g. complete_form(medical={...})."""
    data = copy.deepcopy(COMPLETE)
    data.update(changes)
    return data


def _gaps(data: dict) -> dict[str, list[str]]:
    return {k: v for k, v in missing(RecruitFormData.model_validate(data)).items() if v}


def test_a_complete_form_has_nothing_missing():
    assert _gaps(COMPLETE) == {}


def test_an_empty_form_is_missing_every_step():
    assert set(_gaps({})) == {"cadet", "contact", "history", "parent", "consent", "medical", "about"}


def test_progress_counts_steps_and_sign_only_once_submitted():
    form = RecruitFormData.model_validate(COMPLETE)
    assert progress(form, submitted=False)["done"] == 7
    done = progress(form, submitted=True)
    assert (done["done"], done["total"]) == (8, 8)
    assert progress(RecruitFormData(), submitted=False)["done"] == 0


@pytest.mark.parametrize("contact, gap", [
    ({"mobile": "", "homePhone": ""}, "Cadet's phone number"),
    ({"mobile": "", "homePhone": "0116 496 0000"}, None),   # either phone will do
    ({"email": "not-an-email"}, "Cadet's email address"),
    ({"postcode": ""}, "Cadet's postcode"),
])
def test_contact_details_need_a_phone_and_a_real_email(contact, gap):
    data = complete_form(contact={**COMPLETE["contact"], **contact})
    assert _gaps(data).get("contact", []) == ([gap] if gap else [])


def test_gender_other_needs_saying_what():
    cadet = {**COMPLETE["cadet"], "gender": "other"}
    assert "Gender" in _gaps(complete_form(cadet=cadet))["cadet"]
    assert "cadet" not in _gaps(complete_form(cadet={**cadet, "genderOther": "Non-binary"}))


def test_a_no_to_any_consent_still_counts_as_answered():
    # "No" is an answer; only a question left blank is missing.
    consent = {**COMPLETE["consent"], "marksmanship": False, "photo": False}
    assert "consent" not in _gaps(complete_form(consent=consent))
    del consent["photo"]
    assert _gaps(complete_form(consent=consent))["consent"] == [recruit_form.CONSENTS[0][1]]


def test_accommodation_consent_needs_at_least_one_type():
    consent = {**COMPLETE["consent"], "accommodationTypes": []}
    assert _gaps(complete_form(consent=consent))["consent"] == ["Which accommodation types you consent to"]
    assert "consent" not in _gaps(complete_form(consent={**consent, "accommodation": False}))


def _described(key: str, **over) -> dict:
    """A fully answered details card for a ticked condition."""
    return {"condition": key, "name": recruit_form.condition_name(key) if key in dict(recruit_form.CONDITIONS) else key, "severity": "mild", "soughtAdvice": False,
            "routine": "None", "strenuous": "Short of breath", "control": "Yes, inhaler", **over}


def test_medical_yes_needs_the_details():
    gaps = _gaps(complete_form(medical={"has": True}))["medical"]
    assert "At least one condition, allergy or dietary requirement" in gaps
    assert "Education, Health and Care Plan" in gaps

    medical = {"has": True, "conditions": ["asthma"], "ehcPlan": True, "details": [_described("asthma")],
               "allergies": {"nut": {"autoInjector": True}, "other": {"severity": "mild"}}, "diet": ["other"]}
    assert _gaps(complete_form(medical=medical))["medical"] == [
        "Whether you'll share the Education, Health and Care Plan",
        "Severity of each allergy",
        "Details of the other allergy",
        "Details of the other dietary requirement",
    ]

    medical.update(ehcCopy=True, allergies={"nut": {"autoInjector": True, "severity": "severe"}}, diet=["vegan"])
    assert "medical" not in _gaps(complete_form(medical=medical))


def test_every_ticked_condition_needs_its_own_details():
    # Ticking three and describing one used to count as done.
    medical = {"has": True, "conditions": ["asthma", "diabetes", "learning"], "ehcPlan": False,
               "details": [_described("asthma")]}
    assert _gaps(complete_form(medical=medical))["medical"] == [
        "Details of Diabetes",
        "Details of Learning difficulties",
    ]
    medical["details"] += [_described("diabetes"), _described("learning", name="Dyslexia")]
    assert "medical" not in _gaps(complete_form(medical=medical))


def test_a_condition_card_lists_exactly_what_it_is_missing():
    medical = {"has": True, "conditions": ["asthma"], "ehcPlan": False,
               "details": [_described("asthma", severity=None, routine="", control="  ")]}
    assert _gaps(complete_form(medical=medical))["medical"] == [
        "Asthma: severity, how it affects them day to day, whether they can control it"]


def test_no_to_sought_advice_is_an_answer_and_medication_is_optional():
    medical = {"has": True, "conditions": ["asthma"], "ehcPlan": False,
               "details": [_described("asthma", soughtAdvice=False, medication="")]}
    assert "medical" not in _gaps(complete_form(medical=medical))


def test_a_condition_added_by_hand_is_named_by_number_until_it_has_a_name():
    medical = {"has": True, "conditions": [], "diet": ["vegan"], "ehcPlan": False,
               "details": [{"severity": "mild", "soughtAdvice": True, "routine": "a", "strenuous": "b", "control": "c"}]}
    assert _gaps(complete_form(medical=medical))["medical"] == ["Condition 1: name"]


def test_the_name_a_ticked_condition_starts_with_drops_the_examples():
    assert recruit_form.condition_name("learning") == "Learning difficulties"
    assert recruit_form.condition_name("asthma") == "Asthma"


@pytest.mark.parametrize("dob, day, age", [
    (date(2010, 3, 4), date(2026, 3, 3), 15),
    (date(2010, 3, 4), date(2026, 3, 4), 16),
    (date(2012, 2, 29), date(2026, 2, 28), 13),   # leap-day birthday not reached yet
    (None, date(2026, 1, 1), None),
])
def test_age_on_a_day(dob, day, age):
    assert age_on(dob, day) == age


def test_the_cadet_signs_the_health_form_from_sixteen_and_only_if_there_is_one():
    sixteen = complete_form(cadet={**COMPLETE["cadet"], "dob": "2010-03-04"}, medical={"has": True})
    form = RecruitFormData.model_validate(sixteen)
    assert cadet_signs_health_form(form, date(2026, 3, 4))
    assert not cadet_signs_health_form(form, date(2026, 3, 3))
    form.medical.has = False
    assert not cadet_signs_health_form(form, date(2026, 3, 4))


@pytest.mark.parametrize("bad", [
    {"cadet": {"surprise": "field"}},
    {"cadet": {"fullName": "x" * 201}},
    {"cadet": {"ethnicity": "martian"}},
    {"medical": {"details": [{}] * (recruit_form.MAX_CONDITIONS + 1)}},
    {"medical": {"allergies": {"cats": {"severity": "mild"}}}},
    {"consent": {"accommodationTypes": ["tent"]}},
    {"unknown_section": {}},
])
def test_the_schema_refuses_unknown_keys_values_and_oversized_answers(bad):
    with pytest.raises(ValidationError):
        RecruitFormData.model_validate(bad)


def test_every_option_list_has_unique_keys():
    opts = recruit_form.options()
    for name in ("ethnicities", "conditions", "allergies", "diets", "accommodation", "organisations", "consents"):
        keys = [o["key"] for o in opts[name]]
        assert len(keys) == len(set(keys)), name


# ── dates, people and contact details ───────────────────────────────────────

TODAY = date(2026, 10, 16)


@pytest.mark.parametrize("dob, gap", [
    ("2014-10-16", None),                                                 # 12 today
    ("2014-10-17", "Date of birth: cadets must be at least 12 to join"),  # 12 tomorrow
    ("2027-01-01", "Date of birth (it's in the future)"),
])
def test_the_child_must_be_at_least_twelve(dob, gap):
    data = complete_form(cadet={**COMPLETE["cadet"], "dob": dob})
    gaps = missing(RecruitFormData.model_validate(data), TODAY)["cadet"]
    assert gaps == ([gap] if gap else [])


def test_no_parental_responsibility_blocks_the_form():
    nok = {**COMPLETE["nok"], "hasParentalResponsibility": False}
    assert _gaps(complete_form(nok=nok))["parent"] == [
        "Only someone with parental responsibility for the child can fill in and sign this form"]


@pytest.mark.parametrize("change, gap", [
    ({"mobile": "0770090"}, "Cadet's mobile number (it doesn't look complete)"),
    ({"mobile": "call me"}, "Cadet's mobile number (it doesn't look complete)"),
    ({"mobile": "+44 (0)7700 900123"}, None),
    ({"email2": "nope"}, "Cadet's second email address (it doesn't look right)"),
    ({"postcode": "12345"}, "Cadet's postcode (it doesn't look like a UK postcode)"),
    ({"postcode": "m350aa"}, None),
    ({"postcode": "12345", "country": "Ireland"}, None),                # not checked outside the UK
    ({"postcode": "12345", "country": "United Kingdom"}, "Cadet's postcode (it doesn't look like a UK postcode)"),
])
def test_contact_details_must_look_real(change, gap):
    data = complete_form(contact={**COMPLETE["contact"], **change})
    assert _gaps(data).get("contact", []) == ([gap] if gap else [])


def test_an_additional_contact_is_optional_but_needs_enough_to_be_reached():
    assert "parent" not in _gaps(complete_form(extra={}))
    gaps = _gaps(complete_form(extra={"fullName": "John Smith"}))["parent"]
    assert gaps == ["Additional contact's relationship", "Additional contact's phone number",
                    "Additional contact's email address"]
    ok = {"fullName": "John Smith", "relationship": "Father", "mobile": "07700 900456", "email": "j@example.com"}
    assert "parent" not in _gaps(complete_form(extra=ok))


def test_a_condition_added_by_hand_counts_as_something_declared():
    medical = {"has": True, "ehcPlan": False, "details": [_described("asthma", condition=None, name="Eczema")]}
    assert "medical" not in _gaps(complete_form(medical=medical))


def test_every_listed_condition_can_be_ticked_and_described():
    keys = [k for k, _ in recruit_form.CONDITIONS]
    medical = {"has": True, "conditions": keys, "ehcPlan": False, "details": [_described(k, name="x") for k in keys]}
    assert "medical" not in _gaps(complete_form(medical=medical))


def test_tidy_drops_answers_to_questions_the_parent_was_taken_past():
    form = RecruitFormData.model_validate(complete_form(
        cadet={**COMPLETE["cadet"], "gender": "male", "genderOther": "x"},
        consent={**COMPLETE["consent"], "accommodation": False, "accommodationTypes": ["male"]},
        medical={"has": True, "conditions": ["asthma"], "ehcPlan": False, "ehcCopy": True,
                 "details": [_described("asthma"), _described("diabetes"), _described("x", condition=None, name="Eczema")],
                 "allergies": {"nut": {"severity": "mild"}}, "allergyOther": "kiwi", "diet": ["vegan"], "dietOther": "x"},
    ))
    t = recruit_form.tidy(form)
    assert t.cadet.genderOther == "" and t.consent.accommodationTypes == []
    assert t.medical.ehcCopy is None and t.medical.allergyOther == "" and t.medical.dietOther == ""
    # Diabetes was unticked; the hand-added one stays.
    assert [d.name for d in t.medical.details] == ["Asthma", "Eczema"]
    assert form.medical.ehcCopy is True          # the original is untouched

    gone = recruit_form.tidy(RecruitFormData.model_validate(complete_form(
        medical={"has": False, "conditions": ["asthma"], "diet": ["vegan"]})))
    assert gone.medical == recruit_form.Medical(has=False)
