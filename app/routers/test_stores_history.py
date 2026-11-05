"""Stores history: the stock change log, recently deleted records (and putting
them back), and the badges given-out history.

Driven through HTTP, uniform and badge side by side, with the refusals and
edge cases alongside each happy path.
"""

import json
from datetime import datetime

import pytest

from database.models import (
    BadgeOrderItem,
    Cadet,
    LogsForm,
    LogsFormEntry,
    StoresBox,
    StoresDeletedRecord,
    StoresItem,
    StoresOrderItem,
    StoresSection,
    StoresStockChange,
)

STAFF = {"Authorization": "Bearer staff"}

ENDPOINTS = [
    ("get", "/stores/stock/changes"),
    ("get", "/stores/deleted"),
    ("post", "/stores/deleted/1/restore"),
    ("get", "/stores/badges/history"),
]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_history_needs_a_token(api, method, path):
    assert getattr(api, method)(path).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_history_is_staff_only(api, persona, method, path):
    assert getattr(api, method)(path, headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_role_can_read_the_history(api, persona):
    for method, path in ENDPOINTS:
        if method == "get":
            assert api.get(path, headers=api.as_(persona)).status_code == 200


# ── helpers ───────────────────────────────────────────────────────────────────

def _box(db, label="A", sections=("1", "2")):
    box = StoresBox(label=label, shelf_level=1, shelf_position=0)
    db.add(box)
    db.flush()
    for i, s in enumerate(sections):
        db.add(StoresSection(box_id=box.id, label=s, position=i))
    db.commit()
    return box


def _cadet(db, cin=1001, first="Amy", last="Adams"):
    db.add(Cadet(cin=cin, first_name=first, last_name=last, email=f"c{cin}@317atc.co.uk", flight="A"))
    db.commit()


def _add_stock(api, item="Beret", size="55", box="A", section="1", qty=3):
    res = api.post("/stores/stock", json={"itemType": item, "size": size, "box": box, "section": section,
                                          "quantity": qty}, headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


def _changes(api, kind=None):
    res = api.get("/stores/stock/changes" + (f"?kind={kind}" if kind else ""), headers=STAFF)
    assert res.status_code == 200, res.text
    # Oldest first reads more naturally in a test than the API's newest first.
    return list(reversed(res.json()))


def _summary(changes):
    return [(c["action"], c["itemName"], c["change"], c["quantityAfter"]) for c in changes]


def _uniform_order(api, cin=1001, items=None):
    items = items or [{"itemType": "Beret", "size": "55"}, {"itemType": "Tie"}]
    res = api.post("/stores/orders", json={"cadetCin": cin, "items": items}, headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


def _badge_order(api, cin=1001, items=None):
    items = items or [{"badgeName": "Leadership – Blue"}]
    res = api.post("/stores/badges/orders", json={"cadetCin": cin, "items": items}, headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


def _badge_cell(api, rows=1, cols=1):
    grid = api.patch("/stores/badges/config", json={"numRows": rows, "numCols": cols}, headers=STAFF).json()
    return sorted(grid["cells"], key=lambda c: (c["row"], c["col"]))


def _badge_stock(api, cell, name="Leadership – Blue", qty=2):
    res = api.post(f"/stores/badges/cells/{cell['id']}/items", json={"name": name, "quantity": qty}, headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


def _deleted(api):
    res = api.get("/stores/deleted", headers=STAFF)
    assert res.status_code == 200, res.text
    return res.json()


def _restore(api, record_id):
    return api.post(f"/stores/deleted/{record_id}/restore", headers=STAFF)


# ── uniform stock changes ─────────────────────────────────────────────────────

def test_adding_and_topping_up_stock_is_logged_with_who_and_where(api, db):
    _box(db)
    _add_stock(api, qty=3)
    _add_stock(api, qty=2)  # same place: tops up
    changes = _changes(api)
    assert _summary(changes) == [("added", "Beret", 3, 3), ("topped_up", "Beret", 2, 5)]
    assert changes[0]["size"] == "55" and changes[0]["location"] == "A / 1"
    assert changes[0]["changedBy"] == "Sam Staff" and changes[0]["kind"] == "uniform"


def test_editing_a_count_logs_the_difference_and_what_it_was(api, db):
    _box(db)
    item = _add_stock(api, qty=3)
    api.patch(f"/stores/stock/{item['id']}", json={"quantity": 1}, headers=STAFF)
    last = _changes(api)[-1]
    assert (last["action"], last["change"], last["quantityAfter"], last["note"]) == ("counted", -2, 1, "Was 3")


def test_moving_and_renaming_a_line_are_logged_separately(api, db):
    _box(db)
    item = _add_stock(api)
    api.patch(f"/stores/stock/{item['id']}", json={"size": "56", "section": "2"}, headers=STAFF)
    renamed, moved = _changes(api)[-2:]
    assert (renamed["action"], renamed["note"]) == ("renamed", "Was Beret 55")
    assert (moved["action"], moved["note"], moved["location"]) == ("moved", "From A / 1", "A / 2")
    assert renamed["change"] == moved["change"] == 0


def test_an_edit_that_changes_nothing_logs_nothing(api, db):
    _box(db)
    item = _add_stock(api)
    api.patch(f"/stores/stock/{item['id']}", json={"quantity": 3}, headers=STAFF)
    assert len(_changes(api)) == 1


def test_an_edit_that_merges_two_lines_logs_the_merge(api, db):
    _box(db)
    _add_stock(api, size="55", qty=2)
    other = _add_stock(api, size="56", qty=4)
    api.patch(f"/stores/stock/{other['id']}", json={"size": "55"}, headers=STAFF)
    merged = _changes(api)[-1]
    assert (merged["action"], merged["change"], merged["quantityAfter"]) == ("merged", 4, 6)


def test_deleting_a_line_logs_what_it_held(api, db):
    _box(db)
    item = _add_stock(api, qty=3)
    assert api.delete(f"/stores/stock/{item['id']}", headers=STAFF).status_code == 204
    assert _summary(_changes(api))[-1] == ("deleted", "Beret", -3, None)


def test_deleting_a_box_or_section_logs_every_line_on_it(api, db):
    _box(db)
    _add_stock(api, item="Beret", section="1")
    _add_stock(api, item="Jumper", size="M", section="2", qty=1)
    api.post("/stores/structure", json={"action": "delete-section", "box": "A", "section": "2"}, headers=STAFF)
    jumper = _changes(api)[-1]
    assert (jumper["action"], jumper["itemName"], jumper["note"]) == ("deleted", "Jumper", "Section A / 2 deleted")
    api.post("/stores/structure", json={"action": "delete-box", "box": "A"}, headers=STAFF)
    beret = _changes(api)[-1]
    assert (beret["action"], beret["itemName"], beret["change"], beret["note"]) == \
        ("deleted", "Beret", -3, "Box A deleted")


def test_taking_stock_for_an_order_and_putting_it_back_is_logged_against_the_order(api, db):
    _box(db)
    _cadet(db, first="Zoë", last="Ó Briain")
    stock = _add_stock(api, qty=2)
    order = _uniform_order(api)
    item = order["items"][0]["id"]
    path = f"/stores/orders/{order['id']}/items/{item}/stock"
    assert api.post(path, json={"action": "remove", "stockItemId": stock["id"]}, headers=STAFF).status_code == 200
    assert api.post(path, json={"action": "return"}, headers=STAFF).status_code == 200
    taken, returned = _changes(api)[-2:]
    assert (taken["action"], taken["change"], taken["quantityAfter"], taken["orderId"]) == \
        ("taken", -1, 1, int(order["id"]))
    assert taken["note"] == "For Zoë Ó Briain"
    assert (returned["action"], returned["change"], returned["quantityAfter"]) == ("returned", 1, 2)


def test_a_refused_stock_change_logs_nothing(api, db):
    _box(db)
    _cadet(db)
    stock = _add_stock(api, qty=0)
    order = _uniform_order(api)
    path = f"/stores/orders/{order['id']}/items/{order['items'][0]['id']}/stock"
    assert api.post(path, json={"action": "remove", "stockItemId": stock["id"]}, headers=STAFF).status_code == 400
    assert api.patch("/stores/stock/999", json={"quantity": 1}, headers=STAFF).status_code == 404
    assert api.delete("/stores/stock/999", headers=STAFF).status_code == 404
    assert _summary(_changes(api)) == [("added", "Beret", 0, 0)]


# ── badge stock changes ───────────────────────────────────────────────────────

def test_badge_grid_changes_are_logged(api, db):
    [cell] = _badge_cell(api)
    api.patch(f"/stores/badges/cells/{cell['id']}", json={"label": "Top shelf"}, headers=STAFF)
    item = _badge_stock(api, cell, qty=2)
    api.patch(f"/stores/badges/items/{item['id']}", json={"quantity": 5, "name": "Leadership – Bronze"},
              headers=STAFF)
    api.delete(f"/stores/badges/items/{item['id']}", headers=STAFF)
    changes = _changes(api, "badge")
    assert _summary(changes) == [
        ("added", "Leadership – Blue", 2, 2),
        ("counted", "Leadership – Bronze", 3, 5),
        ("renamed", "Leadership – Bronze", 0, 5),
        ("deleted", "Leadership – Bronze", -5, None),
    ]
    assert changes[0]["location"] == "Top shelf" and changes[0]["kind"] == "badge"
    # The uniform list doesn't mix them in.
    assert _changes(api, "uniform") == []


def test_moving_a_badge_to_another_cell_is_logged(api, db):
    first, second = _badge_cell(api, cols=2)
    item = _badge_stock(api, first)
    api.patch(f"/stores/badges/items/{item['id']}", json={"cellId": second["id"]}, headers=STAFF)
    moved = _changes(api, "badge")[-1]
    assert (moved["action"], moved["note"], moved["location"]) == \
        ("moved", "From Row 1, column 1", "Row 1, column 2")


@pytest.mark.parametrize("what,path,note", [
    ("cell", "/stores/badges/cells/{cell}", "Its grid cell was deleted"),
    ("row", "/stores/badges/rows/1", "Row 2 was deleted"),
    ("col", "/stores/badges/cols/0", "Column 1 was deleted"),
])
def test_deleting_a_cell_row_or_column_logs_the_badges_it_held(api, db, what, path, note):
    cells = _badge_cell(api, rows=2, cols=2)
    target = next(c for c in cells if (c["row"], c["col"]) == (1, 0))
    _badge_stock(api, target, name="Radio – Bronze", qty=3)
    assert api.delete(path.format(cell=target["id"]), headers=STAFF).status_code in (200, 204)
    gone = _changes(api, "badge")[-1]
    assert (gone["action"], gone["change"], gone["quantityAfter"], gone["note"]) == ("deleted", -3, None, note)


def test_taking_the_last_badge_off_the_grid_and_putting_it_back_is_logged(api, db):
    _cadet(db)
    [cell] = _badge_cell(api)
    stock = _badge_stock(api, cell, qty=1)
    order = _badge_order(api)
    path = f"/stores/badges/orders/{order['id']}/items/{order['items'][0]['id']}/stock"
    assert api.post(path, json={"action": "remove", "itemId": stock["id"]}, headers=STAFF).status_code == 200
    assert api.post(path, json={"action": "return"}, headers=STAFF).status_code == 200
    taken, returned = _changes(api, "badge")[-2:]
    # Taking the last one removes the line, so nothing is left to count.
    assert (taken["action"], taken["change"], taken["quantityAfter"]) == ("taken", -1, None)
    assert (returned["action"], returned["change"], returned["quantityAfter"]) == ("returned", 1, 1)
    assert returned["note"] == "From Amy Adams's order"


@pytest.mark.parametrize("query", ["kind=shoes", "limit=0", "limit=1001", "limit=x"])
def test_stock_changes_reject_bad_filters(api, query):
    assert api.get(f"/stores/stock/changes?{query}", headers=STAFF).status_code == 422


def test_stock_changes_are_empty_to_start_and_honour_the_limit(api, db):
    assert api.get("/stores/stock/changes", headers=STAFF).json() == []
    _box(db)
    for qty in (1, 2, 3):
        _add_stock(api, size=str(50 + qty), qty=qty)
    [newest] = api.get("/stores/stock/changes?limit=1", headers=STAFF).json()
    assert newest["size"] == "53"


# ── deleted orders ────────────────────────────────────────────────────────────

def test_a_deleted_uniform_order_can_be_seen_and_restored_with_its_history(api, db):
    _cadet(db, first="Zoë", last="Ó Briain")
    order = _uniform_order(api)
    beret = db.get(StoresOrderItem, int(order["items"][0]["id"]))
    beret.given_at, beret.given_by = datetime(2026, 10, 1, 18), "QM"
    beret.qm_notes = json.dumps([{"id": "n1", "content": "Wants a 56 next time"}])
    db.commit()
    assert api.delete(f"/stores/orders/{order['id']}", headers=STAFF).status_code == 204

    [record] = _deleted(api)
    assert record["kind"] == "uniform_order"
    assert record["label"] == "Uniform order for Zoë Ó Briain: Beret, Tie"
    assert record["deletedBy"] == "Sam Staff" and record["restoredAt"] is None

    res = _restore(api, record["id"])
    assert res.status_code == 200, res.text
    assert res.json()["restoredBy"] == "Sam Staff"
    back = next(o for o in api.get("/stores/orders", headers=STAFF).json() if o["id"] == str(res.json()["restoredId"]))
    assert [i["itemType"] for i in back["items"]] == ["Beret", "Tie"]
    assert back["items"][0]["givenAt"] == "2026-10-01T18:00:00"
    assert back["items"][0]["qmNotes"][0]["content"] == "Wants a 56 next time"
    assert back["timestamp"] == order["timestamp"]

    # Once only.
    assert _restore(api, record["id"]).status_code == 409


def test_restoring_an_order_for_a_cadet_who_has_gone_is_refused(api, db):
    _cadet(db)
    order = _uniform_order(api)
    api.delete(f"/stores/orders/{order['id']}", headers=STAFF)
    db.delete(db.get(Cadet, 1001))
    db.commit()
    [record] = _deleted(api)
    res = _restore(api, record["id"])
    assert res.status_code == 409 and "roster" in res.json()["detail"]
    assert _deleted(api)[0]["restoredAt"] is None


def test_restoring_something_that_was_never_deleted_is_404(api):
    assert _restore(api, 999).status_code == 404


def test_a_deleted_badge_order_can_be_restored(api, db):
    _cadet(db)
    order = _badge_order(api, items=[{"badgeName": "Radio – Bronze", "replacement": True}])
    assert api.delete(f"/stores/badges/orders/{order['id']}", headers=STAFF).status_code == 204
    [record] = _deleted(api)
    assert record["label"] == "Badge order for Amy Adams: Radio – Bronze"
    assert _restore(api, record["id"]).status_code == 200
    [back] = api.get("/stores/badges/orders", headers=STAFF).json()
    assert back["items"][0]["badgeName"] == "Radio – Bronze" and back["items"][0]["replacement"] is True


def test_a_long_order_is_summarised_in_its_label(api, db):
    _cadet(db)
    order = _uniform_order(api, items=[{"itemType": t} for t in ("Beret", "Tie", "Belt", "Jumper", "Brassard")])
    api.delete(f"/stores/orders/{order['id']}", headers=STAFF)
    assert _deleted(api)[0]["label"].endswith("Beret, Tie, Belt and 2 more")


def test_a_refused_delete_archives_nothing(api):
    assert api.delete("/stores/orders/999", headers=STAFF).status_code == 404
    assert api.delete("/stores/badges/orders/999", headers=STAFF).status_code == 404
    assert _deleted(api) == []


def test_an_order_a_cadet_cancels_on_the_portal_is_kept(api, db):
    cadet = Cadet(cin=2001, first_name="Cara", last_name="Cadet", email="cadet@317atc.co.uk", flight="A")
    db.add(cadet)
    db.commit()
    created = api.post("/cadets/me/orders", json={"items": [{"itemType": "Tie"}]}, headers=api.as_("cadet"))
    assert created.status_code == 201, created.text
    assert api.delete(f"/cadets/me/orders/{created.json()['id']}", headers=api.as_("cadet")).status_code == 204
    [record] = _deleted(api)
    assert record["deletedBy"] == "Cara Cadet (cancelled on the portal)"
    assert _restore(api, record["id"]).status_code == 200


# ── deleted list entries ──────────────────────────────────────────────────────

def _logs_entry(api, db, item_type="Beret"):
    _cadet(db)
    order = _uniform_order(api, items=[{"itemType": item_type, "size": "55"}])
    item = order["items"][0]["id"]
    res = api.post("/stores/logs-forms/entries", json={"orderItemId": item}, headers=STAFF)
    assert res.status_code == 201, res.text
    return item, res.json()["entries"][0]["id"]


def test_a_removed_logs_form_entry_goes_back_on_the_form_it_came_off(api, db):
    item, entry = _logs_entry(api, db)
    assert api.delete(f"/stores/logs-forms/entries/{entry}", headers=STAFF).status_code == 204
    [record] = _deleted(api)
    assert record["kind"] == "logs_form_entry" and record["label"] == "Logs Form entry: Beret 55 for Amy Adams"
    assert _restore(api, record["id"]).status_code == 200
    restored = db.query(LogsFormEntry).one()
    assert restored.order_item_id == int(item) and restored.form.ordered_at is None
    assert db.query(LogsForm).count() == 1


def test_a_logs_form_entry_restored_after_its_form_was_sent_goes_on_the_open_one(api, db):
    _, entry = _logs_entry(api, db)
    form = db.query(LogsForm).one()
    api.delete(f"/stores/logs-forms/entries/{entry}", headers=STAFF)
    form.ordered_at = datetime(2026, 11, 1)
    db.commit()
    assert _restore(api, _deleted(api)[0]["id"]).status_code == 200
    db.expire_all()
    restored = db.query(LogsFormEntry).one()
    assert restored.form_id != form.id and restored.form.ordered_at is None


def test_restoring_a_logs_form_entry_whose_item_is_back_on_a_form_is_refused(api, db):
    item, entry = _logs_entry(api, db)
    api.delete(f"/stores/logs-forms/entries/{entry}", headers=STAFF)
    api.post("/stores/logs-forms/entries", json={"orderItemId": item}, headers=STAFF)
    res = _restore(api, _deleted(api)[0]["id"])
    assert res.status_code == 409 and "already back on the Logs Form" in res.json()["detail"]
    assert db.query(LogsFormEntry).count() == 1


def test_a_logs_form_entry_whose_order_has_gone_comes_back_unlinked(api, db):
    item, entry = _logs_entry(api, db)
    api.delete(f"/stores/logs-forms/entries/{entry}", headers=STAFF)
    order_id = db.get(StoresOrderItem, int(item)).order_id
    api.delete(f"/stores/orders/{order_id}", headers=STAFF)
    logs_record = next(r for r in _deleted(api) if r["kind"] == "logs_form_entry")
    assert _restore(api, logs_record["id"]).status_code == 200
    restored = db.query(LogsFormEntry).one()
    assert restored.order_item_id is None and restored.cadet_name == "Amy Adams"


def test_a_removed_badge_order_list_entry_can_be_restored(api, db):
    _cadet(db)
    order = _badge_order(api)
    item = order["items"][0]["id"]
    entry = api.post("/stores/badges/order-lists/entries", json={"orderItemId": item}, headers=STAFF).json()
    assert api.delete(f"/stores/badges/order-lists/entries/{entry['id']}", headers=STAFF).status_code == 204
    [record] = _deleted(api)
    assert record["label"] == "Badge order list entry: Leadership – Blue for Amy Adams"
    assert _restore(api, record["id"]).status_code == 200
    [back] = api.get("/stores/badges/order-lists", headers=STAFF).json()
    assert back["orderItemId"] == item and back["addedAt"] == entry["addedAt"]


def test_restoring_a_badge_list_entry_already_re_added_is_refused(api, db):
    _cadet(db)
    order = _badge_order(api)
    item = order["items"][0]["id"]
    entry = api.post("/stores/badges/order-lists/entries", json={"orderItemId": item}, headers=STAFF).json()
    api.delete(f"/stores/badges/order-lists/entries/{entry['id']}", headers=STAFF)
    api.post("/stores/badges/order-lists/entries", json={"orderItemId": item}, headers=STAFF)
    assert _restore(api, _deleted(api)[0]["id"]).status_code == 409


def test_an_entry_that_cant_be_removed_isnt_archived(api, db):
    _cadet(db)
    order = _badge_order(api)
    entry = api.post("/stores/badges/order-lists/entries", json={"orderItemId": order["items"][0]["id"]},
                     headers=STAFF).json()
    api.post(f"/stores/badges/order-lists/entries/{entry['id']}/mark-ordered", json={}, headers=STAFF)
    assert api.delete(f"/stores/badges/order-lists/entries/{entry['id']}", headers=STAFF).status_code == 400
    assert _deleted(api) == []


def test_an_unknown_kind_cant_be_restored(api, db):
    db.add(StoresDeletedRecord(kind="mystery", label="?", data="{}", deleted_at=datetime(2026, 1, 1)))
    db.commit()
    assert _restore(api, _deleted(api)[0]["id"]).status_code == 400


@pytest.mark.parametrize("limit", [0, 1001, "x"])
def test_deleted_rejects_a_bad_limit(api, limit):
    assert api.get(f"/stores/deleted?limit={limit}", headers=STAFF).status_code == 422


# ── badges given out ──────────────────────────────────────────────────────────

def _given(api, order, item, verb):
    res = api.post(f"/stores/badges/orders/{order['id']}/items/{item}/{verb}", json={}, headers=STAFF)
    assert res.status_code == 200, res.text


def test_badge_history_lists_gives_and_undos_newest_first(api, db):
    _cadet(db, first="Zoë", last="Ó Briain")
    order = _badge_order(api)
    item = order["items"][0]["id"]
    _given(api, order, item, "mark-given")
    _given(api, order, item, "undo-given")
    _given(api, order, item, "mark-given")
    rows = api.get("/stores/badges/history", headers=STAFF).json()
    assert [r["action"] for r in rows] == ["given", "undone", "given"]
    assert [r["current"] for r in rows] == [True, False, False]
    assert rows[0]["cadetName"] == "Zoë Ó Briain" and rows[0]["badgeName"] == "Leadership – Blue"
    assert rows[0]["by"] == "Sam Staff" and rows[0]["cadetCin"] == 1001
    assert len({r["id"] for r in rows}) == 3


def test_a_badge_given_before_the_log_existed_still_shows(api, db):
    _cadet(db)
    order = _badge_order(api)
    row = db.get(BadgeOrderItem, int(order["items"][0]["id"]))
    row.given_at, row.given_by = datetime(2025, 6, 1), "Old QM"
    db.commit()
    [only] = api.get("/stores/badges/history", headers=STAFF).json()
    assert (only["action"], only["by"], only["timestamp"], only["current"]) == \
        ("given", "Old QM", "2025-06-01T00:00:00", True)


def test_badge_history_is_empty_until_something_is_given(api, db):
    _cadet(db)
    _badge_order(api)
    assert api.get("/stores/badges/history", headers=STAFF).json() == []


def test_badge_history_honours_the_limit(api, db):
    _cadet(db)
    order = _badge_order(api, items=[{"badgeName": "ATC"}, {"badgeName": "First Class"}])
    for item in order["items"]:
        _given(api, order, item["id"], "mark-given")
    assert len(api.get("/stores/badges/history?limit=1", headers=STAFF).json()) == 1
    assert api.get("/stores/badges/history?limit=0", headers=STAFF).status_code == 422


def test_stock_log_rows_have_no_foreign_keys_so_they_outlive_the_line(api, db):
    # The log is history: deleting the line it describes must not take it too.
    _box(db)
    item = _add_stock(api)
    api.delete(f"/stores/stock/{item['id']}", headers=STAFF)
    assert db.query(StoresItem).count() == 0
    assert db.query(StoresStockChange).count() == 2
