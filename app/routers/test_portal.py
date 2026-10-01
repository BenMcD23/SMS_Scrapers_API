"""Cadet portal self-service: a cadet (or adult) only ever sees and edits their own."""

from datetime import datetime

import pytest

from core import stock_events
from database.models import (
    BadgeOrder,
    BadgeOrderItem,
    Cadet,
    Staff,
    StaffAttendance,
    StoresItemIssuance,
    StoresOrder,
    StoresOrderItem,
    TextSettings,
    User,
)

CADET = {"Authorization": "Bearer cadet"}
CADET2 = {"Authorization": "Bearer cadet2"}
STAFF = {"Authorization": "Bearer staff"}


@pytest.fixture
def cadets(db):
    db.add_all([
        Cadet(cin=1, first_name="Cara", last_name="Cadet", email="Cadet@317atc.co.uk", rank="Cdt", flight="A"),
        Cadet(cin=2, first_name="Carl", last_name="Cadet", email="cadet2@317atc.co.uk"),
    ])
    db.commit()


ME_ENDPOINTS = [
    ("get", "/cadets/me"),
    ("patch", "/cadets/me/phone-number"),
    ("get", "/cadets/me/orders"),
    ("post", "/cadets/me/orders"),
    ("get", "/cadets/me/issuances"),
    ("get", "/cadets/me/inspections"),
    ("get", "/cadets/me/badge-orders"),
    ("post", "/cadets/me/badge-orders"),
]


@pytest.mark.parametrize("method,path", ME_ENDPOINTS + [("get", "/users/me/orders"), ("get", "/users/me/issuances")])
def test_portal_needs_a_token(api, method, path):
    res = api.get(path) if method == "get" else getattr(api, method)(path, json={})
    assert res.status_code == 401


@pytest.mark.parametrize("method,path", ME_ENDPOINTS)
def test_someone_who_is_not_a_cadet_gets_a_clear_404(api, method, path):
    res = api.get(path, headers=STAFF) if method == "get" else getattr(api, method)(path, json={"items": [],
                                                                                              "phone_number": ""},
                                                                                     headers=STAFF)
    assert res.status_code == 404
    assert "not registered" in res.json()["detail"]


# ── me ────────────────────────────────────────────────────────────────────────

def test_me_matches_by_email_case_insensitively(api, db, cadets):
    db.add(TextSettings(whatsapp_invite_url="https://chat.whatsapp.com/abc"))
    db.commit()
    me = api.get("/cadets/me", headers=CADET).json()
    assert me["cin"] == 1 and me["name"] == "Cara Cadet" and me["phone_number"] == ""
    assert "whatsapp_invite_url" in me


def test_phone_number_set_and_clear(api, db, cadets):
    res = api.patch("/cadets/me/phone-number", json={"phone_number": "07700 900 123"}, headers=CADET)
    assert res.status_code == 200
    assert res.json()["phone_number"]
    assert api.get("/cadets/me", headers=CADET).json()["phone_number"] == res.json()["phone_number"]

    api.patch("/cadets/me/phone-number", json={"phone_number": ""}, headers=CADET)
    assert db.get(Cadet, 1).phone_number is None


@pytest.mark.parametrize("bad", ["hello", "0123", "+1 555 0100"])
def test_phone_number_rejects_non_uk_mobiles(api, cadets, bad):
    assert api.patch("/cadets/me/phone-number", json={"phone_number": bad}, headers=CADET).status_code == 400


def test_phone_number_body_is_validated(api, cadets):
    assert api.patch("/cadets/me/phone-number", json={}, headers=CADET).status_code == 422


# ── uniform orders ────────────────────────────────────────────────────────────

def _uniform(api, items, headers=CADET, base="/cadets/me/orders"):
    return api.post(base, json={"items": items}, headers=headers)


def test_cadet_orders_create_list_and_isolation(api, cadets):
    created = _uniform(api, [{"itemType": "Beret", "size": "55"}, {"itemType": ""}])
    assert created.status_code == 201
    order = created.json()
    assert [i["itemType"] for i in order["items"]] == ["Beret"]
    assert order["cadetCin"] == 1

    assert [o["id"] for o in api.get("/cadets/me/orders", headers=CADET).json()] == [order["id"]]
    # Another cadet sees nothing and can't touch it.
    assert api.get("/cadets/me/orders", headers=CADET2).json() == []
    assert api.patch(f"/cadets/me/orders/{order['id']}", json={"items": []}, headers=CADET2).status_code == 404
    assert api.delete(f"/cadets/me/orders/{order['id']}", headers=CADET2).status_code == 404


def test_create_order_needs_items(api, cadets):
    assert _uniform(api, []).status_code == 400
    assert api.post("/cadets/me/orders", json={}, headers=CADET).status_code == 422
    assert _uniform(api, [{"size": "55"}]).status_code == 422


def test_editing_one_item_keeps_the_others_rows_and_their_qm_history(api, db, cadets):
    order = _uniform(api, [{"itemType": "Beret", "size": "55"}, {"itemType": "Tie"}]).json()
    beret_id, tie_id = (int(i["id"]) for i in order["items"])

    # The QM has taken the tie off the shelf and noted it.
    tie = db.get(StoresOrderItem, tie_id)
    tie.stock_events = stock_events.dump([stock_events.new_event(stock_events.REMOVED, "qm", stockItemId=1)])
    tie.qm_notes = '[{"content": "on the side"}]'
    tie.ready_to_collect = datetime(2026, 1, 1)
    db.commit()

    # The cadet changes the beret size; the tie comes back unchanged.
    res = api.patch(f"/cadets/me/orders/{order['id']}", headers=CADET, json={"items": [
        {"itemType": "Beret", "size": "56"}, {"itemType": "Tie"}]})
    assert res.status_code == 200
    items = {i["itemType"]: i for i in res.json()["items"]}
    assert items["Beret"]["size"] == "56" and int(items["Beret"]["id"]) != beret_id
    assert int(items["Tie"]["id"]) == tie_id
    assert items["Tie"]["qmNotes"] == [{"content": "on the side"}]
    assert items["Tie"]["readyToCollect"] is not None
    assert [e["action"] for e in items["Tie"]["stockEvents"]] == ["removed"]


def test_removing_items_keeps_given_ones(api, db, cadets):
    order = _uniform(api, [{"itemType": "Beret", "size": "55"}, {"itemType": "Tie"}]).json()
    given = db.get(StoresOrderItem, int(order["items"][0]["id"]))
    given.given_at = datetime(2026, 1, 1)
    db.commit()
    res = api.patch(f"/cadets/me/orders/{order['id']}", json={"items": []}, headers=CADET).json()
    assert [i["itemType"] for i in res["items"]] == ["Beret"]


def test_duplicate_items_are_matched_one_for_one(api, cadets):
    order = _uniform(api, [{"itemType": "Tie"}, {"itemType": "Tie"}]).json()
    ids = sorted(i["id"] for i in order["items"])
    res = api.patch(f"/cadets/me/orders/{order['id']}", json={"items": [{"itemType": "Tie"}]}, headers=CADET).json()
    assert len(res["items"]) == 1 and res["items"][0]["id"] in ids


def test_completed_orders_are_locked(api, db, cadets):
    order = _uniform(api, [{"itemType": "Tie"}]).json()
    db.get(StoresOrder, int(order["id"])).completed = True
    db.commit()
    assert api.patch(f"/cadets/me/orders/{order['id']}", json={"items": []}, headers=CADET).status_code == 400
    assert api.delete(f"/cadets/me/orders/{order['id']}", headers=CADET).status_code == 400


def test_cannot_cancel_once_anything_is_given(api, db, cadets):
    order = _uniform(api, [{"itemType": "Tie"}]).json()
    db.get(StoresOrderItem, int(order["items"][0]["id"])).given_at = datetime(2026, 1, 1)
    db.commit()
    res = api.delete(f"/cadets/me/orders/{order['id']}", headers=CADET)
    assert res.status_code == 400 and "given out" in res.json()["detail"]


def test_cancel_order(api, cadets):
    order = _uniform(api, [{"itemType": "Tie"}]).json()
    assert api.delete(f"/cadets/me/orders/{order['id']}", headers=CADET).status_code == 204
    assert api.delete(f"/cadets/me/orders/{order['id']}", headers=CADET).status_code == 404
    assert api.patch("/cadets/me/orders/999", json={"items": []}, headers=CADET).status_code == 404


def test_issuances_are_scoped_to_the_cadet(api, db, cadets):
    db.add_all([
        StoresItemIssuance(cadet_id=1, item_category="Beret", last_given=datetime(2025, 1, 1)),
        StoresItemIssuance(cadet_id=2, item_category="Tie", last_given=datetime(2025, 1, 1)),
    ])
    db.commit()
    assert [i["itemCategory"] for i in api.get("/cadets/me/issuances", headers=CADET).json()] == ["Beret"]


# ── badge orders ──────────────────────────────────────────────────────────────

def test_badge_orders_create_edit_cancel(api, db, cadets):
    created = api.post("/cadets/me/badge-orders", headers=CADET, json={"items": [
        {"badgeName": "Radio – Blue", "gainedWhere": "camp", "gainedDateFrom": "2026-07-01T00:00:00.000Z"},
        {"badgeName": ""},
    ]})
    assert created.status_code == 201
    order = created.json()
    assert [i["badgeName"] for i in order["items"]] == ["Radio – Blue"]
    keep_id = order["items"][0]["id"]

    db.get(BadgeOrderItem, int(keep_id)).qm_notes = '[{"content": "x"}]'
    db.commit()

    # Resending the same badge keeps its row; adding one appends.
    res = api.patch(f"/cadets/me/badge-orders/{order['id']}", headers=CADET, json={"items": [
        {"badgeName": "Radio – Blue", "gainedWhere": "camp", "gainedDateFrom": "2026-07-01T00:00:00.000Z"},
        {"badgeName": "Leadership – Blue", "replacement": True},
    ]}).json()
    by_name = {i["badgeName"]: i for i in res["items"]}
    assert by_name["Radio – Blue"]["id"] == keep_id and by_name["Radio – Blue"]["qmNotes"] == [{"content": "x"}]
    assert by_name["Leadership – Blue"]["replacement"] is True

    assert api.get("/cadets/me/badge-orders", headers=CADET2).json() == []
    assert api.patch(f"/cadets/me/badge-orders/{order['id']}", json={"items": []}, headers=CADET2).status_code == 404
    assert api.delete(f"/cadets/me/badge-orders/{order['id']}", headers=CADET2).status_code == 404
    assert api.delete(f"/cadets/me/badge-orders/{order['id']}", headers=CADET).status_code == 204
    assert api.get("/cadets/me/badge-orders", headers=CADET).json() == []


def test_badge_order_validation(api, cadets):
    assert api.post("/cadets/me/badge-orders", json={"items": []}, headers=CADET).status_code == 400
    bad_date = {"items": [{"badgeName": "x", "gainedDateTo": "soon"}]}
    assert api.post("/cadets/me/badge-orders", json=bad_date, headers=CADET).status_code == 400


def test_completed_badge_order_is_locked(api, db, cadets):
    db.add(BadgeOrder(id=5, cadet_id=1, created_at=datetime.now(), completed=True))
    db.commit()
    assert api.patch("/cadets/me/badge-orders/5", json={"items": []}, headers=CADET).status_code == 400
    assert api.delete("/cadets/me/badge-orders/5", headers=CADET).status_code == 400


# ── inspections ───────────────────────────────────────────────────────────────

def test_my_inspections_with_none_recorded(api, cadets):
    body = api.get("/cadets/me/inspections", headers=CADET).json()
    assert body["cin"] == 1 and body["inspection_count"] == 0 and body["timeline"] == []


# ── adults ────────────────────────────────────────────────────────────────────

def test_adult_orders_are_created_on_first_sight_and_scoped(api, db):
    created = _uniform(api, [{"itemType": "Beret", "size": "57"}], headers=STAFF, base="/users/me/orders")
    assert created.status_code == 201
    order = created.json()
    assert order["subjectType"] == "user" and order["cadetCin"] is None
    assert db.query(User).filter(User.email == "staff@317atc.co.uk").count() == 1

    other = api.as_("staff2")
    assert api.get("/users/me/orders", headers=other).json() == []
    assert api.patch(f"/users/me/orders/{order['id']}", json={"items": []}, headers=other).status_code == 404
    assert api.delete(f"/users/me/orders/{order['id']}", headers=other).status_code == 404

    res = api.patch(f"/users/me/orders/{order['id']}", json={"items": [{"itemType": "Tie"}]}, headers=STAFF).json()
    assert [i["itemType"] for i in res["items"]] == ["Tie"]
    assert api.delete(f"/users/me/orders/{order['id']}", headers=STAFF).status_code == 204
    assert _uniform(api, [], headers=STAFF, base="/users/me/orders").status_code == 400


def test_a_cadet_cannot_reach_another_cadets_order_through_the_user_endpoints(api, db, cadets):
    order = _uniform(api, [{"itemType": "Tie"}]).json()
    # The cadet's own *user* row has no orders; the cadet order isn't reachable here.
    assert api.get("/users/me/orders", headers=CADET2).json() == []
    assert api.delete(f"/users/me/orders/{order['id']}", headers=CADET2).status_code == 404


def test_user_issuances(api, db):
    api.get("/users/me/orders", headers=STAFF)  # creates the User row
    user = db.query(User).filter(User.email == "staff@317atc.co.uk").one()
    db.add(StoresItemIssuance(user_id=user.id, item_category="Beret", last_given=datetime(2025, 1, 1)))
    db.commit()
    assert [i["itemCategory"] for i in api.get("/users/me/issuances", headers=STAFF).json()] == ["Beret"]


# ── staff admin ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/users", "/staff", "/staff/1/attendance"])
@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_admin_lists_are_staff_only(api, path, persona):
    assert api.get(path, headers=api.as_(persona)).status_code == 403


def test_users_list_includes_roles(api, db):
    db.add_all([
        User(google_id="a", email="staff@317atc.co.uk", first_name="S"),
        User(google_id="b", email="nco@317atc.co.uk"),
        User(google_id="c", email="parent@gmail.com"),
    ])
    db.commit()
    roles = {u["email"]: u["role"] for u in api.get("/users", headers=STAFF).json()}
    assert roles == {"staff@317atc.co.uk": "staff", "nco@317atc.co.uk": "nco", "parent@gmail.com": None}


def test_staff_roster_links_portal_users_by_email(api, db):
    db.add_all([
        Staff(cin=10, first_name="A", last_name="One", email="Staff@317atc.co.uk", attendance={"2026-01": 4}),
        Staff(cin=11, first_name="B", last_name="Two", email=None),
        User(google_id="a", email="staff@317atc.co.uk"),
    ])
    db.commit()
    rows = {s["cin"]: s for s in api.get("/staff", headers=STAFF).json()}
    assert rows[10]["userId"] is not None and rows[10]["attendance"] == {"2026-01": 4}
    assert rows[11]["userId"] is None


def test_staff_attendance_newest_first(api, db):
    db.add(Staff(cin=10, first_name="A", last_name="One"))
    db.add_all([
        StaffAttendance(staff_id=10, date=datetime(2026, 1, 1), status="Present"),
        StaffAttendance(staff_id=10, date=datetime(2026, 2, 1), status="Absent"),
    ])
    db.commit()
    rows = api.get("/staff/10/attendance", headers=STAFF).json()
    assert [(r["date"][:7], r["state"]) for r in rows] == [("2026-02", "absent"), ("2026-01", "present")]
