"""Parade-night attendance reports: read them, and manage who gets them.

Staff can always read reports and manage the list. SNCOs and NCOs can read
them only while they are on the recipient list, so taking someone off the list
takes their access away too. Cadets and anyone else never can.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core import parade_report as pr
from core.attendance import ABSENT, AUTHORISED, PRESENT
from core.db import get_db, get_or_create_user
from core.emailer import EMAIL_RE
from core.security import _authenticate, get_user_role, require_staff, require_staff_or_nco
from database.models import ParadeReport, ParadeReportRecipient, ParadeReportSettings, User

router = APIRouter()


def _is_recipient(db: Session, email: str) -> bool:
    key = (email or "").strip().lower()
    return db.query(ParadeReportRecipient).filter(ParadeReportRecipient.email == key).first() is not None


def require_report_reader(authorization: str = Header(None), db: Session = Depends(get_db)) -> dict:
    """Staff, or an SNCO/NCO on the recipient list."""
    idinfo = _authenticate(authorization)
    role = get_user_role(idinfo["email"])
    if role == "staff":
        return {**idinfo, "role": role}
    if role in ("snco", "nco") and _is_recipient(db, idinfo["email"]):
        return {**idinfo, "role": role}
    raise HTTPException(status_code=403, detail="Only staff and the people on the parade report list can see these")


def _counts(report: ParadeReport) -> dict:
    counts = {PRESENT: 0, AUTHORISED: 0, ABSENT: 0}
    for entry in report.entries:
        counts[entry.state] += 1
    return counts


def _summary(report: ParadeReport) -> dict:
    return {
        "date": report.parade_date.isoformat(),
        "generatedAt": report.generated_at.isoformat(),
        "generatedBy": report.generated_by,
        "emailedAt": report.emailed_at.isoformat() if report.emailed_at else None,
        "counts": _counts(report),
    }


def _detail(report: ParadeReport, can_manage: bool) -> dict:
    return {
        **_summary(report),
        # Who it went to is for staff managing the list, not every reader.
        "emailedTo": (report.emailed_to or []) if can_manage else None,
        "cadets": [pr.entry_json(e) for e in report.entries],
    }


def _person(email: str, users: dict[str, User], role: str | None) -> dict:
    user = users.get(email)
    name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else ""
    return {"email": email, "name": name or None, "role": role}


def _settings_json(db: Session) -> dict:
    settings = pr.get_settings(db)
    users: dict[str, User] = {}
    for u in db.query(User).all():
        users.setdefault((u.email or "").lower(), u)
    listed = [r.email for r in db.query(ParadeReportRecipient).order_by(ParadeReportRecipient.email)]
    roles = {email: get_user_role(email) for email in set(users) | set(listed)}
    candidates = sorted(
        (e for e in users if e not in listed and roles[e] in pr.REPORT_ROLES),
        key=lambda e: (_person(e, users, None)["name"] or e).casefold(),
    )
    return {
        "enabled": bool(settings and settings.enabled),
        "runsAs": settings.user.email if settings and settings.user else None,
        "updatedBy": settings.updated_by if settings else None,
        "updatedAt": settings.updated_at.isoformat() if settings and settings.updated_at else None,
        "schedule": {"days": pr.RUN_DAYS.split(","), "hour": pr.RUN_HOUR, "minute": pr.RUN_MINUTE},
        "recipients": [_person(e, users, roles[e]) for e in listed],
        "candidates": [_person(e, users, roles[e]) for e in candidates],
    }


# ── Settings (staff) ──────────────────────────────────────────────────────────
# Declared before /parade-reports/{night} so "settings" is never read as a date.

@router.get("/parade-reports/settings")
def get_report_settings(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    return _settings_json(db)


class SettingsPut(BaseModel):
    enabled: bool


@router.put("/parade-reports/settings")
def put_report_settings(body: SettingsPut, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    user = get_or_create_user(db, idinfo)
    if body.enabled and not user.bader_credentials:
        raise HTTPException(
            status_code=400,
            detail="The report scrapes Bader with your credentials. Save them in Settings first.",
        )
    settings = pr.get_settings(db)
    if not settings:
        settings = ParadeReportSettings()
        db.add(settings)
    settings.enabled = body.enabled
    # Whoever switches it on is whose credentials it scrapes with, as with the
    # scraper schedules; switching it off leaves that alone.
    if body.enabled:
        settings.user_id = user.id
    settings.updated_by = user.email
    settings.updated_at = datetime.now()
    db.commit()
    return _settings_json(db)


class RecipientPost(BaseModel):
    email: str


@router.post("/parade-reports/recipients", status_code=201)
def add_recipient(body: RecipientPost, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter an email address")
    if not db.query(User).filter(User.email.ilike(email)).first():
        raise HTTPException(status_code=404, detail="No one has signed in to SMS with that email yet")
    if get_user_role(email) not in pr.REPORT_ROLES:
        raise HTTPException(status_code=400, detail="Only staff, SNCOs and NCOs can get the parade report")
    if _is_recipient(db, email):
        raise HTTPException(status_code=409, detail="They're already on the list")
    db.add(ParadeReportRecipient(email=email, added_by=idinfo.get("email"), added_at=datetime.now()))
    db.commit()
    return _settings_json(db)


@router.delete("/parade-reports/recipients/{email}")
def remove_recipient(email: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    row = db.query(ParadeReportRecipient).filter(ParadeReportRecipient.email == email.strip().lower()).first()
    if not row:
        raise HTTPException(status_code=404, detail="They're not on the list")
    db.delete(row)
    db.commit()
    return _settings_json(db)


class BuildPost(BaseModel):
    date: date
    email: bool = False


@router.post("/parade-reports")
def build_report_now(body: BuildPost, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Build (or rebuild) a night's report from the attendance already scraped,
    for a night the 21:30 run missed or got wrong. It doesn't scrape: run the
    cadet scraper first if Bader has changed since."""
    if body.date > date.today():
        raise HTTPException(status_code=400, detail="That night hasn't happened yet")
    report = pr.build_report(db, body.date, idinfo.get("email") or "staff")
    if report is None:
        raise HTTPException(status_code=404, detail="No parade night register for that date")
    if body.email:
        pr.email_report(db, report)
    return _detail(report, can_manage=True)


# ── Reports (staff and listed NCOs) ───────────────────────────────────────────

@router.get("/parade-reports")
def list_reports(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)):
    """Every report, newest first. An SNCO or NCO who isn't on the list gets
    `onList: false` and no reports rather than a 403: the page links from
    their menu, and "ask staff to add you" is an answer, not an error."""
    role = get_user_role(idinfo["email"])
    if role != "staff" and not _is_recipient(db, idinfo["email"]):
        return {"canManage": False, "onList": False, "reports": []}
    reports = db.query(ParadeReport).order_by(ParadeReport.parade_date.desc()).all()
    return {"canManage": role == "staff", "onList": True, "reports": [_summary(r) for r in reports]}


@router.get("/parade-reports/{night}")
def get_report(night: date, db: Session = Depends(get_db), reader: dict = Depends(require_report_reader)):
    report = db.query(ParadeReport).filter(ParadeReport.parade_date == night).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report for that night")
    return _detail(report, can_manage=reader["role"] == "staff")
