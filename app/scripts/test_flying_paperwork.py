"""Reconciling the flying paperwork tracker with each event scrape: flying
events are picked up, untouched ones go when they leave Bader, and anything
with a form ticked off survives until staff close it."""

from datetime import datetime

import pytest

from database.models import Cadet, FlyingEvent, FlyingPaperwork
from scripts.flying_paperwork import is_flying_category, sync_flying_events

AEF = "RAFAC Air Experience Flying (AEF)"
GLIDING = "RAFAC Gliding"
NOW = datetime(2026, 10, 11, 12)


@pytest.fixture
def cadets(db):
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="Able"),
                Cadet(cin=2, first_name="Bob", last_name="Baker"),
                Cadet(cin=3, first_name="Cat", last_name="Cole")])
    db.commit()


def scraped(event_id="100", category=AEF, cadets=(1, 2), title="AEF Day", **extra):
    return {"event_id": event_id, "title": title, "category": category,
            "date_from": "01/11/2026 08:00", "date_to": "01/11/2026 17:00", "cadets": set(cadets), **extra}


def tracked(db, event_id="100"):
    db.expire_all()
    return db.query(FlyingEvent).filter_by(bader_event_id=event_id).first()


def tick(db, event_id="100", cin=1, field="tg21"):
    row = tracked(db, event_id).cadets
    setattr(next(r for r in row if r.cadet_id == cin), field, True)
    db.commit()


@pytest.mark.parametrize("category, flying", [
    (AEF, True), (GLIDING, True), ("rafac gliding", True),
    ("Road Marching", False), (None, False), ("", False),
])
def test_only_aef_and_gliding_count_as_flying(category, flying):
    assert is_flying_category(category) is flying


def test_a_flying_event_is_tracked_with_its_cadets(db, cadets):
    counts = sync_flying_events(db, [scraped(), scraped("200", "Road Marching", (3,))], now=NOW)
    assert counts == {"tracked": 1, "added": 1, "removed": 0, "kept": 0}
    [fe] = db.query(FlyingEvent).all()
    assert (fe.bader_event_id, fe.title, fe.category) == ("100", "AEF Day", AEF)
    assert fe.date_from == datetime(2026, 11, 1, 8) and fe.on_bader and fe.created_at == NOW
    assert sorted((r.cadet_id, r.on_event, r.tg21, r.tg23, r.av_med) for r in fe.cadets) == [
        (1, True, False, False, False), (2, True, False, False, False)]


def test_a_rescrape_refreshes_details_and_adds_new_cadets_without_losing_ticks(db, cadets):
    sync_flying_events(db, [scraped()], now=NOW)
    tick(db, cin=1, field="av_med")
    later = datetime(2026, 10, 12)
    counts = sync_flying_events(db, [scraped(title="AEF Day (moved)", cadets=(1, 2, 3))], now=later)
    assert counts["added"] == 0
    fe = tracked(db)
    assert fe.title == "AEF Day (moved)" and fe.last_seen_at == later and fe.created_at == NOW
    rows = {r.cadet_id: r for r in fe.cadets}
    assert set(rows) == {1, 2, 3} and rows[1].av_med


def test_a_cadet_who_drops_off_goes_unless_something_was_ticked(db, cadets):
    sync_flying_events(db, [scraped(cadets=(1, 2))], now=NOW)
    tick(db, cin=1)
    sync_flying_events(db, [scraped(cadets=())], now=NOW)
    rows = {r.cadet_id: r for r in tracked(db).cadets}
    assert set(rows) == {1}  # Bob had nothing ticked, so he just goes
    assert rows[1].on_event is False and rows[1].tg21
    # and back on the event clears the flag
    sync_flying_events(db, [scraped(cadets=(1,))], now=NOW)
    assert tracked(db).cadets[0].on_event is True


def test_a_finished_event_with_nothing_ticked_is_deleted(db, cadets):
    sync_flying_events(db, [scraped()], now=NOW)
    counts = sync_flying_events(db, [scraped("200", "Road Marching")], now=NOW)
    assert counts["removed"] == 1
    assert db.query(FlyingEvent).count() == 0 and db.query(FlyingPaperwork).count() == 0


def test_a_finished_event_with_ticks_stays_until_closed(db, cadets):
    sync_flying_events(db, [scraped()], now=NOW)
    tick(db, cin=2, field="tg23")
    counts = sync_flying_events(db, [scraped("200", "Road Marching")], now=NOW)
    assert counts == {"tracked": 0, "added": 0, "removed": 0, "kept": 1}
    fe = tracked(db)
    assert fe.on_bader is False and len(fe.cadets) == 2  # Bader stopped listing it, so the roster is frozen
    # It reappearing on Bader puts it back
    sync_flying_events(db, [scraped()], now=NOW)
    assert tracked(db).on_bader is True


def test_an_unreadable_category_never_deletes_anything(db, cadets):
    """A detail page that didn't fetch this run gives no category. Treating
    that as "not flying" would delete the event — and its cadet list — on
    every flaky run."""
    sync_flying_events(db, [scraped()], now=NOW)
    counts = sync_flying_events(db, [scraped(category=None, cadets=())], now=NOW)
    assert counts == {"tracked": 0, "added": 0, "removed": 0, "kept": 0}
    assert len(tracked(db).cadets) == 2


def test_an_empty_scrape_changes_nothing(db, cadets):
    sync_flying_events(db, [scraped()], now=NOW)
    assert sync_flying_events(db, [], now=NOW)["removed"] == 0
    assert sync_flying_events(db, [scraped(event_id=None)], now=NOW)["removed"] == 0
    assert tracked(db) is not None


def test_an_event_recategorised_away_from_flying_is_treated_as_gone(db, cadets):
    sync_flying_events(db, [scraped()], now=NOW)
    sync_flying_events(db, [scraped(category="Road Marching")], now=NOW)
    assert tracked(db) is None


def test_bad_dates_keep_what_we_had(db, cadets):
    sync_flying_events(db, [scraped()], now=NOW)
    sync_flying_events(db, [scraped(date_from="", date_to="not a date")], now=NOW)
    fe = tracked(db)
    assert fe.date_from == datetime(2026, 11, 1, 8) and fe.date_to == datetime(2026, 11, 1, 17)


def test_a_new_event_with_no_title_or_dates_still_saves(db, cadets):
    sync_flying_events(db, [scraped(title=None, date_from=None, date_to=None, cadets=())], now=NOW)
    fe = tracked(db)
    assert fe.title == "Untitled" and fe.date_from is None and fe.cadets == []
