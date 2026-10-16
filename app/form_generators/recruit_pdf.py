"""Draw a recruit's online joining form onto the official RAFAC 3822A and 3822H.

The forms are fillable PDFs, but their fields can't be trusted to fill
directly: two ethnicity boxes share a name with two others (ticking "Arab"
would tick "African" too), and the 3822H uses one "sought advice" field for
both conditions. So the field rectangles are read from the template only as
positions, the answers are drawn into them on an overlay, and the form fields
are dropped. The result is flat and can't be edited after the fact, which is
what a signed record should be, and copies of a page can be joined without
their field names colliding.

The templates are pinned in app/assets/recruit_forms. A new RAFAC version
needs its field names checking against the maps below.
"""

import io
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from functools import lru_cache

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from core.paths import RECRUIT_FORMS_DIR
from core.recruit_form import RecruitFormData, cadet_signs_health_form

TEMPLATE_A = RECRUIT_FORMS_DIR / "3822A.pdf"
TEMPLATE_H = RECRUIT_FORMS_DIR / "3822H.pdf"

FONT = "Helvetica"
BOLD = "Helvetica-Bold"

# Ethnicity key -> (field name, occurrence). Occurrences count in reading order
# (page, then top to bottom, then left to right): Ethnicity_11 and _12 each
# appear twice, once in the Black column and once under "Other ethnic group".
ETHNICITY_FIELDS = {
    "white_british": ("Ethnicity_1", 0), "white_irish": ("Ethnicity_2", 0),
    "gypsy": ("Ethnicity_3", 0), "white_other": ("Ethnicity_4", 0),
    "indian": ("Ethnicity_5", 0), "pakistani": ("Ethnicity_6", 0),
    "bangladeshi": ("Ethnicity_7", 0), "chinese": ("Ethnicity_8", 0),
    "asian_other": ("Ethnicity_10", 0),
    "african": ("Ethnicity_11", 0), "caribbean": ("Ethnicity_12", 0),
    "white_black_caribbean": ("Ethnicity_13", 0), "black_other": ("Ethnicity_14", 0),
    "white_black_african": ("Ethnicity_15", 0), "white_asian": ("Ethnicity_16", 0),
    "mixed_other": ("Ethnicity_17", 0),
    "arab": ("Ethnicity_11", 1), "other": ("Ethnicity_12", 1),
    "pnts": ("Ethnicity_PNTS", 0),
}

GENDER_FIELDS = {"male": "Gender_Male", "female": "Gender_Female", "pnts": "Gender_PNTS", "other": "Gender_Other"}
PRONOUN_FIELDS = {"he": "Pronoun_HeHim", "she": "Pronoun_SheHer", "they": "Pronoun_TheyThem"}
ORGANISATION_FIELDS = {"atc": "1d_ATC", "acf": "1d_ACF", "bbgg": "1d_BBGG", "ccf": "1d_CCF",
                       "scc": "1d_SCC", "scouts": "1d_SAS", "vcc": "1d_VCC"}
CONSENT_FIELDS = {
    "photo": "Photo", "flyingLight": "Flying_1", "flyingSolo": "Flying_2",
    "flyingPassenger": "Flying_3", "flyingOther": "Flying_4", "marksmanship": "LFMT",
    "strenuous": "Phys", "lowerRisk": "Lower_Risk", "medicalInform": "Medical_Inform",
    "accommodation": "Accom", "transport": "Transport", "medicalTreatment": "Medical_Treatment",
    "shareContact": "Contact",
}

# (property, street, area, town, county, country, postcode, mobile, home, email, email2)
_ADDRESS_KEYS = ("property", "street", "area", "town", "county", "country", "postcode",
                 "mobile", "homePhone", "email", "email2")
CADET_CONTACT_FIELDS = ("Property NameNumber", "Street", "Area", "Town", "County", "Country",
                        "Country_2",  # the postcode box, misnamed in the template
                        "Mobile Phone", "Home Phone", "Cadet Primary Email", "Cadet Secondary Email")
NOK_FIELDS = ("Property NameNumber_2", "Street_2", "Area_2", "Town_2", "County_2", "Country_3",
              "Post Code", "Mobile Phone_2", "Home Phone_3", "Primary Email", "Secondary Email_2")
EXTRA_FIELDS = ("Property NameNumber_3", "Street_3", "Area_3", "Town_3", "County_3", "Country_4",
                "Post Code_2", "Mobile Phone_3", "Home Phone_4", "Primary Email_2", "Secondary Email_3")

CONDITION_FIELDS = {
    "asthma": "Asthma", "back": "Back Injury", "concentration": "Concentration",
    "diabetes": "Diabeties", "epilepsy": "Epilepsy", "eyesight": "Eyesight",
    "fractures": "Fractures", "hearing": "Hearing", "objects": "Inability to move objects",
    "incontinence": "Incontinence", "learning": "Learning Difficulties",
    "dexterity": "Manual Dexterity", "memory": "Memory", "migraine": "Migraine",
    "mobility": "Mobility problems", "coordination": "Physical coordination",
    "rheumatic": "Rheumatic Fever", "speech": "Speech", "mental": "Mental Health",
    "other": "Section2Other",
}
# Allergy key -> the row's name prefix; "<prefix> - Auto" / " - Mild" etc. follow.
ALLERGY_FIELDS = {
    "animal": "Animal Skin / Hair", "dust": "Dust Mites", "hayfever": "Hayfever",
    "chemicals": "Household Chemicals", "insects": "Insect Stings", "lactose": "Lactose",
    "latex": "Latex", "medicines": "Medicines", "mould": "Mould", "nut": "Nut",
    "penicillin": "Penicillin", "seafood": "Sea Food", "wheat": "Wheat", "other": "Other",
}
# The template's own spellings, where they differ from "<prefix> - Auto".
ALLERGY_TICK = {"other": "Section3Other"}
ALLERGY_AUTO = {"medicines": "Medicines  - Auto"}
DIET_FIELDS = {"vegetarian": "Vegetarian", "vegan": "Vegan", "halal": "Halal", "kosher": "Kosher",
               "gluten_free": "Gluton Free", "lactose_free": "Lactose Free", "other": "Section4Other"}
H_OTHER_ALLERGY = "If you selected Other above please provide further informationOther"
H_OTHER_DIET = "If you selected Other above please provide further informationOther_2"

# Condition block n (0 or 1 on a page) -> its fields. The second block's names
# carry a "_2" suffix; "sought advice" is shared, so it goes by occurrence.
_COND_TEXT = {
    "name": "Name of Condition",
    "routine": "Please explain how your child is affected by the condition during normal routine activitiesFurther Details",
    "strenuous": "Please explain how your child is affected by the condition during strenuous activitiesFurther Details",
    "control": "Is your child able to control their condition without the need for further interventionFurther Details",
    "medication": "Name of MedicationMedication",
    "dosage": "Dosage  FrequencyMedication",
    "storage": "Storage RequirementsMedication",
}

# Things on the forms that have no field, placed from the template's own text
# (page index, x, baseline, font size). Re-measure if the template changes.
GENDER_OTHER_LABEL = (0, 137.5, 656.8, 8, "Other (please specify):  ")
ACCOMMODATION_LINE = (2, 124.9, 95.6, 7,
                      "I consent to my child being accommodated in ",
                      {"male": "shared male", "female": "shared female",
                       "mixed": "shared mixed", "single": "single occupancy"})


# Letters NFKD can't split into base + accent, so folding alone would lose them.
_NO_DECOMPOSITION = str.maketrans({"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Ħ": "H", "ħ": "h", "ı": "i"})


def _latin(text: str) -> str:
    """The standard PDF fonts only carry Latin-1-ish glyphs. Keep what they can
    draw (Zoë, Siân), fold accents that they can't, and never drop a whole name."""
    out = []
    for ch in (text or "").translate(_NO_DECOMPOSITION):
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            folded = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(folded or "?")
    return "".join(out)


def _ddmmyyyy(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


@lru_cache(maxsize=4)
def _widgets(path: str) -> dict[str, list[tuple[int, tuple[float, float, float, float]]]]:
    """{field name: [(page index, (x0, y0, x1, y1)), ...]} in reading order."""
    found = []
    for pi, page in enumerate(PdfReader(path).pages):
        for annot in page.get("/Annots") or []:
            a = annot.get_object()
            if a.get("/Subtype") != "/Widget":
                continue
            parent = a.get("/Parent")
            name = a.get("/T") or (parent.get_object().get("/T") if parent else None)
            r = [float(v) for v in a["/Rect"]]
            x0, x1 = sorted((r[0], r[2]))
            y0, y1 = sorted((r[1], r[3]))
            found.append((pi, -y1, x0, str(name), (x0, y0, x1, y1)))
    out: dict = defaultdict(list)
    for pi, _, _, name, rect in sorted(found):
        out[name].append((pi, rect))
    return dict(out)


def _ink_only(png: bytes):
    """The signature cropped to the ink. A signature pad's image is mostly
    blank margin; scaled whole into a 20pt box, the signature itself would be
    a few points high."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    box = img.getchannel("A").getbbox()
    if box:
        pad = 4
        img = img.crop((max(box[0] - pad, 0), max(box[1] - pad, 0),
                        min(box[2] + pad, img.width), min(box[3] + pad, img.height)))
    return img


class _Filler:
    """Collects what to draw where on one copy of a template, then renders it."""

    def __init__(self, template):
        self.path = str(template)
        self.widgets = _widgets(self.path)
        self.ops: dict[int, list] = defaultdict(list)

    def _at(self, name: str, idx: int = 0):
        return self.widgets[name][idx]

    def text(self, name: str, value, idx: int = 0):
        value = _latin(str(value or "")).strip()
        if not value:
            return
        page, (x0, y0, x1, y1) = self._at(name, idx)

        def draw(c):
            w, h = x1 - x0 - 4, y1 - y0
            # The largest size at which the text, wrapped, fits the box: two
            # readable lines beat one tiny one. Bottoms out small rather than
            # cutting anything off.
            size = min(9, h * 0.65)
            while True:
                lines = simpleSplit(value, FONT, size, w)
                if len(lines) * size * 1.15 <= h - 1 or size <= 4:
                    break
                size -= 0.5
            c.setFont(FONT, size)
            leading = size * 1.15
            top = y0 + (h + len(lines) * leading) / 2 - size
            for i, line in enumerate(lines):
                c.drawString(x0 + 2, top - i * leading, line)

        self.ops[page].append(draw)

    def tick(self, name: str, idx: int = 0):
        page, (x0, y0, x1, y1) = self._at(name, idx)

        def draw(c):
            size = min(10, (y1 - y0) * 0.85, (x1 - x0) * 0.95)
            c.setFont(BOLD, size)
            c.drawCentredString((x0 + x1) / 2, (y0 + y1) / 2 - size * 0.36, "X")

        self.ops[page].append(draw)

    def yes_no(self, prefix: str, value: bool | None, sep: str = "_"):
        if value is not None:
            self.tick(f"{prefix}{sep}{'Yes' if value else 'No'}")

    def image(self, name: str, png: bytes | None, idx: int = 0):
        if not png:
            return
        page, (x0, y0, x1, y1) = self._at(name, idx)
        img = ImageReader(_ink_only(png))

        def draw(c):
            c.drawImage(img, x0 + 1, y0 + 1, x1 - x0 - 2, y1 - y0 - 2,
                        preserveAspectRatio=True, anchor="sw", mask="auto")

        self.ops[page].append(draw)

    def draw(self, page: int, fn):
        self.ops[page].append(fn)

    def render(self, pages: list[int] | None = None) -> list:
        """The filled pages (all, or just ``pages``) as pypdf page objects."""
        writer = PdfWriter(clone_from=self.path)
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        for i, page in enumerate(writer.pages):
            c.setPageSize((float(page.mediabox.width), float(page.mediabox.height)))
            for fn in self.ops[i]:
                fn(c)
            c.showPage()
        c.save()
        overlay = PdfReader(buf)

        out = []
        for i, page in enumerate(writer.pages):
            if pages is not None and i not in pages:
                continue
            # Drop the form fields, keep the links (the privacy notice, etc.).
            kept = [a for a in page.get("/Annots") or [] if a.get_object().get("/Subtype") != "/Widget"]
            page[NameObject("/Annots")] = ArrayObject(kept)
            page.merge_page(overlay.pages[i])
            out.append(page)
        return out


def _address(f: _Filler, person, fields: tuple[str, ...]):
    for key, field in zip(_ADDRESS_KEYS, fields, strict=True):
        f.text(field, getattr(person, key))


def _strike_unchosen_accommodation(f: _Filler, chosen: list[str]):
    """The form says "(delete as appropriate)": strike through the types the
    parent didn't consent to, as they would with a pen."""
    page, x, y, size, prefix, words = ACCOMMODATION_LINE

    def draw(c):
        c.setLineWidth(0.8)
        cursor = x + stringWidth(prefix, BOLD, size)
        for key, word in words.items():
            width = stringWidth(word, BOLD, size)
            if key not in chosen:
                c.line(cursor - 0.5, y + size * 0.3, cursor + width + 0.5, y + size * 0.3)
            cursor += width + stringWidth("/", BOLD, size)

    f.draw(page, draw)


def build_3822a(form: RecruitFormData, signature: bytes | None, signed_on: date | None) -> list:
    f = _Filler(TEMPLATE_A)
    c = form.cadet
    f.text("Full Name of Child", c.fullName)
    f.text("DOB_af_date", _ddmmyyyy(c.dob))
    f.text("Religion", c.religion)
    if c.gender:
        f.tick(GENDER_FIELDS[c.gender])
    if c.gender == "other" and c.genderOther:
        page, x, y, size, label = GENDER_OTHER_LABEL
        other = _latin(c.genderOther)
        start = x + stringWidth(label, FONT, size)

        def gender_other(cv):
            # Shrink into the space left before the column edge.
            s = size
            while s > 5 and start + stringWidth(other, FONT, s) > 268:
                s -= 0.5
            cv.setFont(FONT, s)
            cv.drawString(start, y, other)

        f.draw(page, gender_other)
    if c.pronoun:
        f.tick(PRONOUN_FIELDS[c.pronoun])
    if c.ethnicity:
        f.tick(*ETHNICITY_FIELDS[c.ethnicity])
    f.yes_no("External_Agency", c.externalAgency)

    _address(f, form.contact, CADET_CONTACT_FIELDS)
    for key, prefix in ORGANISATION_FIELDS.items():
        f.yes_no(prefix, getattr(form.history, key))
    f.text("Other Youth Organisation", form.history.other)

    nok = form.nok
    f.yes_no("PR", nok.hasParentalResponsibility)
    f.text("Title", nok.title)
    f.text("Full Name", nok.fullName)
    f.text("Relationship", nok.relationship)
    _address(f, nok, NOK_FIELDS)

    extra = form.extra
    f.text("Additional_Contact_Title", extra.title)
    f.text("Full Name_2", extra.fullName)
    f.text("Relationship_2", extra.relationship)
    _address(f, extra, EXTRA_FIELDS)

    for key, prefix in CONSENT_FIELDS.items():
        f.yes_no(prefix, getattr(form.consent, key))
    if form.consent.accommodation:
        _strike_unchosen_accommodation(f, form.consent.accommodationTypes)
    f.yes_no("Medical_Allergies_Diet", form.medical.has)

    f.text("School", form.about.school)
    f.text("Where did you hear about the unit", form.about.heardFrom)
    f.text("Reason for joining", form.about.reason)

    f.text("Forename", form.signer.forename)
    f.text("Surname", form.signer.surname)
    f.image("Signature", signature)
    f.text("Date4_af_date", _ddmmyyyy(signed_on))
    return f.render()


def _condition_block(f: _Filler, slot: int, d):
    suffix = "_2" if slot else ""
    for key, field in _COND_TEXT.items():
        f.text(field + suffix, getattr(d, key))
    if d.severity:
        f.tick(f"Cond{slot + 1}{d.severity.capitalize()}")
    if d.soughtAdvice is not None:
        f.tick("Yes Sought Advice" if d.soughtAdvice else "No Not Sought Advice", slot)


def _child_header(f: _Filler, form: RecruitFormData):
    f.text("Full Name of Child", form.cadet.fullName)
    if form.cadet.dob:
        f.text("DD", f"{form.cadet.dob.day:02d}")
        f.text("MM", f"{form.cadet.dob.month:02d}")
        f.text("YYYY", form.cadet.dob.year)


def build_3822h(form: RecruitFormData, parent_signature: bytes | None,
                cadet_signature: bytes | None, signed_on: date | None) -> list:
    """The health declaration. Two conditions fit on the first page; the form
    says to use further copies for more, so each further pair gets another
    copy of page 1, placed before the allergies page."""
    m = form.medical
    f = _Filler(TEMPLATE_H)
    _child_header(f, form)
    for key in m.conditions:
        f.tick(CONDITION_FIELDS[key])
    f.yes_no("EHC", m.ehcPlan, " ")
    if m.ehcPlan:
        f.yes_no("EHC Provide Copy", m.ehcCopy, " ")
    for slot, d in enumerate(m.details[:2]):
        _condition_block(f, slot, d)

    for key, allergy in m.allergies.items():
        prefix = ALLERGY_FIELDS[key]
        f.tick(ALLERGY_TICK.get(key, prefix))
        if allergy.autoInjector:
            f.tick(ALLERGY_AUTO.get(key, f"{prefix} - Auto"))
        if allergy.severity:
            f.tick(f"{prefix} - {allergy.severity.capitalize()}")
    f.text(H_OTHER_ALLERGY, m.allergyOther)
    for key in m.diet:
        f.tick(DIET_FIELDS[key])
    f.text(H_OTHER_DIET, m.dietOther)

    if signed_on and cadet_signs_health_form(form, signed_on):
        names, sig, prefix = ("Forename_2", "Surname_2"), ("Cadet Signature", cadet_signature), "Cadet"
        forename, _, surname = form.cadet.fullName.partition(" ")
    else:
        names, sig, prefix = ("Forename", "Surname"), ("Parent Signature", parent_signature), "Parent"
        forename, surname = form.signer.forename, form.signer.surname
    f.text(names[0], forename)
    f.text(names[1], surname)
    f.image(*sig)
    if signed_on:
        f.text(f"{prefix}_DD", f"{signed_on.day:02d}")
        f.text(f"{prefix}_MM", f"{signed_on.month:02d}")
        f.text(f"{prefix}_YYYY", signed_on.year)

    first, *rest = f.render()
    extra = []
    for start in range(2, len(m.details), 2):
        more = _Filler(TEMPLATE_H)
        _child_header(more, form)
        for slot, d in enumerate(m.details[start:start + 2]):
            _condition_block(more, slot, d)
        extra += more.render([0])
    return [first, *extra, *rest]


def build_pdf(form: RecruitFormData, which: str, parent_signature: bytes | None,
              cadet_signature: bytes | None, submitted_at: datetime | None) -> bytes:
    """``which`` is "3822A", "3822H" or "both" (A then H, when there is an H)."""
    signed_on = submitted_at.date() if submitted_at else None
    pages = []
    if which in ("3822A", "both"):
        pages += build_3822a(form, parent_signature, signed_on)
    if which == "3822H" or (which == "both" and form.medical.has):
        pages += build_3822h(form, parent_signature, cadet_signature, signed_on)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page)
    writer.add_metadata({"/Title": f"RAFAC {which if which != 'both' else '3822A/H'} - {_latin(form.cadet.fullName)}"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
