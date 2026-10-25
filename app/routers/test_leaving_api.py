"""Leaving process over HTTP (OC only): who gets listed, marking the parents
notified, asking the adjutant to remove the cadet, cancelling, and the
parent-email list on the OC dashboard. The date arithmetic itself is pinned in
test_leaving.py."""

from datetime import datetime, timedelta

import pytest

import routers.leaving as leaving
from database.models import Cadet, CadetAttendance, CadetLeavingProcess

OC = {"Authorization": "Bearer oc"}
TONIGHT = datetime(2026, 9, 30)
ADJ = "adj@317atc.co.uk"


def _parade(db, cin, days_ago, status="Present Correctly Dressed", register="Parade Night"):
    db.add(CadetAttendance(cadet_id=cin, date=TONIGHT - timedelta(days=days_ago), status=status,
                           register_type=register))


@pytest.fixture
def squadron(db):
    db.add_all([
        Cadet(cin=1, first_name="Here", last_name="Always", parent_emails="here.parent@x.co"),
        Cadet(cin=2, first_name="Gone", last_name="Away", rank="Cdt", flight="B",
              parent_emails="mum@x.co;dad@x.co"),
        Cadet(cin=3, first_name="Excused", last_name="Exams"),
        Cadet(cin=4, first_name="Brand", last_name="New"),
        Cadet(cin=5, first_name="Camp", last_name="Only"),
        Cadet(cin=6, first_name="Seven", last_name="Weeks"),
    ])
    _parade(db, 1, 0)
    _parade(db, 2, 63)                               # nine weeks
    _parade(db, 2, 0, status="Absent")               # absences don't break the gap
    _parade(db, 3, 0, status="Authorised Absence")   # authorised absence does
    _parade(db, 3, 80)
    _parade(db, 5, 0, register="Camp")               # other registers don't count
    _parade(db, 5, 70)
    _parade(db, 6, 49)                               # seven weeks: not yet lapsed
    db.commit()


@pytest.fixture
def adj(monkeypatch):
    monkeypatch.setattr(leaving, "ADJUTANT_EMAIL", ADJ)


def _row(api, cin):
    return {c["cin"]: c for c in api.get("/leaving-process", headers=OC).json()["cadets"]}.get(cin)


def _age_process(db, cin, days):
    p = db.query(CadetLeavingProcess).filter_by(cadet_id=cin).one()
    p.sent_at = datetime.now() - timedelta(days=days)
    db.commit()


# ---- access ----------------------------------------------------------------

@pytest.mark.parametrize("persona", ["staff", "owner", "snco", "nco", "cadet"])
def test_oc_only(api, squadron, persona):
    h = api.as_(persona)
    assert api.get("/leaving-process", headers=h).status_code == 403
    assert api.post("/leaving-process/2/notified", headers=h).status_code == 403
    assert api.post("/leaving-process/2/request-adj", headers=h).status_code == 403
    assert api.delete("/leaving-process/2", headers=h).status_code == 403
    assert api.get("/oc/parent-emails", headers=h).status_code == 403
    assert api.put("/oc/parent-emails/2", json={"emails": "a@b.co"}, headers=h).status_code == 403


def test_no_token_is_401(api):
    assert api.get("/leaving-process").status_code == 401
    assert api.get("/oc/parent-emails").status_code == 401


# ---- listing ---------------------------------------------------------------

def test_list_flags_only_cadets_absent_eight_weeks(api, squadron):
    body = api.get("/leaving-process", headers=OC).json()
    assert body["currentParadeNight"] == TONIGHT.isoformat()
    rows = {c["cin"]: c for c in body["cadets"]}
    # 6 (seven weeks) and 3 (excused) are not listed; 2 and 5 are.
    assert set(rows) == {2, 5}
    gone = rows[2]
    assert (gone["gapDays"], gone["weeksAbsent"], gone["status"], gone["returned"]) == (63, 9, "flagged", False)
    assert gone["notifiedAt"] is None and gone["parentEmails"] == ["mum@x.co", "dad@x.co"]
    assert rows[5]["parentEmails"] == []


def test_list_with_no_registers_at_all(api, db):
    db.add(Cadet(cin=1, first_name="A", last_name="B"))
    db.commit()
    assert api.get("/leaving-process", headers=OC).json() == {
        "currentParadeNight": None, "adjutantConfigured": leaving.ADJUTANT_EMAIL != "", "cadets": []}


def test_a_hand_edited_bad_parent_address_does_not_break_the_list(api, db, squadron):
    db.get(Cadet, 2).parent_emails = "good@x.co;garbage"
    db.commit()
    assert _row(api, 2)["parentEmails"] == ["good@x.co"]


# ---- notifying parents -----------------------------------------------------

def test_marking_notified_starts_the_clock_and_cancel_ends_it(api, db, squadron, outbox):
    res = api.post("/leaving-process/2/notified", headers=OC)
    assert res.status_code == 200 and res.json()["ok"] is True

    row = _row(api, 2)
    assert row["status"] == "waiting" and row["daysLeft"] == 14
    assert row["notifiedBy"] == "oc@317atc.co.uk"
    assert db.query(CadetLeavingProcess).one().sent_to == "mum@x.co;dad@x.co"

    assert api.post("/leaving-process/2/notified", headers=OC).status_code == 409

    assert api.delete("/leaving-process/2", headers=OC).status_code == 204
    assert api.delete("/leaving-process/2", headers=OC).status_code == 404
    assert db.query(CadetLeavingProcess).count() == 0
    # The site never emails parents — the OC does that from their own account.
    assert outbox == []


def test_notified_works_for_a_cadet_with_no_parent_email(api, db, squadron):
    assert api.post("/leaving-process/5/notified", headers=OC).status_code == 200
    assert db.query(CadetLeavingProcess).one().sent_to == ""


def test_notified_unknown_cadet(api):
    assert api.post("/leaving-process/99/notified", headers=OC).status_code == 404


def test_a_returned_cadet_stays_listed_until_cancelled(api, db, squadron):
    api.post("/leaving-process/2/notified", headers=OC)
    _parade(db, 2, 0, status="Present Incorrectly Dressed")
    db.commit()
    row = _row(api, 2)
    assert row["returned"] is True and row["gapDays"] == 0


# ---- asking the adjutant ---------------------------------------------------

def test_request_adj_emails_the_adjutant_once_notice_has_run_out(api, db, squadron, adj, outbox):
    api.post("/leaving-process/2/notified", headers=OC)
    _age_process(db, 2, 14)
    assert _row(api, 2)["status"] == "start_leaving"

    res = api.post("/leaving-process/2/request-adj", headers=OC)
    assert res.status_code == 200 and res.json()["ok"] is True

    assert len(outbox) == 1
    mail = outbox[0]
    assert mail["to"] == ADJ and mail["reply_to"] == "oc@317atc.co.uk"
    assert "Gone Away" in mail["subject"] and "2" in mail["subject"]
    assert "9 weeks" in mail["html"]
    assert _row(api, 2)["adjRequestedAt"] is not None
    row = db.query(CadetLeavingProcess).one()
    assert row.adj_sent_to == ADJ and row.adj_requested_by == "oc@317atc.co.uk"

    # Asking twice would nag the adjutant.
    assert api.post("/leaving-process/2/request-adj", headers=OC).status_code == 409
    assert len(outbox) == 1


def test_request_adj_refused_while_notice_is_running(api, db, squadron, adj, outbox):
    api.post("/leaving-process/2/notified", headers=OC)
    _age_process(db, 2, 13)
    res = api.post("/leaving-process/2/request-adj", headers=OC)
    assert res.status_code == 409 and "1 day" in res.json()["detail"]
    assert outbox == [] and db.query(CadetLeavingProcess).one().adj_requested_at is None


def test_request_adj_without_a_process_is_404(api, squadron, adj, outbox):
    assert api.post("/leaving-process/2/request-adj", headers=OC).status_code == 404
    assert outbox == []


def test_request_adj_without_an_adjutant_address_sends_and_records_nothing(api, db, squadron, monkeypatch, outbox):
    monkeypatch.setattr(leaving, "ADJUTANT_EMAIL", "")
    api.post("/leaving-process/2/notified", headers=OC)
    _age_process(db, 2, 20)
    res = api.post("/leaving-process/2/request-adj", headers=OC)
    assert res.status_code == 503 and "ADJUTANT_EMAIL" in res.json()["detail"]
    assert outbox == [] and db.query(CadetLeavingProcess).one().adj_requested_at is None


def test_request_adj_refused_for_a_cadet_who_came_back(api, db, squadron, adj, outbox):
    api.post("/leaving-process/2/notified", headers=OC)
    _age_process(db, 2, 20)
    _parade(db, 2, 0)
    db.commit()
    res = api.post("/leaving-process/2/request-adj", headers=OC)
    assert res.status_code == 409 and "back on parade" in res.json()["detail"]
    assert outbox == []


# ---- parent emails on the OC dashboard -------------------------------------

def test_parent_emails_bcc_is_semicolon_separated_and_deduplicated(api, db, squadron):
    api.put("/oc/parent-emails/3", json={"emails": "MUM@x.co, new@x.co"}, headers=OC)
    body = api.get("/oc/parent-emails", headers=OC).json()
    # mum@x.co appears on two cadets (siblings) but is listed once.
    assert body["bcc"] == "here.parent@x.co;mum@x.co;dad@x.co;new@x.co" or \
        set(body["bcc"].split(";")) == {"here.parent@x.co", "mum@x.co", "dad@x.co", "new@x.co"}
    assert body["count"] == 4 and len(body["bcc"].split(";")) == 4
    # Cadets 4, 5, 6 still have none.
    assert body["missing"] == 3


def test_parent_emails_with_nobody_on_file(api, db):
    db.add(Cadet(cin=1, first_name="A", last_name="B"))
    db.commit()
    body = api.get("/oc/parent-emails", headers=OC).json()
    assert (body["bcc"], body["count"], body["missing"]) == ("", 0, 1)


def test_set_parent_emails_normalises_and_can_clear(api, db, squadron):
    res = api.put("/oc/parent-emails/4", json={"emails": " a@x.co;\nb@x.co ; a@x.co"}, headers=OC)
    assert res.status_code == 200 and res.json()["parentEmails"] == ["a@x.co", "b@x.co"]
    assert db.get(Cadet, 4).parent_emails == "a@x.co;b@x.co"

    assert api.put("/oc/parent-emails/4", json={"emails": ""}, headers=OC).status_code == 200
    db.expire_all()
    assert db.get(Cadet, 4).parent_emails is None


def test_set_parent_emails_rejects_a_typo_and_keeps_the_old_value(api, db, squadron):
    res = api.put("/oc/parent-emails/2", json={"emails": "fine@x.co; nope"}, headers=OC)
    assert res.status_code == 400 and "nope" in res.json()["detail"]
    db.expire_all()
    assert db.get(Cadet, 2).parent_emails == "mum@x.co;dad@x.co"


def test_set_parent_emails_unknown_cadet_and_bad_body(api):
    assert api.put("/oc/parent-emails/99", json={"emails": "a@b.co"}, headers=OC).status_code == 404
    assert api.put("/oc/parent-emails/99", json={"emails": 5}, headers=OC).status_code == 422


def test_non_ascii_names_survive(api, db):
    db.add(Cadet(cin=7, first_name="Zoë", last_name="Ó Briain", parent_emails="zoe@x.co"))
    db.commit()
    names = [c["name"] for c in api.get("/oc/parent-emails", headers=OC).json()["cadets"]]
    assert names == ["Zoë Ó Briain"]


# ---- the saved leaver email -------------------------------------------------

def test_leaving_email_is_empty_until_saved_then_round_trips_and_resets(api):
    assert api.get("/oc/leaving-email", headers=OC).json() == {"subject": None, "body": None}

    mail = {"subject": "Is {{ cadet_name }} still with us?", "body": "Dear parent,\n\n{{ cadet_name }} ..."}
    assert api.put("/oc/leaving-email", json=mail, headers=OC).json() == mail
    assert api.get("/oc/leaving-email", headers=OC).json() == mail

    mail2 = {"subject": "", "body": "Second save replaces the first"}
    api.put("/oc/leaving-email", json=mail2, headers=OC)
    assert api.get("/oc/leaving-email", headers=OC).json() == mail2

    assert api.delete("/oc/leaving-email", headers=OC).json() == {"subject": None, "body": None}
    assert api.get("/oc/leaving-email", headers=OC).json() == {"subject": None, "body": None}
    # Resetting when nothing is saved is fine.
    assert api.delete("/oc/leaving-email", headers=OC).status_code == 200


@pytest.mark.parametrize("payload", [
    {"subject": "s", "body": ""},
    {"subject": "s", "body": "x" * 5001},
    {"subject": "x" * 201, "body": "b"},
    {"body": "no subject"},
])
def test_leaving_email_rejects_bad_input(api, payload):
    assert api.put("/oc/leaving-email", json=payload, headers=OC).status_code == 422
    assert api.get("/oc/leaving-email", headers=OC).json()["body"] is None


def test_leaving_email_is_oc_only(api):
    assert api.get("/oc/leaving-email").status_code == 401
    h = api.as_("staff")
    assert api.get("/oc/leaving-email", headers=h).status_code == 403
    assert api.put("/oc/leaving-email", json={"subject": "", "body": "x"}, headers=h).status_code == 403
    assert api.delete("/oc/leaving-email", headers=h).status_code == 403


def test_a_damaged_saved_email_reads_as_nothing_saved(api, db):
    api.put("/oc/leaving-email", json={"subject": "", "body": "x"}, headers=OC)
    from database.models import DashboardLayout
    db.query(DashboardLayout).filter_by(dashboard="oc-leaving-email").one().sections = "{not json"
    db.commit()
    assert api.get("/oc/leaving-email", headers=OC).json() == {"subject": None, "body": None}
