"""The parade report's build, email and 21:30 job (core/parade_report.py)."""

from datetime import date, datetime

import pytest

import core.parade_report as pr
import routers.scrapers as scrapers
from database.models import (
    BaderCredentials,
    Cadet,
    CadetAttendance,
    ParadeReport,
    ParadeReportRecipient,
    ParadeReportSettings,
    Staff,
    StaffAttendance,
    User,
)

WED = date(2026, 11, 25)
DOMAIN = "317atc.co.uk"


def _register(db, night=WED, register_type="Parade Night", marks=None):
    """Cadets 1-4 on one night's register; returns nothing, rows are committed."""
    marks = marks or {
        1: ("Ann", "Able", "Cpl", "A", "Present Correctly Dressed"),
        2: ("Bob", "Brown", "Cadet", "B", "Absent"),
        3: ("Cat", "Cole", "Cadet", "C", "Authorised Absence"),
        4: ("Dan", "Dyer", "Cadet", "A", "Present Incorrectly Dressed"),
    }
    for cin, (first, last, rank, flight, status) in marks.items():
        if not db.get(Cadet, cin):
            db.add(Cadet(cin=cin, first_name=first, last_name=last, rank=rank, flight=flight))
        db.add(CadetAttendance(cadet_id=cin, date=datetime(night.year, night.month, night.day, 19, 0),
                               register_type=register_type, status=status))
    db.commit()


def test_a_report_holds_the_cadet_register_absent_first(db):
    _register(db)
    report = pr.build_report(db, WED, "schedule")
    assert [(e.name, e.state) for e in report.entries] == [
        ("Bob Brown", "absent"), ("Cat Cole", "authorised"), ("Ann Able", "present"), ("Dan Dyer", "present"),
    ]
    assert report.entries[1].status == "Authorised Absence"  # Bader's wording is kept
    assert (report.entries[0].rank, report.entries[0].flight) == ("Cadet", "B")


def test_staff_on_the_same_night_never_appear_in_it(db):
    _register(db)
    db.add(Staff(cin=900, first_name="Sue", last_name="Staff", rank="Sgt"))
    db.add(StaffAttendance(staff_id=900, date=datetime(2026, 11, 25, 19), register_type="Parade Night",
                           status="Absent"))
    db.commit()
    report = pr.build_report(db, WED, "schedule")
    assert "Sue Staff" not in [e.name for e in report.entries]
    assert len(report.entries) == 4


def test_no_parade_register_that_night_means_no_report(db):
    # Another night's parade and a camp the same day are not tonight's parade.
    _register(db, night=date(2026, 11, 20))
    _register(db, register_type="Camp")
    assert pr.build_report(db, WED, "schedule") is None
    assert db.query(ParadeReport).count() == 0


def test_a_register_nobody_has_marked_yet_is_not_reported_as_everyone_absent(db):
    _register(db, marks={1: ("Ann", "Able", "Cpl", "A", None), 2: ("Bob", "Brown", "Cadet", "B", "  ")})
    assert pr.build_report(db, WED, "schedule") is None


def test_rebuilding_a_night_replaces_its_report_rather_than_adding_a_second(db):
    _register(db)
    pr.build_report(db, WED, "schedule")
    db.query(CadetAttendance).filter(CadetAttendance.cadet_id == 2).update({"status": "Present Correctly Dressed"})
    db.commit()
    report = pr.build_report(db, WED, "staff@x")
    assert db.query(ParadeReport).count() == 1
    assert [e.state for e in report.entries].count("absent") == 0
    assert report.generated_by == "staff@x"


def test_a_cadet_listed_twice_is_counted_once_with_the_mark_that_shows_they_came(db):
    _register(db)
    db.add(CadetAttendance(cadet_id=1, date=datetime(2026, 11, 25, 20), register_type="Parade Night",
                           status="Absent"))
    db.commit()
    report = pr.build_report(db, WED, "schedule")
    ann = [e for e in report.entries if e.cadet_cin == 1]
    assert len(ann) == 1 and ann[0].state == "present"


def test_a_saved_report_keeps_a_cadet_who_has_since_left(db):
    _register(db)
    pr.build_report(db, WED, "schedule")
    db.delete(db.get(Cadet, 2))
    db.commit()
    db.expire_all()
    report = db.query(ParadeReport).one()
    assert "Bob Brown" in [e.name for e in report.entries]


# ── Email ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def listed(db, auth):
    """staff@ and nco@ on the list, plus cadet@, who isn't an NCO."""
    for persona in ("staff", "nco", "cadet"):
        db.add(ParadeReportRecipient(email=f"{persona}@{DOMAIN}", added_at=datetime.now()))
    db.commit()


def test_the_email_lists_every_cadet_by_section_and_goes_to_each_listed_nco_or_staff(db, listed, outbox):
    _register(db)
    report = pr.build_report(db, WED, "schedule")
    sent = pr.email_report(db, report)

    # The cadet on the list doesn't get cadets' names, even though they were added.
    assert sorted(m["to"] for m in outbox) == [f"nco@{DOMAIN}", f"staff@{DOMAIN}"]
    assert sorted(sent) == sorted(report.emailed_to)
    assert report.emailed_at is not None
    mail = outbox[0]
    assert mail["subject"] == "Parade night attendance: Wednesday 25 November 2026"
    html = mail["html"]
    for heading in ("Absent (1)", "Authorised absence (1)", "Present (2)"):
        assert heading in html
    for name in ("Bob Brown", "Cat Cole", "Ann Able", "Dan Dyer"):
        assert name in html
    assert html.index("Bob Brown") < html.index("Cat Cole") < html.index("Ann Able")
    assert "/attendance/reports/2026-11-25" in html


def test_scraped_names_are_escaped_in_the_email(db, listed, outbox):
    _register(db, marks={1: ("<b>Ann</b>", "Able", "Cpl", "A", "Absent")})
    pr.email_report(db, pr.build_report(db, WED, "schedule"))
    assert "<b>Ann</b>" not in outbox[0]["html"] and "&lt;b&gt;Ann&lt;/b&gt;" in outbox[0]["html"]


def test_an_empty_list_sends_nothing_and_leaves_the_report_unsent(db, auth, outbox):
    _register(db)
    report = pr.build_report(db, WED, "schedule")
    assert pr.email_report(db, report) == []
    assert outbox == [] and report.emailed_at is None


def test_one_failed_send_still_reaches_the_rest_of_the_list(db, listed, monkeypatch):
    _register(db)
    report = pr.build_report(db, WED, "schedule")
    got = []

    def flaky(to, subject, html, **kw):
        if to.startswith("nco@"):
            raise RuntimeError("Gmail said no")
        got.append(to)

    monkeypatch.setattr(pr, "send_email", flaky)
    assert pr.email_report(db, report) == [f"staff@{DOMAIN}"]
    assert got == [f"staff@{DOMAIN}"] and report.emailed_to == [f"staff@{DOMAIN}"]


# ── The 21:30 job ─────────────────────────────────────────────────────────────

@pytest.fixture
def switched_on(db, listed):
    user = User(google_id="sub-staff", email=f"staff@{DOMAIN}")
    db.add(user)
    db.flush()
    db.add(BaderCredentials(user_id=user.id, role_username="u", role_password="p"))
    db.add(ParadeReportSettings(enabled=True, user_id=user.id))
    db.commit()


@pytest.fixture
def scrape(monkeypatch):
    """Fakes the cadet scrape: records who ran it and returns `result`."""
    calls = []

    def fake(name, user_id, email, started_by):
        calls.append((name, email, started_by))
        return scrape.result

    scrape.result = True
    scrape.calls = calls
    monkeypatch.setattr(scrapers, "run_scraper_and_wait", fake)
    return scrape


def test_the_job_scrapes_then_saves_and_emails_tonights_report(db, switched_on, scrape, outbox):
    _register(db)
    pr.run_parade_report(today=WED)
    assert scrape.calls == [("cadet-quali", f"staff@{DOMAIN}", f"parade report (staff@{DOMAIN})")]
    db.expire_all()
    report = db.query(ParadeReport).one()
    assert report.generated_by == "schedule" and report.emailed_at is not None
    assert len(outbox) == 2


def test_the_job_does_nothing_while_switched_off(db, switched_on, scrape, outbox):
    db.query(ParadeReportSettings).update({"enabled": False})
    db.commit()
    _register(db)
    pr.run_parade_report(today=WED)
    assert scrape.calls == [] and outbox == [] and db.query(ParadeReport).count() == 0


def test_the_job_does_nothing_before_anyone_has_set_it_up(db, scrape, outbox):
    pr.run_parade_report(today=WED)
    assert scrape.calls == [] and outbox == []


def test_the_job_skips_when_the_person_who_switched_it_on_has_no_bader_login(db, switched_on, scrape, outbox):
    db.query(BaderCredentials).delete()
    db.commit()
    pr.run_parade_report(today=WED)
    assert scrape.calls == [] and outbox == []


def test_a_failed_scrape_sends_no_report_rather_than_a_half_updated_one(db, switched_on, scrape, outbox):
    _register(db)
    scrape.result = False
    pr.run_parade_report(today=WED)
    assert outbox == [] and db.query(ParadeReport).count() == 0


def test_a_night_with_no_parade_sends_nothing(db, switched_on, scrape, outbox):
    pr.run_parade_report(today=WED)
    assert len(scrape.calls) == 1
    assert outbox == [] and db.query(ParadeReport).count() == 0


def test_night_labels_read_as_a_date_people_say():
    assert pr.night_label(date(2026, 11, 27)) == "Friday 27 November 2026"
