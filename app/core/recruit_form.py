"""The online joining form: what a recruit's parent fills in, and how complete it is.

It carries the content of RAFAC Form 3822A (consent certificate) and 3822H
(health declaration). Parents fill it in at home from a link; the answers are
stored as JSON on a RecruitApplication and drawn onto the official PDFs by
form_generators/recruit_pdf.py.

Everything is optional while the parent is part-way through (progress is saved
as they go). ``missing`` says what is still needed, per step; the same rules
drive the progress staff see on SMS and the check at submission, so the two
can't disagree.
"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# ── Option lists ──────────────────────────────────────────────────────────────
# (key, label). The keys are stored; the labels are served to both frontends
# with the form, so the wording lives in one place. The PDF field each maps to
# is in recruit_pdf.py.

ETHNICITIES = [
    ("white_british", "English / Welsh / Scottish / Northern Irish / British", "White"),
    ("white_irish", "Irish", "White"),
    ("gypsy", "Gypsy or Irish Traveller", "White"),
    ("white_other", "Any other White background", "White"),
    ("indian", "Indian", "Asian / Asian British"),
    ("pakistani", "Pakistani", "Asian / Asian British"),
    ("bangladeshi", "Bangladeshi", "Asian / Asian British"),
    ("chinese", "Chinese", "Asian / Asian British"),
    ("asian_other", "Any other Asian background", "Asian / Asian British"),
    ("african", "African", "Black / African / Caribbean / Black British"),
    ("caribbean", "Caribbean", "Black / African / Caribbean / Black British"),
    ("black_other", "Any other Black / African / Caribbean background", "Black / African / Caribbean / Black British"),
    ("white_black_caribbean", "White and Black Caribbean", "Mixed / Multiple ethnic groups"),
    ("white_black_african", "White and Black African", "Mixed / Multiple ethnic groups"),
    ("white_asian", "White and Asian", "Mixed / Multiple ethnic groups"),
    ("mixed_other", "Any other Mixed / Multiple ethnic background", "Mixed / Multiple ethnic groups"),
    ("arab", "Arab", "Other ethnic group"),
    ("other", "Any other ethnic group", "Other ethnic group"),
    ("pnts", "Prefer not to say", "Prefer not to say"),
]

CONDITIONS = [
    ("asthma", "Asthma"), ("back", "Back injury"), ("concentration", "Concentration problems"),
    ("diabetes", "Diabetes"), ("epilepsy", "Epilepsy"), ("eyesight", "Eyesight"),
    ("fractures", "Fractures"), ("hearing", "Hearing"),
    ("objects", "Inability to move everyday objects"), ("incontinence", "Incontinence"),
    ("learning", "Learning difficulties (e.g. dyslexia, ADHD, dyspraxia, autism)"),
    ("dexterity", "Manual dexterity"), ("memory", "Memory"), ("migraine", "Migraine"),
    ("mobility", "Mobility problems"), ("coordination", "Physical coordination"),
    ("rheumatic", "Rheumatic fever"), ("speech", "Speech"), ("mental", "Mental health"),
    ("other", "Other"),
]

ALLERGIES = [
    ("animal", "Animal skin / hair"), ("dust", "Dust mites"), ("hayfever", "Hay fever / pollen"),
    ("chemicals", "Household chemicals"), ("insects", "Insect bites / stings"),
    ("lactose", "Lactose"), ("latex", "Latex"), ("medicines", "Medicines"), ("mould", "Mould"),
    ("nut", "Nut"), ("penicillin", "Penicillin"), ("seafood", "Sea food"), ("wheat", "Wheat"),
    ("other", "Other"),
]

DIETS = [
    ("vegetarian", "Vegetarian"), ("vegan", "Vegan"), ("halal", "Halal"), ("kosher", "Kosher"),
    ("gluten_free", "Gluten free"), ("lactose_free", "Lactose free"), ("other", "Other"),
]

ACCOMMODATION = [
    ("male", "Shared male"), ("female", "Shared female"),
    ("mixed", "Shared mixed"), ("single", "Single occupancy only"),
]

# Previous youth organisations, section 1d.
ORGANISATIONS = [
    ("atc", "Air Training Corps"), ("acf", "Army Cadet Force"),
    ("bbgg", "Boys Brigade or Girl Guides"), ("ccf", "Combined Cadet Force"),
    ("scc", "Sea Cadet Corps"), ("scouts", "Scouts, Air Scouts or Sea Scouts"),
    ("vcc", "Volunteer Cadet Corps"),
]

# Section 3 consent questions, in form order.
CONSENTS = [
    ("photo", "Images used to promote the RAF Air Cadets"),
    ("flyingLight", "Flying: British military light aircraft and gliders for air experience and instruction"),
    ("flyingSolo", "Flying: British military gliders and powered aircraft on their own as a solo pilot"),
    ("flyingPassenger", "Flying: British military passenger transport aircraft and helicopters"),
    ("flyingOther", "Flying: other types of British military aircraft including high performance jets"),
    ("marksmanship", "Initial Weapon Training and Live Firing Marksmanship Training"),
    ("strenuous", "Strenuous physical activities"),
    ("lowerRisk", "Lower risk events, without a separate consent form and health declaration each time"),
    ("medicalInform", "I will tell the unit about any relevant medical condition and complete a Health Declaration"),
    ("accommodation", "Being accommodated in the types I've chosen while attending authorised events"),
    ("transport", "Being transported to, from and during events by coach, minibus, or a vehicle driven by "
                  "adult volunteers, other parents, permanent staff or staff cadets over 18"),
    ("medicalTreatment", "The Officer in Charge acting for me if my child needs medical treatment "
                         "and I can't give consent in person"),
    ("shareContact", "Sharing my contact details through the Cadet Forces MIS and using them to contact me"),
]

STEPS = [
    ("cadet", "Cadet details"),
    ("contact", "Cadet contact details"),
    ("history", "Previous youth organisations"),
    ("parent", "Parent or guardian"),
    ("consent", "Consent"),
    ("medical", "Medical, allergies and diet"),
    ("about", "About joining"),
    ("sign", "Sign and submit"),
]

# Age (on the day of signing) from which the cadet signs the health declaration
# themselves, as printed on the 3822H.
CADET_SIGNS_FROM_AGE = 16
MAX_CONDITIONS = 10


def options() -> dict:
    """The option lists, as served to the frontends."""
    pairs = lambda rows: [{"key": k, "label": label} for k, label in rows]  # noqa: E731
    return {
        "ethnicities": [{"key": k, "label": label, "group": g} for k, label, g in ETHNICITIES],
        "conditions": pairs(CONDITIONS),
        "allergies": pairs(ALLERGIES),
        "diets": pairs(DIETS),
        "accommodation": pairs(ACCOMMODATION),
        "organisations": pairs(ORGANISATIONS),
        "consents": pairs(CONSENTS),
        "steps": pairs(STEPS),
        "cadetSignsFromAge": CADET_SIGNS_FROM_AGE,
    }


# ── Schema ────────────────────────────────────────────────────────────────────
# The public endpoints accept this from anyone holding a link, so every string
# and list is capped and unknown keys are refused.

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]

_keys = lambda rows: Literal[tuple(r[0] for r in rows)]  # noqa: E731
EthnicityKey = _keys(ETHNICITIES)
ConditionKey = _keys(CONDITIONS)
AllergyKey = _keys(ALLERGIES)
DietKey = _keys(DIETS)
AccommodationKey = _keys(ACCOMMODATION)
Severity = Literal["mild", "moderate", "severe"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Address(_Model):
    property: Short = ""
    street: Short = ""
    area: Short = ""
    town: Short = ""
    county: Short = ""
    country: Short = ""
    postcode: Short = ""


class Phones(Address):
    mobile: Short = ""
    homePhone: Short = ""
    email: Short = ""
    email2: Short = ""


class CadetDetails(_Model):
    fullName: Short = ""
    dob: date | None = None
    religion: Short = ""
    gender: Literal["male", "female", "pnts", "other"] | None = None
    genderOther: Short = ""
    pronoun: Literal["he", "she", "they"] | None = None
    ethnicity: EthnicityKey | None = None
    externalAgency: bool | None = None


class History(_Model):
    atc: bool | None = None
    acf: bool | None = None
    bbgg: bool | None = None
    ccf: bool | None = None
    scc: bool | None = None
    scouts: bool | None = None
    vcc: bool | None = None
    other: Short = ""


class Person(Phones):
    title: Short = ""
    fullName: Short = ""
    relationship: Short = ""


class NextOfKin(Person):
    hasParentalResponsibility: bool | None = None


class Consent(_Model):
    photo: bool | None = None
    flyingLight: bool | None = None
    flyingSolo: bool | None = None
    flyingPassenger: bool | None = None
    flyingOther: bool | None = None
    marksmanship: bool | None = None
    strenuous: bool | None = None
    lowerRisk: bool | None = None
    medicalInform: bool | None = None
    accommodation: bool | None = None
    accommodationTypes: list[AccommodationKey] = Field(default_factory=list, max_length=4)
    transport: bool | None = None
    medicalTreatment: bool | None = None
    shareContact: bool | None = None


class ConditionDetail(_Model):
    name: Short = ""
    severity: Severity | None = None
    soughtAdvice: bool | None = None
    routine: Long = ""
    strenuous: Long = ""
    control: Long = ""
    medication: Short = ""
    dosage: Short = ""
    storage: Short = ""


class Allergy(_Model):
    autoInjector: bool = False
    severity: Severity | None = None


class Medical(_Model):
    has: bool | None = None
    conditions: list[ConditionKey] = Field(default_factory=list, max_length=len(CONDITIONS))
    ehcPlan: bool | None = None
    ehcCopy: bool | None = None
    details: list[ConditionDetail] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    allergies: dict[AllergyKey, Allergy] = Field(default_factory=dict)
    allergyOther: Long = ""
    diet: list[DietKey] = Field(default_factory=list, max_length=len(DIETS))
    dietOther: Long = ""


class About(_Model):
    school: Short = ""
    heardFrom: Long = ""
    reason: Long = ""


class Signer(_Model):
    """Who is signing as the person with parental responsibility (section 8)."""
    forename: Short = ""
    surname: Short = ""


class RecruitFormData(_Model):
    cadet: CadetDetails = Field(default_factory=CadetDetails)
    contact: Phones = Field(default_factory=Phones)
    history: History = Field(default_factory=History)
    nok: NextOfKin = Field(default_factory=NextOfKin)
    extra: Person = Field(default_factory=Person)
    consent: Consent = Field(default_factory=Consent)
    medical: Medical = Field(default_factory=Medical)
    about: About = Field(default_factory=About)
    signer: Signer = Field(default_factory=Signer)


def parse(raw: dict | None) -> RecruitFormData:
    """Stored JSON back into the model. Stored data was validated on save."""
    return RecruitFormData.model_validate(raw or {})


# ── Completeness ──────────────────────────────────────────────────────────────

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def age_on(dob: date | None, day: date) -> int | None:
    if not dob:
        return None
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def cadet_signs_health_form(form: RecruitFormData, day: date) -> bool:
    """The 3822H is signed by the cadet at 16 or over, otherwise by the parent.
    Only asked for when there is a health declaration at all."""
    age = age_on(form.cadet.dob, day)
    return bool(form.medical.has) and age is not None and age >= CADET_SIGNS_FROM_AGE


def _address_missing(a: Phones, who: str) -> list[str]:
    out = []
    if not a.property or not a.street:
        out.append(f"{who} house name/number and street")
    if not a.town:
        out.append(f"{who} town")
    if not a.postcode:
        out.append(f"{who} postcode")
    if not a.mobile and not a.homePhone:
        out.append(f"{who} phone number")
    if not _EMAIL.match(a.email):
        out.append(f"{who} email address")
    return out


def missing(form: RecruitFormData) -> dict[str, list[str]]:
    """{step: [what's still needed]} for every step but "sign" (that is the
    signatures themselves, checked at submission). Empty list = step done."""
    c, m, consent = form.cadet, form.medical, form.consent

    cadet = []
    if not c.fullName:
        cadet.append("Full name")
    if not c.dob:
        cadet.append("Date of birth")
    if not c.gender or (c.gender == "other" and not c.genderOther):
        cadet.append("Gender")
    if not c.pronoun:
        cadet.append("Pronoun")
    if not c.ethnicity:
        cadet.append("Ethnicity")
    if c.externalAgency is None:
        cadet.append("Local authority involvement")

    history = [label for key, label in ORGANISATIONS if getattr(form.history, key) is None]

    parent = []
    if form.nok.hasParentalResponsibility is None:
        parent.append("Parental responsibility")
    for field, label in (("title", "Title"), ("fullName", "Full name"), ("relationship", "Relationship")):
        if not getattr(form.nok, field):
            parent.append(label)
    parent += _address_missing(form.nok, "Your")

    consents = [label for key, label in CONSENTS if getattr(consent, key) is None]
    if consent.accommodation and not consent.accommodationTypes:
        consents.append("Which accommodation types you consent to")

    medical = []
    if m.has is None:
        medical.append("Whether your child has any medical conditions, allergies or dietary needs")
    elif m.has:
        if not (m.conditions or m.allergies or m.diet):
            medical.append("At least one condition, allergy or dietary requirement")
        if m.ehcPlan is None:
            medical.append("Education, Health and Care Plan")
        elif m.ehcPlan and m.ehcCopy is None:
            medical.append("Whether you'll share the Education, Health and Care Plan")
        if m.conditions and not m.details:
            medical.append("Details of each condition")
        for i, d in enumerate(m.details, 1):
            if not d.name or not d.severity or d.soughtAdvice is None:
                medical.append(f"Condition {i}: name, severity and whether you've sought advice")
        if any(a.severity is None for a in m.allergies.values()):
            medical.append("Severity of each allergy")
        if "other" in m.allergies and not m.allergyOther:
            medical.append("Details of the other allergy")
        if "other" in m.diet and not m.dietOther:
            medical.append("Details of the other dietary requirement")

    about = [] if form.about.school else ["School"]

    return {
        "cadet": cadet,
        "contact": _address_missing(form.contact, "Cadet's"),
        "history": history,
        "parent": parent,
        "consent": consents,
        "medical": medical,
        "about": about,
    }


def progress(form: RecruitFormData, submitted: bool) -> dict:
    """Steps done out of the total, for the SMS list and the parent's page."""
    gaps = missing(form)
    steps = [
        {"key": key, "label": label,
         "done": submitted if key == "sign" else not gaps[key]}
        for key, label in STEPS
    ]
    return {"done": sum(s["done"] for s in steps), "total": len(steps), "steps": steps}
