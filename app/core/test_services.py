"""Core services that talk to the outside world — Gmail, the LLM chain, Google
Calendar — plus the scheduled jobs. Each external client is replaced with a
recorder so what's checked is our side: what we send, and how we degrade."""

import base64
import email
from datetime import datetime, timedelta

import httpx
import pytest

import core.calendar as cal
import core.emailer as emailer
import core.jobs as jobs
import core.llm as llm
from database.models import AssessmentSheet, Cadet, CadetQualification, StoresOrder, User

# ── send_email ────────────────────────────────────────────────────────────────

class FakeGmail:
    def __init__(self, fail=False):
        self.raw = []
        self.fail = fail

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        if self.fail:
            raise RuntimeError("Gmail down")
        self.raw.append(base64.urlsafe_b64decode(body["raw"]))
        return self

    def execute(self):
        return {}


@pytest.fixture
def gmail(monkeypatch, real_send_email):
    """(send_email, fake Gmail) with the service account configured. Settings
    are patched on core.emailer, which is what the real function reads."""
    fake = FakeGmail()

    class Creds:
        def with_subject(self, s):
            return self

    monkeypatch.setattr(emailer, "_service_account_creds", lambda scopes: Creds())
    monkeypatch.setattr(emailer, "google_build", lambda *a, **k: fake)
    monkeypatch.setattr(emailer, "SA_EMAIL", "sa@x")
    monkeypatch.setattr(emailer, "SA_PRIVATE_KEY", "key")
    monkeypatch.setattr(emailer, "NOREPLY_EMAIL", "noreply@317atc.co.uk")
    monkeypatch.delenv("EMAIL_DISABLED", raising=False)
    return real_send_email, fake


def test_send_email_builds_a_mime_message(gmail):
    send, fake = gmail
    send("a@b.co", "Subject £", "<p>Hi</p>", attachment=b"%PDF", attachment_filename="a.pdf",
                   attachments=[("r.png", b"\x89PNG", "image/png"), ("x.bin", b"\x00", "")], reply_to="r@b.co")
    msg = email.message_from_bytes(fake.raw[0])
    assert msg["To"] == "a@b.co" and msg["Reply-To"] == "r@b.co"
    assert msg["From"] == "317 ATC <noreply@317atc.co.uk>"
    parts = [p for p in msg.walk() if p.get_content_disposition() == "attachment"]
    # The legacy single attachment goes first.
    assert [p.get_filename() for p in parts] == ["a.pdf", "r.png", "x.bin"]
    assert parts[0].get_payload(decode=True) == b"%PDF"
    assert parts[2].get_content_type() == "application/octet-stream"


def test_send_email_skips_when_disabled_or_unconfigured(gmail, monkeypatch):
    send, fake = gmail
    monkeypatch.setenv("EMAIL_DISABLED", "true")
    send("a@b.co", "s", "b")
    monkeypatch.delenv("EMAIL_DISABLED")
    monkeypatch.setattr(emailer, "NOREPLY_EMAIL", None)
    send("a@b.co", "s", "b")
    assert fake.raw == []


def test_send_email_never_raises(gmail):
    send, fake = gmail
    fake.fail = True
    send("a@b.co", "s", "b")  # logged, not raised


@pytest.mark.parametrize("address,ok", [
    ("a@b.co", True), ("first.last@317atc.co.uk", True),
    ("a@b", False), ("no-at.co.uk", False), ("a b@c.co", False), ("", False),
])
def test_email_re(address, ok):
    assert bool(emailer.EMAIL_RE.match(address)) is ok


def test_session_plan_emails_escape_nco_text():
    html = emailer.session_plan_submitted_html(1, "<script>x</script>", "I/C & co", "", "<b>NCO</b>", False)
    assert "<script>" not in html and "&lt;script&gt;" in html and "&amp; co" in html


def test_quali_expiry_email_colours_urgent_rows():
    html = emailer.quali_expiry_email_html([("A", "First Aid", "01/01/2027", 10), ("B", "Radio", "01/03/2027", 80)])
    assert html.index("#c62828") < html.index("#e65100")


# ── LLM chain HTTP calls ──────────────────────────────────────────────────────

@pytest.fixture
def posts(monkeypatch):
    calls = []
    queue = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        resp = queue.pop(0)
        return resp

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: calls.append(("sleep", s)))
    return calls, queue


def test_gemini_drops_thought_parts(posts, monkeypatch):
    calls, queue = posts
    monkeypatch.setattr(llm, "GEMINI_API_KEY", "g")
    queue.append(httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": " Answer "}]}}]}))
    assert llm._call_model("gemini-2.5-flash", "p", "s", 0.5, 100, None) == "Answer"
    assert "key=g" in calls[0][0]


@pytest.mark.parametrize("resp,match", [
    (httpx.Response(429, json={"error": "quota"}), "rate limited"),
    (httpx.Response(400, json={"error": "bad"}), "Gemini API error"),
])
def test_gemini_errors(posts, monkeypatch, resp, match):
    calls, queue = posts
    monkeypatch.setattr(llm, "GEMINI_API_KEY", "g")
    queue.append(resp)
    with pytest.raises(RuntimeError, match=match):
        llm._call_model("gemini-2.5-flash", "p", "s", 0.5, 100, None)


def test_gemini_without_a_key(monkeypatch):
    monkeypatch.setattr(llm, "GEMINI_API_KEY", None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm._call_model("gemini-2.5-flash", "p", "s", 0.5, 100, None)


def test_openai_compatible_waits_out_429s(posts, monkeypatch):
    calls, queue = posts
    monkeypatch.setattr(llm, "NVIDIA_API_KEY", "n")
    queue += [httpx.Response(429, headers={"retry-after": "2"}, json={}),
              httpx.Response(429, headers={"retry-after": "500"}, json={}),
              httpx.Response(200, json={"choices": [{"message": {"content": " Hi "}}]})]
    assert llm._call_model(llm.NVIDIA_MODEL, "p", "s", 0.5, 100, None) == "Hi"
    sleeps = [c[1] for c in calls if c[0] == "sleep"]
    assert sleeps == [3.0, 60]  # retry-after + 1, capped at a minute
    assert calls[0][1]["json"]["max_tokens"] == llm.NVIDIA_MAX_TOKENS


def test_openai_compatible_gives_up_after_five_429s(posts, monkeypatch):
    calls, queue = posts
    monkeypatch.setattr(llm, "GROQ_API_KEY", "q")
    queue += [httpx.Response(429, json={"error": "slow down"})] * 5
    with pytest.raises(RuntimeError, match="API error"):
        llm._call_model(llm.GROQ_MODEL, "p", "s", 0.5, 100, 50)


def test_groq_uses_its_own_budget_and_low_reasoning(posts, monkeypatch):
    calls, queue = posts
    monkeypatch.setattr(llm, "GROQ_API_KEY", "q")
    queue.append(httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    assert llm._call_model(llm.GROQ_MODEL, "p", "s", 0.5, 100, 50) == ""
    body = calls[0][1]["json"]
    assert body["max_tokens"] == 50 and body["reasoning_effort"] == "low"


def test_missing_openai_key(monkeypatch):
    monkeypatch.setattr(llm, "NVIDIA_API_KEY", None)
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY not configured"):
        llm._call_model(llm.NVIDIA_MODEL, "p", "s", 0.5, 100, None)


def test_model_label():
    assert llm.model_label(None) == "Unknown"
    assert llm.model_label("something-new") == "something-new"
    assert llm.model_label(llm.NVIDIA_MODEL) == "GLM 5.3"


# ── calendar ──────────────────────────────────────────────────────────────────

class FakeCalendar:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def events(self):
        return self

    def _record(self, name, **kw):
        self.calls.append((name, kw))
        return self

    def insert(self, **kw):
        return self._record("insert", **kw)

    def patch(self, **kw):
        return self._record("patch", **kw)

    def delete(self, **kw):
        return self._record("delete", **kw)

    def execute(self):
        if self.error:
            raise self.error
        return {"id": "evt-1"}


@pytest.fixture
def calendar(monkeypatch):
    fake = FakeCalendar()
    monkeypatch.setattr(cal, "_service", lambda: fake)
    monkeypatch.setattr(cal, "NCO_HOLIDAY_CALENDAR_ID", "cal@x")
    monkeypatch.setattr(cal, "SA_EMAIL", "sa@x")
    monkeypatch.setattr(cal, "SA_PRIVATE_KEY", "k")
    return fake


def test_event_body_is_all_day_and_inclusive():
    body = cal._event_body("Nina", "n@x", datetime(2026, 3, 1), datetime(2026, 3, 3), "")
    assert body["start"] == {"date": "2026-03-01"} and body["end"] == {"date": "2026-03-04"}
    assert body["description"] == "" and body["transparency"] == "transparent"
    assert cal._event_body("N", "n", datetime(2026, 3, 1), datetime(2026, 3, 1), "Exams")["description"] == "Reason: Exams"


def test_calendar_happy_paths(calendar):
    assert cal.create_holiday_event("N", "n@x", datetime(2026, 3, 1), datetime(2026, 3, 2), "r") == "evt-1"
    assert cal.update_holiday_event("evt-1", "N", "n@x", datetime(2026, 3, 1), datetime(2026, 3, 2), "") is True
    assert cal.delete_holiday_event("evt-1") is True
    assert [c[0] for c in calendar.calls] == ["insert", "patch", "delete"]


def test_calendar_failures_are_returned_not_raised(calendar):
    calendar.error = RuntimeError("quota")
    assert cal.create_holiday_event("N", "n@x", datetime(2026, 3, 1), datetime(2026, 3, 2), "") is None
    assert cal.update_holiday_event("e", "N", "n@x", datetime(2026, 3, 1), datetime(2026, 3, 2), "") is False
    assert cal.delete_holiday_event("e") is False


@pytest.mark.parametrize("status,ok", [(404, True), (410, True), (403, False), (500, False)])
def test_deleting_an_already_gone_event_counts_as_done(calendar, status, ok):
    class HttpErr(Exception):
        def __init__(self):
            self.resp = type("R", (), {"status": status})()

    calendar.error = HttpErr()
    assert cal.delete_holiday_event("e") is ok


def test_calendar_unconfigured_or_no_event_id(calendar, monkeypatch):
    assert cal.update_holiday_event("", "N", "n", datetime.now(), datetime.now(), "") is False
    assert cal.delete_holiday_event(None) is False
    monkeypatch.setattr(cal, "NCO_HOLIDAY_CALENDAR_ID", "")
    assert cal.calendar_configured() is False
    assert cal.create_holiday_event("N", "n", datetime.now(), datetime.now(), "") is None
    assert calendar.calls == []


# ── scheduled jobs ────────────────────────────────────────────────────────────

def test_cleanup_old_completed_orders(db, session_factory):
    old = datetime.now() - timedelta(days=200)
    db.add_all([
        StoresOrder(id=1, completed=True, created_at=old),
        StoresOrder(id=2, completed=False, created_at=old),
        StoresOrder(id=3, completed=True, created_at=datetime.now()),
    ])
    db.commit()
    jobs.cleanup_old_completed_orders()
    db.expire_all()
    assert sorted(o.id for o in db.query(StoresOrder).all()) == [2, 3]


def test_cleanup_old_completed_assessments(db, session_factory):
    old = datetime.now() - timedelta(days=200)
    db.add(User(id=1, google_id="g", email="a@x"))
    db.add(Cadet(cin=1, first_name="A", last_name="B"))
    db.add_all([
        AssessmentSheet(id=1, assessment_type="x", fields={}, cadet_id=1, assessor_id=1, created_at=old,
                        uploaded=True, uploaded_at=old),
        # Uploaded long ago but no uploaded_at: falls back to created_at.
        AssessmentSheet(id=2, assessment_type="x", fields={}, cadet_id=1, assessor_id=1, created_at=old,
                        uploaded=True),
        AssessmentSheet(id=3, assessment_type="x", fields={}, cadet_id=1, assessor_id=1, created_at=old,
                        uploaded=False),
        AssessmentSheet(id=4, assessment_type="x", fields={}, cadet_id=1, assessor_id=1, created_at=old,
                        uploaded=True, uploaded_at=datetime.now()),
    ])
    db.commit()
    jobs.cleanup_old_completed_assessments()
    db.expire_all()
    assert sorted(s.id for s in db.query(AssessmentSheet).all()) == [3, 4]


def test_cleanup_jobs_swallow_errors(monkeypatch):
    class Broken:
        def query(self, *a):
            raise RuntimeError("db gone")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(jobs, "SessionLocal", Broken)
    jobs.cleanup_old_completed_orders()
    jobs.cleanup_old_completed_assessments()


def test_quali_expiry_alert_emails_each_qual_once(db, session_factory, outbox):
    now = datetime.now()
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.add_all([
        CadetQualification(cadet_id=1, qual_type="First Aid", status="t", date_expires=now + timedelta(days=20)),
        CadetQualification(cadet_id=1, qual_type="Radio", status="t", date_expires=now + timedelta(days=60)),
        CadetQualification(cadet_id=1, qual_type="Swim", status="t", date_expires=now + timedelta(days=200)),
        CadetQualification(cadet_id=1, qual_type="Old", status="t", date_expires=now - timedelta(days=1)),
    ])
    db.commit()
    jobs.quali_expiry_alert()
    assert len(outbox) == 1 and "(2)" in outbox[0]["subject"]
    html = outbox[0]["html"]
    assert html.index("First Aid") < html.index("Radio") and "Swim" not in html and "Old" not in html

    jobs.quali_expiry_alert()
    assert len(outbox) == 1  # nothing new to say


def test_quali_expiry_alert_with_nothing_due(db, session_factory, outbox):
    jobs.quali_expiry_alert()
    assert outbox == []


def test_register_jobs(monkeypatch):
    added = []

    class Sched:
        def add_job(self, func, trigger=None, **kw):
            added.append((func.__name__, kw.get("id")))

    monkeypatch.setattr(jobs.scrapers, "register_schedule_jobs", lambda: added.append(("schedules", None)))
    monkeypatch.setattr(jobs, "DB_BACKUP_ENABLED", False)
    jobs.register_jobs(Sched())
    names = [a[0] for a in added]
    assert "quali_expiry_alert" in names and "scheduled_send_job" in names and "schedules" in names
    assert "cleanup_old_usage" in names
    assert "run_db_backup" not in names

    added.clear()
    monkeypatch.setattr(jobs, "DB_BACKUP_ENABLED", True)
    jobs.register_jobs(Sched())
    assert ("run_db_backup", "db_backup") in added
