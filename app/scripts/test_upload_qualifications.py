"""The Bader qualification uploader, with Bader faked out — in particular the
badge order it puts in once a qualification is on Bader."""

import json
import threading
from datetime import datetime

import pytest

import scripts.scraper_calls as calls
from database.models import AssessmentSheet, BadgeOrder, BadgeOrderItem, Cadet, User


class Run:
    def __init__(self):
        self.messages, self.lock, self.stop = [], threading.Lock(), threading.Event()

    def of(self, kind):
        return [m["value"] for m in map(json.loads, self.messages) if m["type"] == kind]


@pytest.fixture
def bader(monkeypatch):
    """Fake browser; records each qualification "added" to Bader."""
    added = []

    class Ctx:
        def close(self):
            pass

    monkeypatch.setattr(calls, "init_scraper", lambda user_id, db: ("page", Ctx(), {}))
    monkeypatch.setattr(calls, "login", lambda *a, **k: None)
    monkeypatch.setattr(
        calls, "add_qualification_with_attachment",
        lambda **kw: added.append((kw["cadet_cin"], kw["qualification_name"], kw["award_date"])),
    )
    return added


def _seed(db, assessment_type="Blue Leadership", date="2026-09-14", n=1, cin=1001):
    if not db.get(Cadet, cin):
        db.add(Cadet(cin=cin, first_name="Zoë", last_name="Ó Briain"))
    if not db.get(User, 1):
        db.add(User(id=1, google_id="g-staff", email="staff@317atc.co.uk"))
    sheets = [
        AssessmentSheet(
            assessment_type=assessment_type, fields={"date": date, "passed": True},
            pdf_data=b"%PDF-1.4", created_at=datetime(2026, 9, 14), cadet_id=cin, assessor_id=1,
        )
        for _ in range(n)
    ]
    db.add_all(sheets)
    db.commit()
    return [s.id for s in sheets]


def _upload(db, ids):
    run = Run()
    calls.upload_qualifications_scraper(run.messages, run.lock, 1, db, run.stop, assessment_ids=ids)
    return run


def _items(db):
    db.expire_all()
    return db.query(BadgeOrderItem).join(BadgeOrder).all()


@pytest.mark.parametrize("assessment_type, badge", [
    ("Blue Leadership", "Leadership – Blue"),
    ("Blue Radio", "Radio – Blue"),
    ("Blue Space", "Space – Blue"),
])
def test_uploading_an_assessment_orders_the_badge_it_earns(db, bader, assessment_type, badge):
    ids = _seed(db, assessment_type, n=2)
    run = _upload(db, ids)

    assert len(bader) == 1 and run.of("error") == []
    [item] = _items(db)
    assert (item.order.cadet_id, item.badge_name, item.replacement) == (1001, badge, False)
    # Gained on the squadron, on the day of the assessment.
    assert item.gained_where == "on_sqn"
    assert item.gained_date_from == item.gained_date_to == datetime(2026, 9, 14)
    assert any(badge in m for m in run.of("info"))


def test_moi_has_no_badge_so_nothing_is_ordered(db, bader):
    _upload(db, _seed(db, "MOI", n=2))
    assert len(bader) == 1 and _items(db) == []


def test_a_cadet_who_already_has_the_badge_ordered_does_not_get_a_second(db, bader):
    order = BadgeOrder(cadet_id=1001, created_at=datetime(2026, 1, 1))
    ids = _seed(db)
    db.add(order)
    db.flush()
    db.add(BadgeOrderItem(order_id=order.id, badge_name="Leadership – Blue", qm_notes="[]"))
    db.commit()

    _upload(db, ids)
    assert [i.badge_name for i in _items(db)] == ["Leadership – Blue"]


def test_reuploading_after_a_reopen_does_not_order_twice(db, bader):
    ids = _seed(db)
    _upload(db, ids)
    _upload(db, ids)
    assert len(bader) == 2 and len(_items(db)) == 1


def test_a_replacement_order_does_not_stop_the_earned_badge_being_ordered(db, bader):
    # A replacement is for a lost badge, so it says nothing about this one.
    ids = _seed(db)
    order = BadgeOrder(cadet_id=1001, created_at=datetime(2026, 1, 1))
    db.add(order)
    db.flush()
    db.add(BadgeOrderItem(order_id=order.id, badge_name="Leadership – Blue", replacement=True, qm_notes="[]"))
    db.commit()

    _upload(db, ids)
    assert sorted(i.replacement for i in _items(db)) == [False, True]


def test_each_cadet_in_one_job_gets_their_own_order(db, bader):
    ids = _seed(db, cin=1001) + _seed(db, "Blue Radio", cin=1002)
    _upload(db, ids)
    assert {(i.order.cadet_id, i.badge_name) for i in _items(db)} == {
        (1001, "Leadership – Blue"), (1002, "Radio – Blue")}


def test_an_unreadable_date_still_orders_the_badge_without_dates(db, bader):
    _upload(db, _seed(db, date="sometime in september"))
    [item] = _items(db)
    assert item.gained_date_from is None and item.gained_date_to is None
    # Bader still gets the date as typed, as before.
    assert bader[0][2] == "sometime in september"


def test_a_failed_order_is_a_warning_and_keeps_the_upload(db, bader, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(calls, "order_badge_for_upload", broken)
    ids = _seed(db)
    run = _upload(db, ids)

    assert run.of("error") == []
    assert any("Couldn't create the badge order" in w for w in run.of("warning"))
    db.expire_all()
    assert all(db.get(AssessmentSheet, i).uploaded for i in ids)


def test_a_failed_bader_upload_orders_nothing(db, bader, monkeypatch):
    def refuse(**kw):
        raise RuntimeError("Bader said no")

    monkeypatch.setattr(calls, "add_qualification_with_attachment", refuse)
    ids = _seed(db)
    run = _upload(db, ids)

    assert any("Bader said no" in e for e in run.of("error"))
    assert _items(db) == []
    db.expire_all()
    assert not db.get(AssessmentSheet, ids[0]).uploaded
