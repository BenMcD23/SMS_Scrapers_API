"""The two appointment processes staff walk applicants through, step by step.

- **Staff cadet** (PI501): a cadet nearing 18 who wants to keep serving. If it
  isn't finished by their 18th birthday they're suspended until it is, so the
  list of cadets to start it for is everyone 17½ and over.
- **Uniformed adult staff**: an adult applying as an SNCO or an officer (CFC).
  Only CFC applicants sit the Region board (step 3).

The steps are Greater Manchester Wing's published checklists, kept here once
so the API (which steps exist, when an application is complete) and the site
(their wording and form links) can never disagree. The forms themselves stay on
paper: each step that needs one links to the Wing's copy to print.

Each step a staff member ticks is stored as ``{"at": iso, "by": email}`` under
its key, so the page can say who did what and when.
"""

from dataclasses import dataclass
from datetime import date

STAFF_CADET = "staff_cadet"
UNIFORMED = "uniformed"
KINDS = (STAFF_CADET, UNIFORMED)

# Uniformed adult routes. Only CFC (officer) applicants go to the Region board.
SNCO = "snco"
CFC = "cfc"
ROUTES = (SNCO, CFC)

WHQ_EMAIL = "rafac-whq-allusers-gtrmanchester@mod.gov.uk"
QM_EMAIL = "wso8.gtrmcr@rafac.mod.gov.uk"

_SP = "https://rafac.sharepoint.com/sites/interim"
BPSS_FORM = f"{_SP}/QM/Controlled%20Documents/Pers%20Form%201-01.docx?web=1"
DBS_REQUEST = f"{_SP}/NorthRegion/GM/Lists/DBS%20Request/NewForm.aspx"
HR_FORM_239 = (
    "https://rafac.sharepoint.com/:w:/r/sites/interim/QM/Controlled%20Documents/"
    "Pers%20Form%20HR%20Form%20239.docx?d=w5e3f0fc2011140a084da65a37536aff5&csf=1&web=1&e=gMtanx"
)
PERS_5_01_PAGE_2 = (
    f"{_SP}/NorthRegion/GM/SiteAssets/SitePages/Staff-Cadet-Application-Process/"
    "Pers-Form-5-01-Page-2.pdf?web=1"
)
PERS_5_01A = (
    "https://rafac.sharepoint.com/:w:/r/sites/interim/QM/Controlled%20Documents/"
    "Pers%20Form%205-01A.docx?d=w091a342cd1b644be94707c511dba4003&csf=1&web=1&e=XuGmIM"
)
E_PERS_5_01 = (
    "https://rafac.sharepoint.com/:l:/s/interim/NorthRegion/GM/"
    "JADtnWL6CtvqQZNBPaS8vA_xAakIfz43xBWOOfSPUbCpAwE?nav=OWZmNTE2NGEtODVhYS00NzdlLTlkYWQtNDQ3M2NlZTA5ZDA3"
)
STAFF_CADET_SYSTEM = f"{_SP}/NorthRegion/GM/Lists/Cadet%20Application%20System"
PERS_1_10 = (
    "https://rafac.sharepoint.com/:w:/r/sites/interim/QM/_layouts/15/Doc.aspx?"
    "sourcedoc=%7bAC85D524-6E73-4E83-8B24-FAFE4E10C56F%7d&file=Pers%20Form%201-10.docx"
    "&action=default&mobileredirect=true&web=1"
)
OC_NARRATIVE = (
    f"{_SP}/NorthRegion/GM/SiteAssets/SitePages/Uniformed_Service_Application/"
    "OCs-Narrative%20Gerber%201873.docx?web=1"
)
HMRC_STARTER_CHECKLIST = "https://www.gov.uk/government/publications/paye-starter-checklist"


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    hint: str | None = None
    links: tuple[Link, ...] = ()


@dataclass(frozen=True)
class Stage:
    key: str
    title: str
    steps: tuple[Step, ...]
    # Uniformed routes this stage applies to; empty means every route.
    routes: tuple[str, ...] = ()
    note: str | None = None


def _mail(address: str) -> Link:
    return Link(address, f"mailto:{address}")


STAFF_CADET_STAGES = (
    Stage("dbs_bpss", "Stage 1 – DBS and BPSS", (
        Step("bpss", "BPSS form completed, wet-signed and emailed to WHQ as a PDF",
             "The squadron verifies ID and completes Part 6. Include two references. "
             "Every signature must be wet-signed.",
             (Link("BPSS form (Pers Form 1-01)", BPSS_FORM), _mail(WHQ_EMAIL))),
        Step("dbs_requested", "DBS requested with the cadet's full name and personal email",
             None, (Link("Request DBS", DBS_REQUEST),)),
        Step("dbs_id", "Cadet has completed the DBS ID checks",
             "The cadet gets an email from the Disclosure Submission Portal, uploads ID on the MoD "
             "Screening Platform (passport, driving licence, NI number, 5 years of addresses), then "
             "verifies with uCheck using the Yoti or Post Office EasyID app. Best done with squadron "
             "staff on a computer and a smartphone. Without digital ID: a face-to-face check with "
             "HR Form 239 and 3 forms of ID.",
             (Link("HR Form 239a", HR_FORM_239),)),
    )),
    Stage("application", "Stage 2 – Application", (
        Step("form_5_01_p2", "Cadet completes Pers Form 5-01 page 2", None,
             (Link("Pers Form 5-01 page 2", PERS_5_01_PAGE_2),)),
        Step("form_5_01_p2_uploaded", "Sqn Cdr uploads Pers Form 5-01 page 2 to the cadet's SMS profile under Files"),
        Step("form_5_01a", "Cadet and Sqn Cdr complete Pers Form 5-01A (Service Agreement)", None,
             (Link("Pers Form 5-01A", PERS_5_01A),)),
        Step("form_5_01a_uploaded", "Sqn Cdr uploads Pers Form 5-01A to SMS",
             "General Qualifications › Add Qualification › Miscellaneous › Staff Cadet Service Agreement."),
        Step("code_of_conduct", "Cadet reads and e-signs the Cadet Code of Conduct on Cadet Portal"),
        Step("safeguarding", "Cadet completes safeguarding training on Bader Learn"),
        Step("moi", "Cadet passes MOI training and assessment, updated on SMS"),
        Step("e_form_5_01", "Sqn Cdr completes the e-Pers Form 5-01",
             "Check everything above is done first: MOI, enhanced DBS, safeguarding, BPSS, Code of "
             "Conduct, and 5-01 page 2 and 5-01A uploaded.",
             (Link("e-Pers Form 5-01", E_PERS_5_01), Link("Staff Cadet Application System", STAFF_CADET_SYSTEM))),
    )),
    Stage("appointment", "Stage 3 – Appointment", (
        Step("sector_review", "Sector Commander has reviewed the application for compliance"),
        Step("wing_approval", "OC / Dep OC Wing has given final approval"),
        Step("approval_email", "Approval email received by the Sqn Cdr, Sector Commander and WHQ"),
        Step("rank_slides", "OC has requested Staff Cadet rank slides from the QM", None, (_mail(QM_EMAIL),)),
    )),
)

NOT_RECOMMENDED = ("If not recommended, the Development Officer contacts the applicant to go "
                   "through the feedback and agree a deferral period.")

UNIFORMED_STAGES = (
    Stage("application", "Step 1 – Application", (
        Step("pers_1_10", "Applicant completes Pers Form 1-10 (Adult Volunteers Application Form)", None,
             (Link("Pers Form 1-10", PERS_1_10),)),
        Step("hmrc", "Applicant completes the HMRC Starter Checklist", None,
             (Link("HMRC Starter Checklist", HMRC_STARTER_CHECKLIST),)),
        Step("oc_narrative", "Sqn OC completes the OC Narrative recommending the applicant", None,
             (Link("OC Narrative", OC_NARRATIVE),)),
        Step("emailed", "Sqn OC scans and emails the documents to the Develop.gtrmcr account"),
    )),
    Stage("wing", "Step 2 – Wing interview", (
        Step("wing_arranged", "Development Officer has arranged the Wing board with the applicant"),
        Step("wing_board", "Wing board held and the board report sent to WHQ"),
        Step("wing_ratified", "OC GM Wing has ratified the recommendation for appointment"),
    ), note=NOT_RECOMMENDED),
    Stage("region", "Step 3 – Region interview", (
        Step("region_forwarded", "WHQ has forwarded the application pack to RHQ"),
        Step("region_arranged", "Development Officer has arranged the Region board with the applicant"),
        Step("region_board", "Region board held and the board report sent to RHQ"),
        Step("region_ratified", "RC North has ratified the recommendation for appointment"),
    ), routes=(CFC,), note=NOT_RECOMMENDED),
    Stage("appointment", "Step 4 – Appointment", (
        Step("hq_forwarded", "WHQ / RHQ has forwarded the application pack to HQ RAFAC"),
        Step("jpa", "HQ RAFAC has made the appointment on JPA", "It then cascades into Units and SMS."),
    )),
    Stage("training", "Step 5 – Mandatory training", (
        Step("cic", "Combined Initial Course (CIC) completed at RAF Cranwell",
             "Must be done within 12 months of appointment."),
    )),
)

# The step whose tick starts the 12-month clock for CIC.
APPOINTED_STEP = "jpa"
CIC_MONTHS = 12


def stages_for(kind: str, route: str | None = None) -> tuple[Stage, ...]:
    """The stages an application of this kind (and route) goes through."""
    if kind == STAFF_CADET:
        return STAFF_CADET_STAGES
    return tuple(s for s in UNIFORMED_STAGES if not s.routes or route in s.routes)


def step_keys(kind: str, route: str | None = None) -> list[str]:
    return [step.key for stage in stages_for(kind, route) for step in stage.steps]


def all_step_keys(kind: str) -> set[str]:
    """Every key the kind has on any route — a step from the other route is
    "doesn't apply", not "doesn't exist"."""
    stages = STAFF_CADET_STAGES if kind == STAFF_CADET else UNIFORMED_STAGES
    return {step.key for stage in stages for step in stage.steps}


def progress(kind: str, route: str | None, steps: dict | None) -> dict:
    """How far along it is. Ticks for steps off the current route (a CFC
    applicant switched to SNCO) are kept but don't count."""
    keys = step_keys(kind, route)
    ticked = steps or {}
    done = [k for k in keys if k in ticked]
    next_step = next((k for k in keys if k not in ticked), None)
    return {"done": len(done), "total": len(keys), "complete": next_step is None, "nextStep": next_step}


def serialise_stages(kind: str) -> list[dict]:
    """Every stage of the kind, with the routes it applies to, for the site to render."""
    stages = STAFF_CADET_STAGES if kind == STAFF_CADET else UNIFORMED_STAGES
    return [{
        "key": stage.key,
        "title": stage.title,
        "routes": list(stage.routes),
        "note": stage.note,
        "steps": [{
            "key": step.key,
            "label": step.label,
            "hint": step.hint,
            "links": [{"label": link.label, "url": link.url} for link in step.links],
        } for step in stage.steps],
    } for stage in stages]


# ── Ages ─────────────────────────────────────────────────────────────────────

def add_years(d: date, years: int) -> date:
    """Same day `years` later; 29 February lands on 1 March, as a UK birthday does."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, month=3, day=1)


def add_months(d: date, months: int) -> date:
    """Same day `months` later, clamped to the end of a shorter month."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def eighteenth_birthday(dob: date) -> date:
    return add_years(dob, 18)


def is_nearing_18(dob: date | None, today: date) -> bool:
    """17½ or over: the age the Wing says to start, leaving time to finish."""
    return dob is not None and add_months(dob, 17 * 12 + 6) <= today
