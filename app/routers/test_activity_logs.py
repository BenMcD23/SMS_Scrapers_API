"""DofE activity logs: cadets log their own sections, staff switch them on,
watch the week and get the PDF when a section is finished."""

import io
from datetime import date, timedelta

import pytest
from pypdf import PdfReader

import routers.activity_logs as activity_logs
from core.config import ACTIVITY_LOG_ALERT_EMAIL
from database.models import ActivityLog, ActivityLogEnrolment, ActivityLogEntry, Cadet

TODAY = date(2026, 10, 21)  # a Wednesday
START = date(2026, 7, 27)   # a Monday, twelve full weeks before this one
SETUP = {
    "activity": "Volunteering at Barnardo's",
    "goal": "Help on the till and sort donations",
    "assessor_name": "Kate Siddall",
    "assessor_role": "Shop manager",
    "duration_months": 3,
    "start_date": START.isoformat(),
}
ME = "/cadets/me/activity-log"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(activity_logs, "_today", lambda: TODAY)


@pytest.fixture
def cadets(db):
    db.add_all([
        Cadet(cin=1, first_name="Cara", last_name="Cadet", email="cadet@317atc.co.uk", rank="Cpl", flight="A"),
        Cadet(cin=2, first_name="Zoë", last_name="Ó Briain", email="cadet2@317atc.co.uk", flight="B"),
        Cadet(cin=3, first_name="Nora", last_name="Notdoingit", email="n@example.com"),
    ])
    db.commit()


@pytest.fixture
def enrolled(db, cadets):
    from datetime import datetime
    db.add_all([ActivityLogEnrolment(cadet_id=c, level="Bronze", enabled_at=datetime(2026, 7, 1)) for c in (1, 2)])
    db.commit()


def _setup(api, section="volunteering", who="cadet", **changes):
    return api.put(f"{ME}/{section}", json={**SETUP, **changes}, headers=api.as_(who))


def _entry(day: date, minutes=60, text="Sorted donations"):
    return {"date": day.isoformat(), "description": text, "minutes": minutes}


def _section(body, key):
    return next(s for s in body["sections"] if s["section"] == key)


# ── who can get in ───────────────────────────────────────────────────────────

STAFF_ROUTES = [
    ("get", "/activity-logs/grid", None),
    ("get", "/activity-logs/cadets/1", None),
    ("put", "/activity-logs/cadets/1/enrolment", {"level": "Bronze"}),
    ("delete", "/activity-logs/cadets/1/enrolment", None),
    ("put", "/activity-logs/cadets/1/skills", SETUP),
    ("post", "/activity-logs/cadets/1/skills/entries", {"entries": [_entry(TODAY)]}),
    ("patch", "/activity-logs/cadets/1/skills/entries/1", _entry(TODAY)),
    ("delete", "/activity-logs/cadets/1/skills/entries/1", None),
    ("get", "/activity-logs/cadets/1/skills/pdf", None),
    ("put", "/activity-logs/cadets/1/skills/entries/1/sign-off", {"initials": "BM"}),
    ("delete", "/activity-logs/cadets/1/skills/entries/1/sign-off", None),
]


@pytest.mark.parametrize("method,path,body", STAFF_ROUTES)
def test_staff_routes_need_a_token(api, cadets, method, path, body):
    assert api.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method,path,body", STAFF_ROUTES)
def test_staff_routes_turn_away_everyone_but_staff(api, cadets, persona, method, path, body):
    assert api.request(method, path, json=body, headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_member_sees_the_dashboard(api, persona):
    assert api.get("/activity-logs/grid", headers=api.as_(persona)).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("get", ME, None),
    ("put", f"{ME}/skills", SETUP),
    ("post", f"{ME}/skills/entries", {"entries": [_entry(TODAY)]}),
    ("patch", f"{ME}/skills/entries/1", _entry(TODAY)),
    ("delete", f"{ME}/skills/entries/1", None),
])
def test_cadet_routes_need_a_token_and_a_cadet_record(api, cadets, method, path, body):
    assert api.request(method, path, json=body).status_code == 401
    # Staff have no cadet record, so there's no log of theirs to touch.
    assert api.request(method, path, json=body, headers=api.as_("staff")).status_code == 404


# ── switched off ─────────────────────────────────────────────────────────────

def test_a_cadet_who_isnt_switched_on_is_told_so_rather_than_refused(api, cadets):
    # The portal asks this on every page to decide whether to show the link.
    res = api.get(ME, headers=api.as_("cadet"))
    assert res.status_code == 200
    assert res.json() == {"enabled": False, "cin": 1, "name": "Cpl Cara Cadet", "sections": []}


def test_a_cadet_who_isnt_switched_on_cant_start_a_log(api, cadets, db):
    res = _setup(api)
    assert res.status_code == 403
    assert "Speak to a member of staff" in res.json()["detail"]
    assert db.query(ActivityLog).count() == 0


def test_staff_switch_a_cadet_on_and_it_shows_three_empty_sections(api, cadets, db):
    res = api.put("/activity-logs/cadets/1/enrolment", json={"level": "Silver"}, headers=api.as_("staff"))
    assert res.status_code == 200
    me = api.get(ME, headers=api.as_("cadet")).json()
    assert me["enabled"] and me["level"] == "Silver"
    assert [(s["section"], s["label"], s["log"]) for s in me["sections"]] == [
        ("skills", "Skills", None), ("volunteering", "Volunteering", None), ("physical", "Physical", None)]
    assert db.get(ActivityLogEnrolment, 1).enabled_by == "staff@317atc.co.uk"


def test_switching_off_hides_the_page_but_keeps_the_log(api, enrolled, db):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    assert api.delete("/activity-logs/cadets/1/enrolment", headers=api.as_("staff")).status_code == 200
    assert api.get(ME, headers=api.as_("cadet")).json()["enabled"] is False
    assert db.query(ActivityLogEntry).count() == 1

    api.put("/activity-logs/cadets/1/enrolment", json={"level": "Bronze"}, headers=api.as_("staff"))
    log = _section(api.get(ME, headers=api.as_("cadet")).json(), "volunteering")["log"]
    assert len(log["entries"]) == 1


@pytest.mark.parametrize("method", ["put", "delete"])
def test_switching_a_cadet_who_doesnt_exist_is_404(api, method):
    res = api.request(method, "/activity-logs/cadets/999/enrolment", json={"level": "Bronze"},
                      headers=api.as_("staff"))
    assert res.status_code == 404


def test_switching_off_a_cadet_who_isnt_on_is_404(api, cadets):
    assert api.delete("/activity-logs/cadets/3/enrolment", headers=api.as_("staff")).status_code == 404


def test_a_level_that_isnt_bronze_silver_or_gold_is_rejected(api, cadets):
    res = api.put("/activity-logs/cadets/1/enrolment", json={"level": "Platinum"}, headers=api.as_("staff"))
    assert res.status_code == 422


# ── setting a section up ─────────────────────────────────────────────────────

def test_a_cadet_sets_up_a_section_and_sees_where_they_are(api, enrolled):
    res = _setup(api)
    assert res.status_code == 200
    log = _section(res.json(), "volunteering")["log"]
    assert log["activity"] == "Volunteering at Barnardo's"
    assert log["assessor_name"] == "Kate Siddall"
    assert (log["weeks_done"], log["weeks_needed"], log["complete"]) == (0, 12, False)
    assert log["end_date"] == "2026-10-27"
    assert len(log["weeks"]) == 13 and len(log["missed"]) == 12
    assert _section(res.json(), "skills")["log"] is None


def test_a_cadet_who_changes_their_mind_edits_the_same_log(api, enrolled, db):
    _setup(api)
    res = _setup(api, activity="Helping at the food bank", duration_months=6, assessor_name="")
    log = _section(res.json(), "volunteering")["log"]
    assert (log["activity"], log["duration_months"], log["weeks_needed"]) == ("Helping at the food bank", 6, 24)
    assert db.query(ActivityLog).count() == 1


def test_the_assessor_can_be_filled_in_later(api, enrolled):
    res = _setup(api, assessor_name="  ", assessor_role="")
    assert res.status_code == 200
    assert _section(res.json(), "volunteering")["log"]["assessor_name"] == ""


@pytest.mark.parametrize("changes", [
    {"activity": "   "},
    {"goal": ""},
    {"duration_months": 4},
    {"start_date": "next tuesday"},
    {"activity": "x" * 301},
])
def test_setup_validation(api, enrolled, changes):
    assert _setup(api, **changes).status_code == 422


def test_an_unknown_section_is_422(api, enrolled):
    assert _setup(api, section="swimming").status_code == 422


@pytest.mark.parametrize("start", [TODAY + timedelta(days=400), TODAY - timedelta(days=4 * 366)])
def test_a_start_date_miles_off_is_refused(api, enrolled, start):
    res = _setup(api, start_date=start.isoformat())
    assert res.status_code == 400


def test_moving_the_start_after_something_already_logged_is_refused(api, enrolled):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(START + timedelta(days=1))]},
             headers=api.as_("cadet"))
    res = _setup(api, start_date=(START + timedelta(days=14)).isoformat())
    assert res.status_code == 409
    assert "28 Jul 2026" in res.json()["detail"]


# ── logging ──────────────────────────────────────────────────────────────────

def test_a_cadet_catches_up_several_weeks_in_one_go(api, enrolled):
    _setup(api)
    weeks = [_entry(START + timedelta(weeks=i), 60) for i in range(3)]
    res = api.post(f"{ME}/volunteering/entries", json={"entries": weeks}, headers=api.as_("cadet"))
    assert res.status_code == 201
    log = _section(res.json(), "volunteering")["log"]
    assert log["weeks_done"] == 3
    assert len(log["missed"]) == 9
    # Newest first, as the page lists them.
    assert [e["date"] for e in log["entries"]] == [w["date"] for w in reversed(weeks)]


def test_logging_before_setting_up_is_404(api, enrolled):
    res = api.post(f"{ME}/skills/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    assert res.status_code == 404
    assert "Set up your Skills section first" in res.json()["detail"]


@pytest.mark.parametrize("day,words", [
    (TODAY + timedelta(days=1), "hasn't happened yet"),
    (START - timedelta(days=1), "before you started"),
])
def test_a_date_outside_the_log_is_refused_and_nothing_is_saved(api, enrolled, db, day, words):
    _setup(api)
    # The good entry first: one bad row must not leave half a catch-up saved.
    body = {"entries": [_entry(TODAY), _entry(day)]}
    res = api.post(f"{ME}/volunteering/entries", json=body, headers=api.as_("cadet"))
    assert res.status_code == 400
    assert words in res.json()["detail"]
    assert db.query(ActivityLogEntry).count() == 0


@pytest.mark.parametrize("body", [
    {"entries": []},
    {"entries": [_entry(TODAY, minutes=0)]},
    {"entries": [_entry(TODAY, minutes=601)]},
    {"entries": [_entry(TODAY, text="  ")]},
    {"entries": [_entry(TODAY)] * 61},
    {"entries": [{"date": "yesterday", "description": "x", "minutes": 60}]},
])
def test_entry_validation(api, enrolled, body):
    _setup(api)
    assert api.post(f"{ME}/volunteering/entries", json=body, headers=api.as_("cadet")).status_code == 422


def test_a_cadet_edits_and_deletes_their_own_entries(api, enrolled):
    _setup(api)
    log = _section(api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY, 30)]},
                            headers=api.as_("cadet")).json(), "volunteering")["log"]
    entry_id = log["entries"][0]["id"]

    res = api.patch(f"{ME}/volunteering/entries/{entry_id}", json=_entry(TODAY, 75, "Ran the till"),
                    headers=api.as_("cadet"))
    assert res.status_code == 200
    log = _section(res.json(), "volunteering")["log"]
    assert (log["entries"][0]["description"], log["this_week_minutes"]) == ("Ran the till", 75)

    res = api.delete(f"{ME}/volunteering/entries/{entry_id}", headers=api.as_("cadet"))
    assert _section(res.json(), "volunteering")["log"]["entries"] == []


def test_an_edit_cant_move_an_entry_into_the_future(api, enrolled):
    _setup(api)
    log = _section(api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]},
                            headers=api.as_("cadet")).json(), "volunteering")["log"]
    res = api.patch(f"{ME}/volunteering/entries/{log['entries'][0]['id']}",
                    json=_entry(TODAY + timedelta(days=3)), headers=api.as_("cadet"))
    assert res.status_code == 400


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_a_cadet_cant_touch_someone_elses_entry(api, enrolled, db, method):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    theirs = db.query(ActivityLogEntry).one().id
    _setup(api, who="cadet2")
    res = api.request(method, f"{ME}/volunteering/entries/{theirs}", json=_entry(TODAY, 5),
                      headers=api.as_("cadet2"))
    assert res.status_code == 404
    assert db.get(ActivityLogEntry, theirs).minutes == 60


def test_an_entry_in_another_section_isnt_found_through_this_one(api, enrolled, db):
    _setup(api)
    _setup(api, section="skills")
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    entry_id = db.query(ActivityLogEntry).one().id
    assert api.delete(f"{ME}/skills/entries/{entry_id}", headers=api.as_("cadet")).status_code == 404


# ── finishing ────────────────────────────────────────────────────────────────

def _twelve_weeks():
    return {"entries": [_entry(START + timedelta(weeks=i)) for i in range(12)]}


def test_finishing_a_section_emails_staff_the_pdf_once(api, enrolled, outbox, db):
    _setup(api)
    res = api.post(f"{ME}/volunteering/entries", json=_twelve_weeks(), headers=api.as_("cadet"))
    log = _section(res.json(), "volunteering")["log"]
    assert log["complete"] and log["completed_at"]

    assert len(outbox) == 1
    mail = outbox[0]
    assert mail["to"] == ACTIVITY_LOG_ALERT_EMAIL
    assert mail["subject"] == "Cpl Cara Cadet has finished their Bronze DofE Volunteering section"
    assert "Kate Siddall" in mail["html"]
    assert mail["attachment_filename"] == "Volunteering Section Bronze DofE Activity Log - Cara Cadet.pdf"
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(mail["attachment"])).pages)
    assert "Barnardo" in text and "12 of 12" in text

    # More logging after finishing doesn't send it again.
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    assert len(outbox) == 1


def test_a_section_taken_back_under_by_an_edit_is_sent_again_when_finished_again(api, enrolled, outbox, db):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json=_twelve_weeks(), headers=api.as_("cadet"))
    first = db.query(ActivityLogEntry).order_by(ActivityLogEntry.date).first()

    res = api.delete(f"{ME}/volunteering/entries/{first.id}", headers=api.as_("cadet"))
    log = _section(res.json(), "volunteering")["log"]
    assert not log["complete"] and log["completed_at"] is None

    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(START)]}, headers=api.as_("cadet"))
    assert len(outbox) == 2


def test_lengthening_the_timescale_reopens_a_finished_section(api, enrolled, outbox):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json=_twelve_weeks(), headers=api.as_("cadet"))
    log = _section(_setup(api, duration_months=6).json(), "volunteering")["log"]
    assert (log["complete"], log["completed_at"], log["weeks_needed"]) == (False, None, 24)


def test_an_hour_short_spread_over_weeks_doesnt_finish(api, enrolled, outbox):
    _setup(api)
    body = {"entries": [_entry(START + timedelta(weeks=i), 59) for i in range(12)]}
    log = _section(api.post(f"{ME}/volunteering/entries", json=body, headers=api.as_("cadet")).json(),
                   "volunteering")["log"]
    assert log["weeks_done"] == 0 and outbox == []


def test_a_failed_email_still_keeps_the_entries(api, enrolled, db, monkeypatch):
    # send_email logs rather than raises; a broken template must not cost the
    # cadet their catch-up either, so the commit comes first.
    def boom(*a, **k):
        raise RuntimeError("Gmail down")
    monkeypatch.setattr(activity_logs, "send_email", boom)
    _setup(api)
    with pytest.raises(RuntimeError):
        api.post(f"{ME}/volunteering/entries", json=_twelve_weeks(), headers=api.as_("cadet"))
    assert db.query(ActivityLogEntry).count() == 12


# ── staff, on a cadet's behalf ───────────────────────────────────────────────

def test_staff_set_up_and_log_for_a_cadet(api, enrolled, db):
    res = api.put("/activity-logs/cadets/1/physical", json={**SETUP, "activity": "Swimming"},
                  headers=api.as_("staff"))
    assert res.status_code == 200
    res = api.post("/activity-logs/cadets/1/physical/entries", json={"entries": [_entry(TODAY)]},
                   headers=api.as_("staff"))
    assert res.status_code == 201
    assert db.query(ActivityLogEntry).one().created_by == "staff@317atc.co.uk"
    # And the cadet sees it on their own page.
    me = api.get(ME, headers=api.as_("cadet")).json()
    assert _section(me, "physical")["log"]["activity"] == "Swimming"


def test_staff_edit_and_delete_a_cadets_entry(api, enrolled):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    entry_id = _section(api.get(ME, headers=api.as_("cadet")).json(), "volunteering")["log"]["entries"][0]["id"]
    res = api.patch(f"/activity-logs/cadets/1/volunteering/entries/{entry_id}", json=_entry(TODAY, 90),
                    headers=api.as_("staff"))
    assert _section(res.json(), "volunteering")["log"]["this_week_minutes"] == 90
    res = api.delete(f"/activity-logs/cadets/1/volunteering/entries/{entry_id}", headers=api.as_("staff"))
    assert res.status_code == 200


def test_staff_cant_log_for_a_cadet_who_isnt_switched_on(api, cadets):
    res = api.put("/activity-logs/cadets/3/skills", json=SETUP, headers=api.as_("staff"))
    assert res.status_code == 409


def test_staff_looking_up_a_cadet_who_doesnt_exist_get_404(api):
    assert api.get("/activity-logs/cadets/999", headers=api.as_("staff")).status_code == 404


def test_staff_download_the_pdf_at_any_point(api, enrolled):
    _setup(api, who="cadet2")
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY, 45, "Café rota")]},
             headers=api.as_("cadet2"))
    res = api.get("/activity-logs/cadets/2/volunteering/pdf", headers=api.as_("staff"))
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    # A non-ASCII name still downloads (RFC 5987), rather than 500ing.
    assert "filename*=UTF-8''" in res.headers["content-disposition"]
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(res.content)).pages)
    assert "Zoë Ó Briain" in text and "Café rota" in text and "0 of 12" in text


# ── assessor sign-off ────────────────────────────────────────────────────────

SIGN = "/activity-logs/cadets/{cin}/volunteering/entries/{id}/sign-off"


def _logged(api, who="cadet", text="Sorted donations"):
    """Set up volunteering and log one entry; return its id."""
    _setup(api, who=who)
    body = api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY, text=text)]},
                    headers=api.as_(who)).json()
    return _section(body, "volunteering")["log"]["entries"][0]["id"]


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_staff_sign_an_entry_off_with_their_initials(api, enrolled, db, persona):
    entry_id = _logged(api)
    res = api.put(SIGN.format(cin=1, id=entry_id), json={"initials": "bm"}, headers=api.as_(persona))
    assert res.status_code == 200
    entry = _section(res.json(), "volunteering")["log"]["entries"][0]
    assert entry["signed_off_initials"] == "BM" and entry["signed_off_at"]
    row = db.get(ActivityLogEntry, entry_id)
    db.refresh(row)
    assert row.signed_off_by  # who clicked, even when the initials are someone else's
    # The cadet sees it on the portal too.
    me = _section(api.get(ME, headers=api.as_("cadet")).json(), "volunteering")["log"]["entries"][0]
    assert me["signed_off_initials"] == "BM"


def test_signing_off_with_someone_elses_initials_and_undoing_it(api, enrolled):
    entry_id = _logged(api)
    api.put(SIGN.format(cin=1, id=entry_id), json={"initials": "BM"}, headers=api.as_("staff"))
    # An instructor who saw it: "K. S." is tidied to KS and replaces BM.
    res = api.put(SIGN.format(cin=1, id=entry_id), json={"initials": " K. S. "}, headers=api.as_("staff"))
    assert _section(res.json(), "volunteering")["log"]["entries"][0]["signed_off_initials"] == "KS"
    res = api.delete(SIGN.format(cin=1, id=entry_id), headers=api.as_("staff"))
    entry = _section(res.json(), "volunteering")["log"]["entries"][0]
    assert (entry["signed_off_initials"], entry["signed_off_at"]) == (None, None)


@pytest.mark.parametrize("initials", ["", "   ", "ABCDE", "B1", "B-M", "..."])
def test_initials_must_be_one_to_four_letters(api, enrolled, db, initials):
    entry_id = _logged(api)
    res = api.put(SIGN.format(cin=1, id=entry_id), json={"initials": initials}, headers=api.as_("staff"))
    assert res.status_code == 422
    assert db.get(ActivityLogEntry, entry_id).signed_off_initials is None


def test_non_ascii_initials_are_kept(api, enrolled):
    entry_id = _logged(api)
    res = api.put(SIGN.format(cin=1, id=entry_id), json={"initials": "zó"}, headers=api.as_("staff"))
    assert _section(res.json(), "volunteering")["log"]["entries"][0]["signed_off_initials"] == "ZÓ"


def test_signing_off_an_entry_that_isnt_there_is_404(api, enrolled):
    _logged(api)
    assert api.put(SIGN.format(cin=1, id=999), json={"initials": "BM"},
                   headers=api.as_("staff")).status_code == 404
    assert api.delete(SIGN.format(cin=1, id=999), headers=api.as_("staff")).status_code == 404
    assert api.put(SIGN.format(cin=999, id=1), json={"initials": "BM"},
                   headers=api.as_("staff")).status_code == 404


def test_signing_off_another_cadets_entry_through_this_cadet_is_404(api, enrolled, db):
    theirs = _logged(api, who="cadet2")
    _setup(api)  # cadet 1 has a volunteering log too, just not that entry
    res = api.put(SIGN.format(cin=1, id=theirs), json={"initials": "BM"}, headers=api.as_("staff"))
    assert res.status_code == 404
    assert db.get(ActivityLogEntry, theirs).signed_off_initials is None


def test_signing_off_for_a_cadet_who_isnt_switched_on_is_409(api, enrolled, db):
    entry_id = _logged(api)
    api.delete("/activity-logs/cadets/1/enrolment", headers=api.as_("staff"))
    res = api.put(SIGN.format(cin=1, id=entry_id), json={"initials": "BM"}, headers=api.as_("staff"))
    assert res.status_code == 409


@pytest.mark.parametrize("who,path", [("cadet", ME + "/volunteering/entries/{id}"),
                                      ("staff", "/activity-logs/cadets/1/volunteering/entries/{id}")])
def test_changing_a_signed_off_entry_clears_the_sign_off(api, enrolled, who, path):
    # The assessor vouched for what it said then, not the new version.
    entry_id = _logged(api)
    api.put(SIGN.format(cin=1, id=entry_id), json={"initials": "BM"}, headers=api.as_("staff"))
    res = api.patch(path.format(id=entry_id), json=_entry(TODAY, 90), headers=api.as_(who))
    assert _section(res.json(), "volunteering")["log"]["entries"][0]["signed_off_initials"] is None


def test_saving_a_signed_off_entry_unchanged_keeps_the_sign_off(api, enrolled):
    entry_id = _logged(api)
    api.put(SIGN.format(cin=1, id=entry_id), json={"initials": "BM"}, headers=api.as_("staff"))
    res = api.patch(f"{ME}/volunteering/entries/{entry_id}", json=_entry(TODAY), headers=api.as_("cadet"))
    assert _section(res.json(), "volunteering")["log"]["entries"][0]["signed_off_initials"] == "BM"


def test_the_pdf_shows_the_initials_of_signed_off_rows(api, enrolled):
    signed = _logged(api, text="Ran the till")
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY - timedelta(days=1), text="Tidied")]},
             headers=api.as_("cadet"))
    api.put(SIGN.format(cin=1, id=signed), json={"initials": "QXZ"}, headers=api.as_("staff"))
    res = api.get("/activity-logs/cadets/1/volunteering/pdf", headers=api.as_("staff"))
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(res.content)).pages)
    assert text.count("QXZ") == 1


def test_the_pdf_of_a_section_not_set_up_is_404(api, enrolled):
    assert api.get("/activity-logs/cadets/1/skills/pdf", headers=api.as_("staff")).status_code == 404


# ── the week grid ────────────────────────────────────────────────────────────

def _grid(api, **params):
    res = api.get("/activity-logs/grid", params=params, headers=api.as_("staff"))
    assert res.status_code == 200
    return res.json()


def _cells(cadet, key):
    """One section's states across the weeks shown, oldest first."""
    return [w["cells"][key]["state"] for w in cadet["weeks"]]


def test_the_grid_is_the_last_eight_weeks_ending_this_one(api, cadets):
    body = _grid(api)
    assert body["cadets"] == []
    assert body["current_week"] == "2026-10-19"
    assert body["weeks"][0] == "2026-08-31" and body["weeks"][-1] == "2026-10-19"
    assert len(body["weeks"]) == 8


def test_each_week_shows_which_sections_got_their_hour(api, enrolled):
    # Cara: volunteering logged three weeks ago, last week a bit, this week done.
    _setup(api)
    body = {"entries": [_entry(TODAY - timedelta(weeks=3)), _entry(TODAY - timedelta(weeks=1), 20), _entry(TODAY)]}
    api.post(f"{ME}/volunteering/entries", json=body, headers=api.as_("cadet"))
    # Zoë: volunteering set up, nothing logged.
    _setup(api, who="cadet2")

    grid = _grid(api, weeks=4)
    # Nora isn't switched on, so isn't listed. Sorted by surname, Ó under O.
    assert [c["name"] for c in grid["cadets"]] == ["Cara Cadet", "Zoë Ó Briain"]
    cara, zoe = grid["cadets"]
    assert _cells(cara, "volunteering") == ["done", "missed", "short", "done"]
    assert cara["weeks"][2]["cells"]["volunteering"]["minutes"] == 20
    # Not set up: nothing due, in any week.
    assert _cells(cara, "physical") == ["off"] * 4
    # Nothing yet this week isn't "missed" while there are days left.
    assert _cells(zoe, "volunteering") == ["missed", "missed", "missed", "due"]
    assert cara["behind"] is False and zoe["behind"] is True
    vol = _section(zoe, "volunteering")
    assert (vol["set_up"], vol["weeks_done"], vol["weeks_needed"], vol["last_added"]) == (True, 0, 12, None)


def test_under_an_hour_this_week_needs_chasing(api, enrolled):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY, 30)]}, headers=api.as_("cadet"))
    cara = _grid(api)["cadets"][0]
    assert cara["behind"] is True
    assert _cells(cara, "volunteering")[-1] == "short"


def test_a_section_not_set_up_yet_isnt_chased_but_nothing_set_up_is(api, enrolled):
    # Cadets often start one section at a time.
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    cara, zoe = sorted(_grid(api)["cadets"], key=lambda c: c["cin"])
    assert cara["behind"] is False
    assert zoe["behind"] is True


def test_weeks_before_the_start_and_after_finishing_have_nothing_due(api, enrolled, db):
    _setup(api, start_date=(TODAY - timedelta(weeks=2)).isoformat())
    _setup(api, section="skills", start_date=(TODAY + timedelta(days=10)).isoformat())
    cara = _grid(api, weeks=5)["cadets"][0]
    assert _cells(cara, "volunteering") == ["off", "off", "missed", "missed", "due"]
    assert _cells(cara, "skills") == ["off"] * 5

    # Finished two weeks ago: the weeks since have nothing due.
    from datetime import datetime
    log = db.query(ActivityLog).filter(ActivityLog.section == "volunteering").one()
    log.completed_at = datetime(2026, 10, 7)
    db.commit()
    cara = _grid(api, weeks=5)["cadets"][0]
    assert _cells(cara, "volunteering")[-2:] == ["off", "off"]
    assert _section(cara, "volunteering")["complete"] is True
    assert cara["behind"] is False


def test_stepping_back_shows_earlier_weeks_but_never_future_ones(api, enrolled):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(START, 90)]}, headers=api.as_("cadet"))
    grid = _grid(api, end=(START + timedelta(days=3)).isoformat(), weeks=2)
    assert grid["weeks"] == ["2026-07-20", "2026-07-27"]
    assert grid["cadets"][0]["weeks"][1]["cells"]["volunteering"] == {"state": "done", "minutes": 90}
    # A date after this week is clamped to this week.
    assert _grid(api, end="2027-01-01")["weeks"][-1] == "2026-10-19"


@pytest.mark.parametrize("params", [{"end": "last"}, {"weeks": 0}, {"weeks": 27}])
def test_bad_grid_params_are_422(api, params):
    assert api.get("/activity-logs/grid", params=params, headers=api.as_("staff")).status_code == 422


def test_removing_a_cadet_takes_their_log_with_them(api, enrolled, db):
    _setup(api)
    api.post(f"{ME}/volunteering/entries", json={"entries": [_entry(TODAY)]}, headers=api.as_("cadet"))
    db.delete(db.get(Cadet, 1))
    db.commit()
    assert db.query(ActivityLog).count() == 0 and db.query(ActivityLogEntry).count() == 0
    assert db.query(ActivityLogEnrolment).count() == 1  # Zoë's

