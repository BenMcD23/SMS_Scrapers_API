"""Self-check for NCO appraisals — the schedule, the autofill and the documents.

Run: PYTHONPATH=app:. python -m routers.test_nco_appraisals

Covers the rules that make this feature what it is: the next-review choice is
what schedules the follow-up, an appraisal always wins over a reminder for the
same NCO, the header block is snapshotted rather than recomputed, and both
documents render from the saved row.
"""
import asyncio
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import Cadet, CadetAttendance, NcoAppraisal
import routers.nco_appraisals as na
from core.db import get_or_create_user


def _idinfo(name: str) -> dict:
    return {"sub": name, "email": f"{name}@317atc.co.uk", "given_name": name,
            "family_name": "Staff"}


def _day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).date().isoformat()


def _body(cadet_id: int, **overrides) -> na.AppraisalBody:
    return na.AppraisalBody(cadet_id=cadet_id, **overrides)


def _streamed(response) -> bytes:
    """Drain a StreamingResponse. Starlette hands a plain file object off to a
    threadpool, so the body only comes back through the async iterator."""
    async def collect():
        return b"".join([chunk async for chunk in response.body_iterator])
    return asyncio.run(collect())


def test_add_months():
    # Clamped to the end of the target month rather than rolling into the next.
    assert na.add_months(datetime(2026, 1, 31), 1).date().isoformat() == "2026-02-28"
    assert na.add_months(datetime(2024, 2, 29), 12).date().isoformat() == "2025-02-28"
    # Ordinary cases, including the year rollover the 12-month option always hits.
    assert na.add_months(datetime(2026, 8, 8), 3).date().isoformat() == "2026-11-08"
    assert na.add_months(datetime(2026, 8, 8), 6).date().isoformat() == "2027-02-08"
    assert na.add_months(datetime(2026, 12, 31), 12).date().isoformat() == "2027-12-31"
    print("add_months self-check passed")


def test_is_nco():
    def cadet(rank, flight):
        return Cadet(cin=1, first_name="A", last_name="B", rank=rank, flight=flight)

    # Either marker is enough, because rank and flight don't always move together.
    assert na.is_nco(cadet("Cpl", "A"))
    assert na.is_nco(cadet("Cadet", "NCO"))
    assert na.is_nco(cadet(None, "nco"))          # flight match is case-insensitive
    assert all(na.is_nco(cadet(r, "A")) for r in ("Sgt", "FS", "CWO"))
    # Plain cadets aren't in the NCO team, and neither is a blank record.
    assert not na.is_nco(cadet("Cadet", "B"))
    assert not na.is_nco(cadet(None, None))
    print("is_nco self-check passed")


def test_attendance_summary():
    class Record:
        def __init__(self, date, status):
            self.date, self.status = date, status

    now = datetime.now()
    old = now - timedelta(days=400)
    records = (
        [Record(now, "Present Correctly Dressed")] * 17
        + [Record(now, "Absent")] * 3
        # Authorised absences are excluded entirely, so they move neither the
        # percentage nor the "of N nights" denominator.
        + [Record(now, "Authorised Absence")] * 5
        # Anything older than the window is ignored.
        + [Record(old, "Absent")] * 50
    )
    assert na.attendance_summary(records) == "85% (17/20 nights, last 12 months)"
    # Nothing to divide by reads as blank rather than "0%", which would be a lie
    # about an NCO with no scraped register yet.
    assert na.attendance_summary([]) == ""
    assert na.attendance_summary([Record(now, "Authorised Absence")]) == ""
    print("attendance_summary self-check passed")


def test_age_on():
    cadet = Cadet(cin=1, first_name="A", last_name="B", date_of_birth=datetime(2009, 9, 1))
    assert na.age_on(cadet, datetime(2026, 8, 8).date()) == "16"   # birthday not yet reached
    assert na.age_on(cadet, datetime(2026, 9, 1).date()) == "17"   # on the day itself
    # No date of birth on record leaves the box blank rather than guessing.
    assert na.age_on(Cadet(cin=2, first_name="A", last_name="B"), datetime(2026, 8, 8).date()) == ""
    print("age_on self-check passed")


def test():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    staff = _idinfo("staff")
    get_or_create_user(db, staff)

    now = datetime.now()
    smith = Cadet(cin=101, first_name="Sam", last_name="Smith", rank="Cpl", flight="NCO",
                  date_of_birth=datetime(2009, 1, 5), email="sam.smith@317atc.co.uk")
    jones = Cadet(cin=102, first_name="Jo", last_name="Jones", rank="Sgt", flight="NCO",
                  date_of_birth=datetime(2008, 1, 5))
    plain = Cadet(cin=103, first_name="Pat", last_name="Plain", rank="Cadet", flight="A")
    db.add_all([smith, jones, plain])
    for _ in range(9):
        db.add(CadetAttendance(cadet_id=smith.cin, date=now - timedelta(days=7),
                               status="Present Correctly Dressed"))
    db.add(CadetAttendance(cadet_id=smith.cin, date=now - timedelta(days=14), status="Absent"))
    db.commit()

    # ── the overview only carries the NCO team ───────────────────────────────
    overview = na.list_appraisals(db, staff)
    assert [n["cin"] for n in overview["ncos"]] == [jones.cin, smith.cin]  # surname order
    assert overview["upcoming"] == []      # nothing appraised, nothing reminded
    assert set(overview["unscheduled"]) == {smith.cin, jones.cin}

    # Age and attendance are filled in from the record, ready for the form.
    row = next(n for n in overview["ncos"] if n["cin"] == smith.cin)
    assert row["nco_name"] == "Cpl Sam Smith"
    assert row["attendance"] == "90% (9/10 nights, last 12 months)"
    assert row["age"] == na.age_on(smith, now.date())

    # ── a reminder puts an un-appraised NCO on the upcoming list ─────────────
    reminder = na.upsert_reminder(
        na.ReminderBody(cadet_id=jones.cin, due_date=_day(10), note="First one"), db, staff,
    )
    assert reminder["nco_name"] == "Sgt Jo Jones"
    # A second reminder moves the date rather than stacking up a duplicate.
    moved = na.upsert_reminder(
        na.ReminderBody(cadet_id=jones.cin, due_date=_day(20)), db, staff,
    )
    assert moved["id"] == reminder["id"] and moved["due_date"][:10] == _day(20)

    overview = na.list_appraisals(db, staff)
    assert len(overview["upcoming"]) == 1
    assert overview["upcoming"][0]["source"] == "reminder"
    assert overview["unscheduled"] == [smith.cin]

    # Only NCOs can be appraised or reminded about.
    for call in (
        lambda: na.upsert_reminder(na.ReminderBody(cadet_id=plain.cin, due_date=_day(5)), db, staff),
        lambda: na.create_appraisal(_body(plain.cin), db, staff),
    ):
        try:
            call()
            raise AssertionError("expected a plain cadet to be rejected")
        except HTTPException as e:
            assert e.status_code == 400

    # ── writing an appraisal ─────────────────────────────────────────────────
    appraisal = na.create_appraisal(
        _body(
            smith.cin,
            appraisal_date="2026-08-08",
            general_observations="A valued member of the team.",
            strengths="Reliable - always there.",
            targets="Lead a session alone.\nAttend a camp.",
            next_review_months=6,
            cause_for_concern=True,
        ),
        db, staff,
    )
    # The interval is what schedules the follow-up — the date is derived, never
    # typed, so the stored date and the printed line can't disagree.
    assert appraisal["next_review_date"][:10] == "2027-02-08"
    assert appraisal["cause_for_concern"] is True
    assert appraisal["author_name"] == "staff Staff"
    # Header block snapshotted from the record because the form left it blank.
    assert appraisal["nco_name"] == "Cpl Sam Smith"
    assert appraisal["attendance"] == "90% (9/10 nights, last 12 months)"

    # An out-of-range interval is refused rather than silently coerced.
    try:
        na.create_appraisal(_body(smith.cin, next_review_months=9), db, staff)
        raise AssertionError("expected 9 months to be rejected")
    except HTTPException as e:
        assert e.status_code == 400

    # ── an appraisal supersedes the reminder for that NCO ────────────────────
    na.upsert_reminder(na.ReminderBody(cadet_id=smith.cin, due_date=_day(3)), db, staff)
    second = na.create_appraisal(_body(smith.cin, appraisal_date="2026-08-08"), db, staff)
    overview = na.list_appraisals(db, staff)
    assert [r["cadet_id"] for r in overview["reminders"]] == [jones.cin]
    smith_upcoming = next(r for r in overview["upcoming"] if r["cin"] == smith.cin)
    assert smith_upcoming["source"] == "appraisal"
    assert smith_upcoming["reminder_id"] is None
    # Newest appraisal sets the date: the second one defaulted to 12 months.
    assert smith_upcoming["due_date"][:10] == "2027-08-08"
    # And the list is in due-date order, soonest first.
    assert [r["due_date"] for r in overview["upcoming"]] == sorted(
        r["due_date"] for r in overview["upcoming"]
    )
    na.delete_appraisal(second["id"], db, staff)

    # ── editing keeps the snapshot unless it's overwritten ───────────────────
    updated = na.update_appraisal(
        appraisal["id"],
        _body(smith.cin, appraisal_date="2026-08-08", attendance="70% (7/10 nights)",
              next_review_months=3, general_observations="Rewritten.",
              targets="Lead a session alone.\nAttend a camp.", cause_for_concern=True),
        db, staff,
    )
    assert updated["attendance"] == "70% (7/10 nights)"
    assert updated["next_review_date"][:10] == "2026-11-08"
    assert updated["general_observations"] == "Rewritten."
    # A PUT is the whole form, so a field the edit leaves out really is cleared.
    assert updated["strengths"] == ""
    # Moving an appraisal to a different NCO would rewrite history, not fix it.
    try:
        na.update_appraisal(appraisal["id"], _body(jones.cin), db, staff)
        raise AssertionError("expected a cadet swap to be rejected")
    except HTTPException as e:
        assert e.status_code == 400

    # ── both documents build from the saved row ──────────────────────────────
    row = db.query(NcoAppraisal).filter(NcoAppraisal.id == appraisal["id"]).first()
    from form_generators.nco_appraisal_gen import appraisal_context, number_targets

    context = appraisal_context(row)
    assert context["next_review"] == "3 months (08/11/2026)"
    assert context["cause_for_concern"] == "Yes" and context["extend_probation"] == "No"
    # Targets are numbered on the way out, and an already-numbered list is
    # renumbered rather than double-marked.
    assert context["targets_numbered"] == "1. Lead a session alone.\n2. Attend a camp."
    assert number_targets("1. One\n- Two\n2) Three") == "1. One\n2. Two\n3. Three"

    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK")):
        response = na.download_appraisal(appraisal["id"], fmt, db, staff)
        body = _streamed(response)
        assert body.startswith(magic), f"{fmt} did not render"
        assert "NCO_Appraisal_Cpl_Sam_Smith_20260808" in response.headers["content-disposition"]

    # ── emailing the PDF to the NCO ──────────────────────────────────────────
    sent: list[dict] = []
    na.send_email = lambda to, subject, html, attachments=None, reply_to=None: sent.append(
        {"to": to, "subject": subject, "attachments": attachments, "reply_to": reply_to}
    )
    emailed = na.email_appraisal(appraisal["id"], na.EmailBody(), db, staff)
    # Defaults to the NCO's own address, with replies pointed at the sender
    # rather than the unmonitored noreply mailbox.
    assert sent[0]["to"] == "sam.smith@317atc.co.uk"
    assert sent[0]["reply_to"] == "staff@317atc.co.uk"
    assert sent[0]["attachments"][0][1].startswith(b"%PDF")
    assert emailed["emailed_to"] == "sam.smith@317atc.co.uk" and emailed["emailed_at"]

    # An NCO with no address on record can't be silently skipped.
    jones_appraisal = na.create_appraisal(_body(jones.cin), db, staff)
    try:
        na.email_appraisal(jones_appraisal["id"], na.EmailBody(), db, staff)
        raise AssertionError("expected a missing address to be rejected")
    except HTTPException as e:
        assert e.status_code == 400
    na.email_appraisal(jones_appraisal["id"], na.EmailBody(to="jo.jones@317atc.co.uk"), db, staff)
    assert sent[-1]["to"] == "jo.jones@317atc.co.uk"

    # A typo is caught before the address reaches Gmail.
    try:
        na.EmailBody(to="not-an-address")
        raise AssertionError("expected a malformed address to be rejected")
    except ValueError:
        pass

    print("nco appraisals self-check passed")


if __name__ == "__main__":
    test_add_months()
    test_is_nco()
    test_attendance_summary()
    test_age_on()
    test()
