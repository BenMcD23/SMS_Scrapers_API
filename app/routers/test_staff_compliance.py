"""Staff compliance end to end: the portal sync bringing staff data in, who may
see it, and the weekly email chasing what's due."""

from datetime import date, datetime, timedelta

import pytest

import core.jobs as jobs
from database.models import PushSubscription, Staff, StaffComplianceAlert, StaffPortalData

ME, OTHER, NO_EMAIL = 3000, 3001, 3002
TODAY = date.today()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat() + "T00:00:00"


def _tracker(*modules) -> dict:
    return {"mandatoryTraining": [
        {"criteriaId": n, "displayName": name, "mandatory": True,
         "dateAchieved": _iso(-400) if expires is not None else None,
         "dateExpires": _iso(expires) if expires is not None else None}
        for n, (name, expires) in enumerate(modules, 1)
    ]}


@pytest.fixture
def staff(db):
    db.add_all([
        Staff(cin=ME, first_name="Sam", last_name="Staff", rank="CI", email="staff@317atc.co.uk"),
        Staff(cin=OTHER, first_name="Órla", last_name="Ní Bhriain", rank="Fg Off", email="orla@example.com"),
        Staff(cin=NO_EMAIL, first_name="Pat", last_name="Nomail", rank="Sgt", email=None),
    ])
    db.commit()


def _sync(api, staff, cadets=(), persona="staff"):
    return api.post("/vp-sync", json={"cadets": list(cadets), "staff": staff}, headers=api.as_(persona))


def _stored(db, cin, dataset):
    db.expire_all()
    row = db.query(StaffPortalData).filter_by(staff_id=cin, dataset=dataset).one_or_none()
    return row.data if row else None


# ── the sync ──────────────────────────────────────────────────────────────────

def test_a_sync_stores_each_staff_data_set(api, db, staff):
    r = _sync(api, [{"cin": ME, "data": {"dbs": {"dateValidFrom": _iso(-100)}, "bpss": [],
                                         "training": _tracker(("Safeguarding", 300))}}])
    assert r.status_code == 200
    assert (r.json()["staff"], r.json()["saved"]) == (1, 3)
    assert _stored(db, ME, "dbs") == {"dateValidFrom": _iso(-100)}


def test_a_sync_without_staff_still_works(api, db):
    # A sync page from before staff were read posts only cadets.
    r = api.post("/vp-sync", json={"cadets": []}, headers=api.as_("staff"))
    assert r.status_code == 200 and r.json()["staff"] == 0


def test_staff_not_on_our_roster_are_ignored(api, db, staff):
    r = _sync(api, [{"cin": 999, "data": {"dbs": {"dateValidFrom": _iso(-1)}}}])
    assert r.json()["unmatched"] == 1
    assert db.query(StaffPortalData).count() == 0


def test_a_failed_read_or_an_empty_answer_keeps_the_staff_data_we_had(api, db, staff):
    _sync(api, [{"cin": ME, "data": {"training": _tracker(("Safeguarding", 300))}}])
    r = _sync(api, [{"cin": ME, "data": {"training": None}}])
    assert r.json()["failed"] == 1
    r = _sync(api, [{"cin": ME, "data": {"training": {}}}])
    assert r.json()["kept"] == 1
    assert _stored(db, ME, "training") == _tracker(("Safeguarding", 300))


def test_a_staff_member_sent_twice_is_one_row(api, db, staff):
    _sync(api, [{"cin": ME, "data": {"bpss": [{"checkDate": "2019-01-01"}]}},
                {"cin": ME, "data": {"bpss": [{"checkDate": "2020-01-01"}]}}])
    assert db.query(StaffPortalData).count() == 1
    assert _stored(db, ME, "bpss") == [{"checkDate": "2020-01-01"}]


@pytest.mark.parametrize("staff_row", [
    {"cin": ME, "data": {"whts": []}},              # a cadet data set
    {"cin": 0, "data": {"dbs": {}}},
    {"cin": ME, "data": {"dbs": {}}, "extra": 1},
])
def test_a_malformed_staff_entry_is_422(api, db, staff, staff_row):
    assert _sync(api, [staff_row]).status_code == 422
    assert db.query(StaffPortalData).count() == 0


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_can_send_staff_data(api, db, staff, persona):
    assert _sync(api, [{"cin": ME, "data": {"bpss": []}}], persona=persona).status_code == 403
    assert db.query(StaffPortalData).count() == 0


# ── the page ──────────────────────────────────────────────────────────────────

def test_no_token_is_401(api):
    assert api.get("/staff/compliance").status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_only_staff_see_compliance(api, persona):
    assert api.get("/staff/compliance", headers=api.as_(persona)).status_code == 403


def test_the_oc_sees_everyone_with_their_worst_status(api, db, staff):
    _sync(api, [
        {"cin": ME, "data": {"dbs": {"dateValidFrom": _iso(-100)}, "training": _tracker(("Prevent", -5))}},
        {"cin": OTHER, "data": {"dbs": {"dateValidFrom": _iso(-100)}}},
    ])
    body = api.get("/staff/compliance", headers=api.as_("oc")).json()
    assert body["everyone"] is True and body["due_within_days"] == 60
    by_cin = {s["cin"]: s for s in body["staff"]}
    assert by_cin[ME]["status"] == "expired"
    assert by_cin[OTHER]["status"] == "ok"
    assert by_cin[OTHER]["name"] == "Órla Ní Bhriain"
    # Never synced: no items, rather than everything "missing".
    assert by_cin[NO_EMAIL]["items"] is None and by_cin[NO_EMAIL]["status"] is None
    assert "email" not in by_cin[ME]
    dbs = next(i for i in by_cin[OTHER]["items"] if i["key"] == "dbs")
    assert dbs["achieved"] == _iso(-100)[:10]


def test_other_staff_see_only_their_own_row(api, db, staff):
    _sync(api, [{"cin": ME, "data": {"bpss": []}}, {"cin": OTHER, "data": {"bpss": []}}])
    body = api.get("/staff/compliance", headers=api.as_("staff")).json()
    assert body["everyone"] is False
    assert [s["cin"] for s in body["staff"]] == [ME]


def test_staff_not_on_the_roster_see_nothing_rather_than_an_error(api, db, staff):
    body = api.get("/staff/compliance", headers=api.as_("staff2")).json()
    assert body["staff"] == []


def test_with_no_staff_at_all_the_oc_gets_an_empty_list(api):
    assert api.get("/staff/compliance", headers=api.as_("oc")).json()["staff"] == []


# ── the weekly email ──────────────────────────────────────────────────────────

@pytest.fixture
def oc_email(monkeypatch):
    monkeypatch.setattr(jobs, "OC_EMAIL", "oc@317atc.co.uk, adj@317atc.co.uk")


def _store(db, cin, **datasets):
    for name, data in datasets.items():
        db.add(StaffPortalData(staff_id=cin, dataset=name, data=data, synced_at=datetime.now()))
    db.commit()


def test_each_person_is_emailed_their_own_due_items_and_the_oc_gets_everyone(db, staff, outbox, oc_email):
    _store(db, ME, training=_tracker(("Safeguarding", 30), ("Fire safety", 400)))
    _store(db, OTHER, dbs={"dateValidFrom": _iso(-1200)})  # issued 3+ years ago: lapsed
    jobs.staff_compliance_alert()

    to = {m["to"]: m for m in outbox}
    assert set(to) == {"staff@317atc.co.uk", "orla@example.com", "oc@317atc.co.uk", "adj@317atc.co.uk"}
    mine = to["staff@317atc.co.uk"]["html"]
    assert "Hi Sam" in mine and "Safeguarding" in mine and "Fire safety" not in mine and "Ní Bhriain" not in mine
    assert "Expired" in to["orla@example.com"]["html"]
    digest = to["oc@317atc.co.uk"]
    assert "(2)" in digest["subject"]
    assert "Sam Staff" in digest["html"] and "Órla Ní Bhriain" in digest["html"]


def test_nobody_is_emailed_twice_about_the_same_thing(db, staff, outbox, oc_email):
    _store(db, ME, training=_tracker(("Safeguarding", 30)))
    jobs.staff_compliance_alert()
    sent = len(outbox)
    jobs.staff_compliance_alert()
    assert len(outbox) == sent
    assert db.query(StaffComplianceAlert).count() == 1


def test_lapsing_after_being_due_is_news_again(db, staff, outbox, oc_email):
    _store(db, ME, training=_tracker(("Safeguarding", 30)))
    jobs.staff_compliance_alert()
    # Same expiry date, now passed: the "expired" stage hasn't been sent yet.
    row = db.query(StaffPortalData).one()
    row.data = _tracker(("Safeguarding", -1))
    db.commit()
    outbox.clear()
    jobs.staff_compliance_alert()
    assert "staff@317atc.co.uk" in {m["to"] for m in outbox}


def test_a_renewal_that_lapses_later_is_alerted_afresh(db, staff, outbox, oc_email):
    db.add(StaffComplianceAlert(staff_id=ME, item="training:1", expires_on=TODAY + timedelta(days=-400),
                                stage="expiring", sent_at=datetime.now()))
    _store(db, ME, training=_tracker(("Safeguarding", 30)))
    jobs.staff_compliance_alert()
    assert "staff@317atc.co.uk" in {m["to"] for m in outbox}


def test_only_missing_items_send_nothing(db, staff, outbox, oc_email):
    # "Not done" has no date to key a reminder on; it stays on the page and
    # rides along in the OC's digest when there is news, but never emails alone.
    _store(db, ME, bpss=[], training=_tracker(("Prevent", None)))
    jobs.staff_compliance_alert()
    assert outbox == []


def test_nothing_due_sends_nothing(db, staff, outbox, oc_email):
    _store(db, ME, dbs={"dateValidFrom": _iso(-10)}, training=_tracker(("Safeguarding", 900)))
    jobs.staff_compliance_alert()
    assert outbox == []


def test_someone_with_no_email_still_reaches_the_oc(db, staff, outbox, oc_email):
    _store(db, NO_EMAIL, training=_tracker(("Safeguarding", 10)))
    jobs.staff_compliance_alert()
    assert {m["to"] for m in outbox} == {"oc@317atc.co.uk", "adj@317atc.co.uk"}
    assert "Pat Nomail" in outbox[0]["html"]
    assert db.query(StaffComplianceAlert).count() == 1


def test_with_no_oc_configured_people_are_still_told(db, staff, outbox, monkeypatch):
    monkeypatch.setattr(jobs, "OC_EMAIL", "")
    _store(db, ME, training=_tracker(("Safeguarding", 10)))
    jobs.staff_compliance_alert()
    assert [m["to"] for m in outbox] == ["staff@317atc.co.uk"]


def test_a_database_error_is_logged_not_raised(db, staff, outbox, monkeypatch, caplog):
    def broken(*a, **k):
        raise RuntimeError("database gone")

    monkeypatch.setattr(jobs, "compliance_rows", broken)
    jobs.staff_compliance_alert()
    assert outbox == [] and "staff compliance alert failed" in caplog.text


def test_a_phone_signed_up_for_compliance_gets_a_push(auth, db, staff, outbox, oc_email, pushes):
    db.add(PushSubscription(email="staff@317atc.co.uk", endpoint="https://push.example/me", p256dh="k",
                            auth="a", topics=["compliance"], created_at=datetime.now()))
    db.add(PushSubscription(email="orla@example.com", endpoint="https://push.example/orla", p256dh="k",
                            auth="a", topics=["compliance"], created_at=datetime.now()))
    db.commit()
    _store(db, ME, training=_tracker(("Safeguarding", 10)))
    jobs.staff_compliance_alert()
    assert [m["title"] for m in pushes.to("https://push.example/me")] == ["DBS or training due"]
    # Órla has nothing due, so her phone hears nothing about Sam's training.
    assert pushes.to("https://push.example/orla") == []
