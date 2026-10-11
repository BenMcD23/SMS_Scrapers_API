"""The flying paperwork tracker's endpoints: list, tick off, close."""

from datetime import datetime

import pytest

from conftest import PERSONAS
from database.models import Cadet, FlyingEvent, FlyingPaperwork

AEF = "RAFAC Air Experience Flying (AEF)"


@pytest.fixture
def flying(db):
    db.add_all([Cadet(cin=1, first_name="Amy", last_name="Zed", rank="Cdt", flight="A"),
                Cadet(cin=2, first_name="Bob", last_name="Able")])
    seen = datetime(2026, 10, 11)
    db.add_all([
        FlyingEvent(id=1, bader_event_id="100", title="AEF Day", category=AEF,
                    date_from=datetime(2026, 11, 1, 8), created_at=seen, last_seen_at=seen),
        FlyingEvent(id=2, bader_event_id="200", title="Gliding", category="RAFAC Gliding",
                    date_from=datetime(2026, 10, 20, 8), created_at=seen, last_seen_at=seen),
        FlyingEvent(id=3, bader_event_id="300", title="Undated", category=AEF,
                    created_at=seen, last_seen_at=seen, on_bader=False),
    ])
    db.add_all([FlyingPaperwork(flying_event_id=1, cadet_id=1),
                FlyingPaperwork(flying_event_id=1, cadet_id=2, tg23=True, on_event=False)])
    db.commit()


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method, path", [
    ("get", "/flying-paperwork"),
    ("patch", "/flying-paperwork/1/cadets/1"),
    ("delete", "/flying-paperwork/1"),
])
def test_only_staff_get_in(api, flying, persona, method, path):
    kwargs = {"json": {"tg21": True}} if method == "patch" else {}
    assert getattr(api, method)(path, headers=api.as_(persona), **kwargs).status_code == 403
    assert getattr(api, method)(path, **kwargs).status_code == 401


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_role_can_read(api, flying, persona):
    assert api.get("/flying-paperwork", headers=api.as_(persona)).status_code == 200


def test_events_come_soonest_first_with_cadets_by_surname(api, flying):
    events = api.get("/flying-paperwork", headers=api.as_("staff")).json()
    assert [e["title"] for e in events] == ["Gliding", "AEF Day", "Undated"]
    aef = events[1]
    assert aef["has_ticks"] is True and aef["on_bader"] is True and aef["bader_event_id"] == "100"
    assert [c["last_name"] for c in aef["cadets"]] == ["Able", "Zed"]
    assert aef["cadets"][0] == {
        "cin": 2, "first_name": "Bob", "last_name": "Able", "rank": None, "flight": None,
        "on_event": False, "updated_at": None, "updated_by": None,
        "tg21": False, "tg23": True, "av_med": False,
    }
    assert events[0]["cadets"] == [] and events[0]["has_ticks"] is False
    assert events[2]["on_bader"] is False


def test_no_flying_events_is_an_empty_list(api):
    assert api.get("/flying-paperwork", headers=api.as_("staff")).json() == []


def test_ticking_a_form_records_who_and_leaves_the_others(api, db, flying):
    res = api.patch("/flying-paperwork/1/cadets/1", headers=api.as_("staff"), json={"tg21": True, "av_med": True})
    assert res.status_code == 200
    body = res.json()
    assert (body["tg21"], body["tg23"], body["av_med"]) == (True, False, True)
    assert body["updated_by"] == PERSONAS["staff"]["email"] and body["updated_at"]
    # unticking just the one
    body = api.patch("/flying-paperwork/1/cadets/1", headers=api.as_("staff"), json={"av_med": False}).json()
    assert (body["tg21"], body["av_med"]) == (True, False)


def test_an_empty_update_is_rejected(api, flying):
    res = api.patch("/flying-paperwork/1/cadets/1", headers=api.as_("staff"), json={})
    assert res.status_code == 400


def test_a_non_boolean_tick_is_a_validation_error(api, flying):
    res = api.patch("/flying-paperwork/1/cadets/1", headers=api.as_("staff"), json={"tg21": "maybe"})
    assert res.status_code == 422


@pytest.mark.parametrize("path", ["/flying-paperwork/1/cadets/99", "/flying-paperwork/2/cadets/1",
                                  "/flying-paperwork/99/cadets/1"])
def test_ticking_someone_not_on_the_event_is_404(api, flying, path):
    assert api.patch(path, headers=api.as_("staff"), json={"tg21": True}).status_code == 404


def test_closing_deletes_the_event_and_its_paperwork(api, db, flying):
    assert api.delete("/flying-paperwork/1", headers=api.as_("staff")).status_code == 200
    db.expire_all()
    assert db.get(FlyingEvent, 1) is None
    assert db.query(FlyingPaperwork).count() == 0
    assert db.query(FlyingEvent).count() == 2


def test_closing_a_missing_event_is_404(api, flying):
    assert api.delete("/flying-paperwork/99", headers=api.as_("staff")).status_code == 404
