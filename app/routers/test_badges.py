"""Badge stores: the storage grid, badge orders and the supplier order list."""

from datetime import datetime

import pytest

import routers.badges as badges
from database.models import BadgeGridCell, BadgeItem, BadgeOrderItem, Cadet, CadetQualification

STAFF = {"Authorization": "Bearer staff"}

STAFF_ONLY = [
    ("get", "/stores/badges"),
    ("patch", "/stores/badges/config"),
    ("post", "/stores/badges/cells"),
    ("delete", "/stores/badges/cells/1"),
    ("patch", "/stores/badges/cells/1"),
    ("post", "/stores/badges/cells/1/items"),
    ("patch", "/stores/badges/items/1"),
    ("delete", "/stores/badges/items/1"),
    ("delete", "/stores/badges/rows/0"),
    ("delete", "/stores/badges/cols/0"),
    ("get", "/stores/badges/orders"),
    ("post", "/stores/badges/orders"),
    ("patch", "/stores/badges/orders/1"),
    ("delete", "/stores/badges/orders/1"),
    ("post", "/stores/badges/orders/1/items/1/mark-ready"),
    ("post", "/stores/badges/orders/1/items/1/stock"),
    ("get", "/stores/badges/order-lists"),
    ("post", "/stores/badges/order-lists/entries"),
    ("delete", "/stores/badges/order-lists/entries/1"),
    ("post", "/stores/badges/order-lists/entries/1/mark-ordered"),
    ("post", "/stores/badges/order-lists/entries/1/mark-received"),
]


def _req(api, method, path, headers):
    if method in ("get", "delete"):
        return getattr(api, method)(path, headers=headers)
    return getattr(api, method)(path, json={}, headers=headers)


@pytest.mark.parametrize("method,path", STAFF_ONLY)
def test_badge_endpoints_need_a_token(api, method, path):
    assert _req(api, method, path, {}).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method,path", STAFF_ONLY)
def test_badge_endpoints_are_staff_only(api, persona, method, path):
    assert _req(api, method, path, api.as_(persona)).status_code == 403


# ── helpers ───────────────────────────────────────────────────────────────────

def _grid(api, rows, cols):
    return api.patch("/stores/badges/config", json={"numRows": rows, "numCols": cols}, headers=STAFF).json()


def _cell(grid, row, col):
    return next(c for c in grid["cells"] if (c["row"], c["col"]) == (row, col))


def _cadet(db, cin=1, **kw):
    db.add(Cadet(cin=cin, **{"first_name": "Amy", "last_name": "Adams", "email": f"c{cin}@x", **kw}))
    db.commit()


def _order(api, cin=1, items=None):
    items = items if items is not None else [{"badgeName": "Leadership – Blue"}]
    res = api.post("/stores/badges/orders", json={"cadetCin": cin, "items": items}, headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


# ── timestamps ────────────────────────────────────────────────────────────────

def test_parse_timestamp_accepts_browser_iso_and_naive():
    assert badges._parse_timestamp(None) is None
    assert badges._parse_timestamp("") is None
    assert badges._parse_timestamp("2026-05-01T10:00:00") == datetime(2026, 5, 1, 10, 0)
    # "Z" (what toISOString() emits) is accepted and stored naive.
    parsed = badges._parse_timestamp("2026-05-01T10:00:00.000Z")
    assert parsed.tzinfo is None


def test_parse_timestamp_rejects_garbage_with_400():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        badges._parse_timestamp("next tuesday")
    assert e.value.status_code == 400


# ── grid ──────────────────────────────────────────────────────────────────────

def test_first_load_creates_a_1x1_config(api):
    body = api.get("/stores/badges", headers=STAFF).json()
    assert body == {"config": {"numRows": 1, "numCols": 1}, "cells": []}


def test_config_fills_every_position_and_clamps_to_one(api):
    grid = _grid(api, 2, 3)
    assert grid["config"] == {"numRows": 2, "numCols": 3}
    assert [(c["row"], c["col"]) for c in grid["cells"]] == [(r, c) for r in range(2) for c in range(3)]
    # Shrinking below 1 is clamped; existing cells are left alone.
    grid = _grid(api, 0, -4)
    assert grid["config"] == {"numRows": 1, "numCols": 1}
    assert len(grid["cells"]) == 6


def test_create_cell_rejects_occupied_positions(api):
    first = api.post("/stores/badges/cells", json={"row": 4, "col": 4, "label": ""}, headers=STAFF)
    assert first.status_code == 201 and first.json()["label"] is None
    assert api.post("/stores/badges/cells", json={"row": 4, "col": 4}, headers=STAFF).status_code == 400


def test_moving_a_cell_onto_another_swaps_them(api):
    grid = _grid(api, 1, 2)
    left, right = _cell(grid, 0, 0), _cell(grid, 0, 1)
    api.patch(f"/stores/badges/cells/{left['id']}", json={"label": "L"}, headers=STAFF)
    grid = api.patch(f"/stores/badges/cells/{left['id']}", json={"col": 1}, headers=STAFF).json()
    assert _cell(grid, 0, 1)["id"] == left["id"] and _cell(grid, 0, 1)["label"] == "L"
    assert _cell(grid, 0, 0)["id"] == right["id"]
    # Empty label clears it.
    grid = api.patch(f"/stores/badges/cells/{left['id']}", json={"label": ""}, headers=STAFF).json()
    assert _cell(grid, 0, 1)["label"] is None


def test_cell_404s(api):
    assert api.patch("/stores/badges/cells/9", json={}, headers=STAFF).status_code == 404
    assert api.delete("/stores/badges/cells/9", headers=STAFF).status_code == 404
    assert api.post("/stores/badges/cells/9/items", json={"name": "x"}, headers=STAFF).status_code == 404


def test_items_add_edit_move_delete(api):
    grid = _grid(api, 1, 2)
    a, b = _cell(grid, 0, 0), _cell(grid, 0, 1)
    item = api.post(f"/stores/badges/cells/{a['id']}/items", json={"name": "  Radio – Blue ", "quantity": 0},
                    headers=STAFF)
    assert item.status_code == 201
    item = item.json()
    # Trimmed, and a grid entry always holds at least one.
    assert item["name"] == "Radio – Blue" and item["quantity"] == 1

    moved = api.patch(f"/stores/badges/items/{item['id']}", json={"cellId": b["id"], "quantity": 7, "name": "X"},
                      headers=STAFF).json()
    assert moved == {"id": item["id"], "name": "X", "quantity": 7, "cellId": b["id"]}

    assert api.patch(f"/stores/badges/items/{item['id']}", json={"name": "  "}, headers=STAFF).status_code == 400
    assert api.patch(f"/stores/badges/items/{item['id']}", json={"cellId": 999}, headers=STAFF).status_code == 404
    assert api.delete(f"/stores/badges/items/{item['id']}", headers=STAFF).status_code == 204
    assert api.delete(f"/stores/badges/items/{item['id']}", headers=STAFF).status_code == 404
    assert api.patch(f"/stores/badges/items/{item['id']}", json={}, headers=STAFF).status_code == 404


def test_add_item_requires_a_name(api):
    cell = _cell(_grid(api, 1, 1), 0, 0)
    assert api.post(f"/stores/badges/cells/{cell['id']}/items", json={"name": None}, headers=STAFF).status_code == 400


def test_deleting_a_cell_takes_its_items(api, db):
    cell = _cell(_grid(api, 1, 1), 0, 0)
    api.post(f"/stores/badges/cells/{cell['id']}/items", json={"name": "x"}, headers=STAFF)
    assert api.delete(f"/stores/badges/cells/{cell['id']}", headers=STAFF).status_code == 204
    assert db.query(BadgeItem).count() == 0


def test_delete_row_and_column_shift_the_rest_up(api):
    grid = _grid(api, 3, 3)
    marker = _cell(grid, 2, 2)
    grid = api.delete("/stores/badges/rows/1", headers=STAFF).json()
    assert grid["config"]["numRows"] == 2
    assert _cell(grid, 1, 2)["id"] == marker["id"]
    grid = api.delete("/stores/badges/cols/0", headers=STAFF).json()
    assert grid["config"] == {"numRows": 2, "numCols": 2}
    assert _cell(grid, 1, 1)["id"] == marker["id"]
    assert len(grid["cells"]) == 4


def test_cannot_delete_the_last_row_or_column(api):
    _grid(api, 1, 1)
    assert api.delete("/stores/badges/rows/0", headers=STAFF).status_code == 400
    assert api.delete("/stores/badges/cols/0", headers=STAFF).status_code == 400


@pytest.mark.parametrize("kind", ["rows", "cols"])
@pytest.mark.parametrize("index", [-1, 3, 99])
def test_deleting_a_row_or_column_outside_the_grid_is_404_and_changes_nothing(api, kind, index):
    _grid(api, 3, 3)
    assert api.delete(f"/stores/badges/{kind}/{index}", headers=STAFF).status_code == 404
    assert api.get("/stores/badges", headers=STAFF).json()["config"] == {"numRows": 3, "numCols": 3}


# ── orders ────────────────────────────────────────────────────────────────────

def test_create_order_with_gained_details_and_qual_check(api, db):
    _cadet(db)
    db.add(CadetQualification(cadet_id=1, qual_type="Leadership Blue", status="true"))
    db.commit()
    order = _order(api, items=[
        {"badgeName": "Leadership – Blue", "gainedWhere": "camp",
         "gainedDateFrom": "2026-07-01T00:00:00", "gainedDateTo": "2026-07-08T00:00:00"},
        {"badgeName": "", "replacement": True},
        {"badgeName": "Core – Squadron", "replacement": True, "gainedWhere": "other", "gainedWhereDetail": "Wing"},
    ])
    assert order["cadetName"] == "Amy Adams" and order["completed"] is False
    first, core = order["items"]
    assert first["gainedWhere"] == "camp"
    assert first["gainedDateFrom"] == "2026-07-01T00:00:00"
    assert core["replacement"] is True and core["gainedWhereDetail"] == "Wing"
    # Qualification-backed badges are checked, everything else is "unknown".
    assert core["qualHeld"] is None
    assert first["qualHeld"] in (True, False)


@pytest.mark.parametrize("body,code", [
    ({}, 400),
    ({"cadetCin": 1, "items": {}}, 400),
    ({"cadetCin": 5, "items": []}, 404),
    ({"cadetCin": 1, "items": [{"badgeName": "x", "gainedDateFrom": "garbage"}]}, 400),
])
def test_create_order_validation(api, db, body, code):
    _cadet(db)
    assert api.post("/stores/badges/orders", json=body, headers=STAFF).status_code == code


def test_update_order_items(api, db):
    _cadet(db)
    order = _order(api, items=[{"badgeName": "A"}, {"badgeName": "B"}])
    a, b = order["items"]
    res = api.patch(f"/stores/badges/orders/{order['id']}", headers=STAFF, json={
        "completed": True,
        "items": [
            {"id": a["id"], "badgeName": "A2", "qmNotes": [{"content": "n"}], "givenAt": "2026-01-01T00:00:00",
             "givenBy": "QM", "gainedWhere": "on_sqn", "gainedWhereDetail": "d",
             "gainedDateFrom": None, "gainedDateTo": "2026-01-02T00:00:00"},
            {"badgeName": "C", "replacement": True},
        ],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["completed"] is True
    assert [i["badgeName"] for i in body["items"]] == ["A2", "C"]
    a2 = body["items"][0]
    assert (a2["givenBy"], a2["givenAt"], a2["gainedWhere"], a2["gainedDateTo"]) == (
        "QM", "2026-01-01T00:00:00", "on_sqn", "2026-01-02T00:00:00")
    assert a2["qmNotes"] == [{"content": "n"}]
    assert db.get(BadgeOrderItem, int(b["id"])) is None


def test_update_and_delete_missing_order(api):
    assert api.patch("/stores/badges/orders/1", json={}, headers=STAFF).status_code == 404
    assert api.delete("/stores/badges/orders/1", headers=STAFF).status_code == 404


def test_list_orders_newest_first_and_delete(api, db):
    _cadet(db)
    first, second = _order(api), _order(api)
    assert [o["id"] for o in api.get("/stores/badges/orders", headers=STAFF).json()] == [second["id"], first["id"]]
    assert api.delete(f"/stores/badges/orders/{first['id']}", headers=STAFF).status_code == 204
    assert len(api.get("/stores/badges/orders", headers=STAFF).json()) == 1


def test_mark_ready_emails_cadet(api, db, outbox):
    _cadet(db, rank="Cdt")
    order = _order(api)
    item = order["items"][0]["id"]
    res = api.post(f"/stores/badges/orders/{order['id']}/items/{item}/mark-ready", headers=STAFF)
    assert res.status_code == 200 and res.json()["items"][0]["readyToCollect"]
    assert outbox[0]["to"] == "c1@x" and "Cdt Adams" in outbox[0]["html"]


def test_mark_ready_no_email_and_404s(api, db, outbox):
    _cadet(db, email=None)
    order = _order(api)
    item = order["items"][0]["id"]
    assert api.post(f"/stores/badges/orders/{order['id']}/items/{item}/mark-ready", headers=STAFF).status_code == 200
    assert outbox == []
    assert api.post(f"/stores/badges/orders/999/items/{item}/mark-ready", headers=STAFF).status_code == 404
    assert api.post(f"/stores/badges/orders/{order['id']}/items/999/mark-ready", headers=STAFF).status_code == 404


# ── take from / return to the grid ────────────────────────────────────────────

def _stock(api, order, body):
    item = order["items"][0]["id"]
    return api.post(f"/stores/badges/orders/{order['id']}/items/{item}/stock", json=body, headers=STAFF)


def _grid_item(api, qty=2, name="Leadership – Blue"):
    cell = _cell(_grid(api, 1, 1), 0, 0)
    item = api.post(f"/stores/badges/cells/{cell['id']}/items", json={"name": name, "quantity": qty},
                    headers=STAFF).json()
    return cell, item


def test_remove_and_return_adjust_the_grid(api, db):
    _cadet(db)
    cell, item = _grid_item(api, qty=2)
    order = _order(api)

    res = _stock(api, order, {"action": "remove", "itemId": item["id"]}).json()
    assert res["badges"]["cells"][0]["items"][0]["quantity"] == 1
    assert [e["action"] for e in res["order"]["items"][0]["stockEvents"]] == ["removed"]
    assert _stock(api, order, {"action": "remove", "itemId": item["id"]}).status_code == 400

    res = _stock(api, order, {"action": "return"}).json()
    assert res["badges"]["cells"][0]["items"][0]["quantity"] == 2
    assert _stock(api, order, {"action": "return"}).status_code == 400


def test_taking_the_last_one_removes_the_grid_entry_and_return_recreates_it(api, db):
    _cadet(db)
    cell, item = _grid_item(api, qty=1)
    order = _order(api)
    res = _stock(api, order, {"action": "remove", "itemId": item["id"]}).json()
    assert res["badges"]["cells"][0]["items"] == []
    res = _stock(api, order, {"action": "return", "by": "QM"}).json()
    items = res["badges"]["cells"][0]["items"]
    assert [(i["name"], i["quantity"]) for i in items] == [("Leadership – Blue", 1)]
    assert res["order"]["items"][0]["stockEvents"][-1]["by"] == "QM"


def test_return_matches_by_name_when_the_original_row_was_replaced(api, db):
    _cadet(db)
    cell, item = _grid_item(api, qty=1)
    order = _order(api)
    _stock(api, order, {"action": "remove", "itemId": item["id"]})
    # Someone re-added the same badge to the cell in the meantime.
    db.add(BadgeItem(cell_id=cell["id"], name="Leadership – Blue", quantity=4))
    db.commit()
    res = _stock(api, order, {"action": "return"}).json()
    assert [i["quantity"] for i in res["badges"]["cells"][0]["items"]] == [5]


def test_return_when_the_cell_is_gone_is_a_clear_400(api, db):
    _cadet(db)
    cell, item = _grid_item(api, qty=1)
    order = _order(api)
    _stock(api, order, {"action": "remove", "itemId": item["id"]})
    db.query(BadgeGridCell).delete()
    db.commit()
    res = _stock(api, order, {"action": "return"})
    assert res.status_code == 400 and "no longer exists" in res.json()["detail"]


def test_stock_errors(api, db):
    _cadet(db)
    cell, item = _grid_item(api, qty=1)
    order = _order(api)
    db.get(BadgeItem, item["id"]).quantity = 0
    db.commit()
    assert _stock(api, order, {"action": "nope"}).status_code == 400
    assert _stock(api, order, {"action": "remove"}).status_code == 404
    assert _stock(api, order, {"action": "remove", "itemId": item["id"]}).json()["detail"] == "Out of stock"
    assert api.post("/stores/badges/orders/99/items/1/stock", json={"action": "remove"}, headers=STAFF).status_code == 404
    assert api.post(f"/stores/badges/orders/{order['id']}/items/99/stock", json={"action": "remove"},
                    headers=STAFF).status_code == 404


# ── supplier order list ───────────────────────────────────────────────────────

def test_order_list_lifecycle_and_audit_trail(api, db):
    _cadet(db)
    order = _order(api)
    item = order["items"][0]["id"]

    entry = api.post("/stores/badges/order-lists/entries", json={"orderItemId": item}, headers=STAFF)
    assert entry.status_code == 201
    entry = entry.json()
    assert (entry["badgeName"], entry["cadetName"], entry["addedBy"]) == (
        "Leadership – Blue", "Amy Adams", "staff@317atc.co.uk")
    assert api.post("/stores/badges/order-lists/entries", json={"orderItemId": item}, headers=STAFF).status_code == 409

    eid = entry["id"]
    # Can't receive before ordering.
    assert api.post(f"/stores/badges/order-lists/entries/{eid}/mark-received", json={}, headers=STAFF).status_code == 400
    ordered = api.post(f"/stores/badges/order-lists/entries/{eid}/mark-ordered", json={"by": "QM"}, headers=STAFF).json()
    assert ordered["orderedBy"] == "QM" and ordered["orderedAt"]
    assert api.post(f"/stores/badges/order-lists/entries/{eid}/mark-ordered", json={}, headers=STAFF).status_code == 400
    # Ordered entries are locked in.
    assert api.delete(f"/stores/badges/order-lists/entries/{eid}", headers=STAFF).status_code == 400

    received = api.post(f"/stores/badges/order-lists/entries/{eid}/mark-received", json={}, headers=STAFF).json()
    assert received["receivedBy"] == "staff@317atc.co.uk"
    assert api.post(f"/stores/badges/order-lists/entries/{eid}/mark-received", json={}, headers=STAFF).status_code == 400

    # Deleting the order leaves the audit entry with a null link.
    api.delete(f"/stores/badges/orders/{order['id']}", headers=STAFF)
    listed = api.get("/stores/badges/order-lists", headers=STAFF).json()
    assert len(listed) == 1 and listed[0]["badgeName"] == "Leadership – Blue"


def test_order_list_queued_entry_can_be_removed(api, db):
    _cadet(db)
    order = _order(api)
    entry = api.post("/stores/badges/order-lists/entries", json={"orderItemId": order["items"][0]["id"]},
                     headers=STAFF).json()
    assert api.delete(f"/stores/badges/order-lists/entries/{entry['id']}", headers=STAFF).status_code == 204
    assert api.get("/stores/badges/order-lists", headers=STAFF).json() == []


def test_order_list_errors(api):
    assert api.post("/stores/badges/order-lists/entries", json={}, headers=STAFF).status_code == 400
    assert api.post("/stores/badges/order-lists/entries", json={"orderItemId": 9}, headers=STAFF).status_code == 404
    assert api.delete("/stores/badges/order-lists/entries/9", headers=STAFF).status_code == 404
    assert api.post("/stores/badges/order-lists/entries/9/mark-ordered", json={}, headers=STAFF).status_code == 404
    assert api.post("/stores/badges/order-lists/entries/9/mark-received", json={}, headers=STAFF).status_code == 404
