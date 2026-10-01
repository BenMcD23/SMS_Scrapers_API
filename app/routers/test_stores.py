"""Stores: shelf structure, stock, uniform orders, issuances and logs forms.

Every endpoint is staff-only, so the gate is checked once for the whole surface,
then each flow is driven through HTTP with its unhappy paths alongside.
"""

import io
import json
from datetime import datetime

import openpyxl
import pytest

import routers.stores as stores
from core import stock_events
from database.models import (
    Cadet,
    LogsForm,
    StoresBox,
    StoresItem,
    StoresItemIssuance,
    StoresOrder,
    StoresOrderItem,
    StoresSection,
    User,
)

STAFF = {"Authorization": "Bearer staff"}

# (method, path, json) for every staff-only stores endpoint.
STAFF_ONLY = [
    ("get", "/stores/structure", None),
    ("post", "/stores/structure", {"action": "add-box", "box": "A"}),
    ("patch", "/stores/boxes/A/layout", {}),
    ("patch", "/stores/boxes/A/sections/reorder", {"sections": []}),
    ("get", "/stores/stock", None),
    ("post", "/stores/stock", {}),
    ("patch", "/stores/stock/1", {}),
    ("delete", "/stores/stock/1", None),
    ("get", "/stores/orders", None),
    ("post", "/stores/orders", {}),
    ("patch", "/stores/orders/1", {}),
    ("post", "/stores/orders/kit-flight", None),
    ("delete", "/stores/orders/1", None),
    ("post", "/stores/orders/1/items/1/mark-ready", None),
    ("post", "/stores/orders/1/items/1/stock", {"action": "remove"}),
    ("get", "/stores/issuances/1", None),
    ("post", "/stores/issuances/1", {}),
    ("get", "/stores/issuances/user/1", None),
    ("post", "/stores/issuances/user/1", {}),
    ("delete", "/stores/issuances/1", None),
    ("get", "/stores/logs-forms", None),
    ("post", "/stores/logs-forms/entries", {}),
    ("delete", "/stores/logs-forms/entries/1", None),
    ("post", "/stores/logs-forms/1/mark-ordered", None),
    ("get", "/stores/logs-forms/1/download", None),
]


def _call(api, method, path, body, headers=None):
    kwargs = {"headers": headers or {}}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)(path, **kwargs) if method != "delete" else api.delete(path, **kwargs)


@pytest.mark.parametrize("method,path,body", STAFF_ONLY)
def test_every_stores_endpoint_needs_a_token(api, method, path, body):
    assert _call(api, method, path, body).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method,path,body", STAFF_ONLY)
def test_every_stores_endpoint_is_staff_only(api, persona, method, path, body):
    assert _call(api, method, path, body, api.as_(persona)).status_code == 403


# ── helpers ───────────────────────────────────────────────────────────────────

def _structure(api, **body):
    return api.post("/stores/structure", json=body, headers=STAFF)


def _box(db, label="A", level=1, pos=0, sections=("1",)):
    box = StoresBox(label=label, shelf_level=level, shelf_position=pos)
    db.add(box)
    db.flush()
    for i, s in enumerate(sections):
        db.add(StoresSection(box_id=box.id, label=s, position=i))
    db.commit()
    return box


def _cadet(db, cin=1001, **kw):
    defaults = dict(first_name="Amy", last_name="Adams", email=f"c{cin}@317atc.co.uk", flight="A")
    db.add(Cadet(cin=cin, **{**defaults, **kw}))
    db.commit()
    return db.get(Cadet, cin)


def _stock(db, box, section_label="1", item_type="Beret", size="55", qty=3):
    section = next(s for s in box.sections if s.label == section_label)
    item = StoresItem(item_type=item_type, size=size, quantity=qty, gender="unisex",
                      box_id=box.id, section_id=section.id)
    db.add(item)
    db.commit()
    return item


def _order(api, cin=1001, items=None, **extra):
    items = items if items is not None else [{"itemType": "Beret", "size": "55"}]
    res = api.post("/stores/orders", json={"cadetCin": cin, "items": items, **extra}, headers=STAFF)
    assert res.status_code == 201, res.text
    return res.json()


# ── structure ─────────────────────────────────────────────────────────────────

def test_structure_starts_empty(api):
    assert api.get("/stores/structure", headers=STAFF).json() == {"boxes": []}


def test_add_box_upper_cases_and_appends_to_the_bottom_shelf(api):
    _structure(api, action="add-box", box=" a ")
    boxes = _structure(api, action="add-box", box="b").json()["boxes"]
    assert [(b["label"], b["shelfLevel"], b["shelfPosition"]) for b in boxes] == [("A", 1, 0), ("B", 1, 1)]
    assert boxes[0]["topEnd"] == "left" and boxes[0]["sections"] == []


@pytest.mark.parametrize("action", ["add-box", "add-area"])
def test_add_box_or_area_rejects_blank_and_duplicate_labels(api, action):
    assert _structure(api, action=action, box="  ").status_code == 400
    assert _structure(api, action=action, box="X").status_code == 200
    dup = _structure(api, action=action, box="x")
    assert dup.status_code == 400
    assert "already exists" in dup.json()["detail"]


def test_area_goes_on_level_zero_and_does_not_disturb_boxes(api):
    _structure(api, action="add-box", box="A")
    boxes = _structure(api, action="add-area", box="Floor").json()["boxes"]
    by_label = {b["label"]: b for b in boxes}
    assert by_label["FLOOR"]["shelfLevel"] == 0 and by_label["FLOOR"]["shelfPosition"] == 0
    assert by_label["A"]["shelfPosition"] == 0


def test_delete_box_compacts_the_remaining_positions(api):
    for label in "ABC":
        _structure(api, action="add-box", box=label)
    boxes = _structure(api, action="delete-box", box="a").json()["boxes"]
    assert [(b["label"], b["shelfPosition"]) for b in boxes] == [("B", 0), ("C", 1)]


def test_delete_missing_box_is_404(api):
    assert _structure(api, action="delete-box", box="NOPE").status_code == 404


def test_delete_box_takes_its_stock_with_it(api, db):
    box = _box(db)
    _stock(db, box)
    assert _structure(api, action="delete-box", box="A").status_code == 200
    assert api.get("/stores/stock", headers=STAFF).json() == []


def test_sections_add_rename_delete(api):
    _structure(api, action="add-box", box="A")
    _structure(api, action="add-section", box="A", section="Top")
    boxes = _structure(api, action="add-section", box="A", section="Bottom").json()["boxes"]
    assert [(s["label"], s["position"]) for s in boxes[0]["sections"]] == [("Top", 0), ("Bottom", 1)]

    assert _structure(api, action="add-section", box="A", section="Top").status_code == 400
    assert _structure(api, action="rename-section", box="A", section="Top", newLabel="Bottom").status_code == 400

    boxes = _structure(api, action="rename-section", box="A", section="Top", newLabel="Upper").json()["boxes"]
    assert [s["label"] for s in boxes[0]["sections"]] == ["Upper", "Bottom"]

    # Renaming to itself is a no-op, not a "duplicate" error.
    assert _structure(api, action="rename-section", box="A", section="Upper", newLabel="Upper").status_code == 200

    boxes = _structure(api, action="delete-section", box="A", section="Upper").json()["boxes"]
    assert [(s["label"], s["position"]) for s in boxes[0]["sections"]] == [("Bottom", 0)]


@pytest.mark.parametrize("body,code", [
    ({"action": "add-section", "box": "A"}, 400),
    ({"action": "add-section", "section": "S"}, 400),
    ({"action": "add-section", "box": "ZZ", "section": "S"}, 404),
    ({"action": "delete-section", "box": "A"}, 400),
    ({"action": "delete-section", "box": "ZZ", "section": "S"}, 404),
    ({"action": "delete-section", "box": "A", "section": "missing"}, 404),
    ({"action": "rename-section", "box": "A", "section": "1"}, 400),
    ({"action": "rename-section", "box": "ZZ", "section": "1", "newLabel": "2"}, 404),
    ({"action": "rename-section", "box": "A", "section": "missing", "newLabel": "2"}, 404),
    ({"action": "rename-box", "box": "A"}, 400),
    ({"action": "rename-box", "box": "ZZ", "newLabel": "Q"}, 404),
    ({"action": "explode"}, 400),
    ({}, 400),
])
def test_structure_bad_requests(api, db, body, code):
    _box(db)
    assert _structure(api, **body).status_code == code


def test_rename_box_upper_cases_and_refuses_a_taken_label(api, db):
    _box(db, "A")
    _box(db, "B", pos=1)
    assert _structure(api, action="rename-box", box="A", newLabel="b").status_code == 400
    boxes = _structure(api, action="rename-box", box="A", newLabel="z").json()["boxes"]
    assert sorted(b["label"] for b in boxes) == ["B", "Z"]
    # Same label (case-insensitively) is allowed.
    assert _structure(api, action="rename-box", box="Z", newLabel="z").status_code == 200


# ── layout ────────────────────────────────────────────────────────────────────

def test_layout_404_for_unknown_box(api):
    assert api.patch("/stores/boxes/NOPE/layout", json={}, headers=STAFF).status_code == 404


def test_layout_top_end_and_width(api, db):
    _box(db)
    res = api.patch("/stores/boxes/a/layout", json={"topEnd": "right", "boxWidth": 3}, headers=STAFF)
    box = res.json()["boxes"][0]
    assert box["topEnd"] == "right"
    # Clamped so a box can't be dragged to nothing.
    assert box["boxWidth"] == 10
    bad = api.patch("/stores/boxes/A/layout", json={"topEnd": "middle"}, headers=STAFF)
    assert bad.status_code == 400


@pytest.mark.parametrize("level", [0, 4, -1])
def test_layout_rejects_shelf_levels_outside_1_to_3(api, db, level):
    _box(db)
    res = api.patch("/stores/boxes/A/layout", json={"shelfLevel": level}, headers=STAFF)
    assert res.status_code == 400


def test_moving_a_box_between_shelves_reflows_both(api, db):
    for i, label in enumerate("ABC"):
        _box(db, label, level=1, pos=i, sections=())
    _box(db, "X", level=2, pos=0, sections=())
    _box(db, "Y", level=2, pos=1, sections=())

    res = api.patch("/stores/boxes/B/layout", json={"shelfLevel": 2, "shelfPosition": 1}, headers=STAFF)
    layout = {b["label"]: (b["shelfLevel"], b["shelfPosition"]) for b in res.json()["boxes"]}
    assert layout == {"A": (1, 0), "C": (1, 1), "X": (2, 0), "B": (2, 1), "Y": (2, 2)}


def test_moving_past_the_end_clamps_to_last(api, db):
    _box(db, "A", pos=0, sections=())
    _box(db, "B", pos=1, sections=())
    res = api.patch("/stores/boxes/A/layout", json={"shelfPosition": 99}, headers=STAFF)
    layout = {b["label"]: b["shelfPosition"] for b in res.json()["boxes"]}
    assert layout == {"B": 0, "A": 1}
    res = api.patch("/stores/boxes/A/layout", json={"shelfPosition": -5}, headers=STAFF)
    assert {b["label"]: b["shelfPosition"] for b in res.json()["boxes"]} == {"A": 0, "B": 1}


# ── section reorder ───────────────────────────────────────────────────────────

def test_reorder_sections(api, db):
    _box(db, sections=("1", "2"))
    body = {"sections": [
        {"label": "1", "row": 1, "position": 0, "sectionWidth": 5},
        {"label": "2", "row": 0, "position": 0, "sectionWidth": 60},
    ]}
    res = api.patch("/stores/boxes/a/sections/reorder", json=body, headers=STAFF)
    sections = res.json()["boxes"][0]["sections"]
    # Sorted by row then position; width clamped at 10.
    assert [(s["label"], s["row"], s["sectionWidth"]) for s in sections] == [("2", 0, 60), ("1", 1, 10)]


@pytest.mark.parametrize("labels", [[], ["1"], ["1", "2", "3"], ["1", "nope"]])
def test_reorder_must_name_exactly_the_existing_sections(api, db, labels):
    _box(db, sections=("1", "2"))
    body = {"sections": [{"label": label} for label in labels]}
    assert api.patch("/stores/boxes/A/sections/reorder", json=body, headers=STAFF).status_code == 400


def test_reorder_unknown_box_404(api):
    assert api.patch("/stores/boxes/Q/sections/reorder", json={"sections": []}, headers=STAFF).status_code == 404


# ── stock ─────────────────────────────────────────────────────────────────────

def test_create_stock_and_top_up(api, db):
    _box(db)
    body = {"itemType": "Trousers", "size": "80/76/92", "box": "a", "section": "1", "quantity": 2}
    first = api.post("/stores/stock", json=body, headers=STAFF)
    assert first.status_code == 201
    item = first.json()
    assert item["gender"] == "male" and item["box"] == "A" and item["quantity"] == 2

    again = api.post("/stores/stock", json={**body, "quantity": 3}, headers=STAFF).json()
    assert again["id"] == item["id"] and again["quantity"] == 5
    assert len(api.get("/stores/stock", headers=STAFF).json()) == 1


def test_unknown_item_type_defaults_to_unisex(api, db):
    _box(db)
    body = {"itemType": "Hat", "size": "M", "box": "A", "section": "1"}
    item = api.post("/stores/stock", json=body, headers=STAFF).json()
    assert item["gender"] == "unisex" and item["quantity"] == 0


@pytest.mark.parametrize("missing", ["itemType", "size", "box", "section"])
def test_create_stock_requires_every_field(api, db, missing):
    _box(db)
    body = {"itemType": "Beret", "size": "55", "box": "A", "section": "1"}
    body[missing] = "   "
    assert api.post("/stores/stock", json=body, headers=STAFF).status_code == 400


def test_create_stock_unknown_box_or_section(api, db):
    _box(db)
    base = {"itemType": "Beret", "size": "55", "box": "A", "section": "1"}
    assert api.post("/stores/stock", json={**base, "box": "Z"}, headers=STAFF).status_code == 404
    assert api.post("/stores/stock", json={**base, "section": "9"}, headers=STAFF).status_code == 404


def test_update_stock_fields(api, db):
    box = _box(db, sections=("1", "2"))
    item = _stock(db, box)
    res = api.patch(f"/stores/stock/{item.id}", json={"quantity": 9, "itemType": "Slacks", "size": "S", "section": "2"},
                    headers=STAFF).json()
    assert (res["quantity"], res["itemType"], res["gender"], res["size"], res["section"]) == (9, "Slacks", "female", "S", "2")


def test_moving_stock_to_another_box_resets_to_its_first_section(api, db):
    a = _box(db, "A")
    _box(db, "B", pos=1, sections=("X", "Y"))
    item = _stock(db, a)
    res = api.patch(f"/stores/stock/{item.id}", json={"box": "b"}, headers=STAFF).json()
    assert (res["box"], res["section"]) == ("B", "X")


def test_update_stock_into_an_existing_row_merges_them(api, db):
    box = _box(db, sections=("1", "2"))
    keep = _stock(db, box, "1", qty=2)
    move = _stock(db, box, "2", qty=5)
    res = api.patch(f"/stores/stock/{move.id}", json={"section": "1"}, headers=STAFF).json()
    assert res["id"] == str(keep.id) and res["quantity"] == 7
    assert len(api.get("/stores/stock", headers=STAFF).json()) == 1


def test_update_stock_errors(api, db):
    box = _box(db)
    item = _stock(db, box)
    assert api.patch("/stores/stock/999", json={}, headers=STAFF).status_code == 404
    assert api.patch(f"/stores/stock/{item.id}", json={"box": "nope"}, headers=STAFF).status_code == 404
    assert api.patch(f"/stores/stock/{item.id}", json={"section": "nope"}, headers=STAFF).status_code == 404


def test_delete_stock(api, db):
    item = _stock(db, _box(db))
    assert api.delete(f"/stores/stock/{item.id}", headers=STAFF).status_code == 204
    assert api.delete(f"/stores/stock/{item.id}", headers=STAFF).status_code == 404


# ── orders ────────────────────────────────────────────────────────────────────

def test_create_order_skips_blank_items(api, db):
    _cadet(db)
    order = _order(api, items=[{"itemType": "Beret", "size": "55"}, {"itemType": "  "}, {"size": "x"},
                               {"itemType": "Jumper", "needSizing": True, "sizingDetails": "chest 90"}])
    assert order["cadetName"] == "Amy Adams" and order["subjectType"] == "cadet"
    assert [i["itemType"] for i in order["items"]] == ["Beret", "Jumper"]
    jumper = order["items"][1]
    assert jumper["needSizing"] is True and jumper["sizingDetails"] == "chest 90"
    assert jumper["qmNotes"] == [] and jumper["stockEvents"] == [] and jumper["lastIssued"] is None
    assert order["completed"] is False and order["kitting"] is False


@pytest.mark.parametrize("body,code", [
    ({}, 400),
    ({"cadetCin": 1001}, 200),  # items default to []
    ({"cadetCin": 1001, "items": "Beret"}, 400),
    ({"items": []}, 400),
    ({"cadetCin": 9999, "items": []}, 404),
])
def test_create_order_validation(api, db, body, code):
    _cadet(db)
    res = api.post("/stores/orders", json=body, headers=STAFF)
    assert res.status_code == (201 if code == 200 else code)


def test_orders_list_newest_first_with_last_issued(api, db):
    _cadet(db)
    db.add(StoresItemIssuance(cadet_id=1001, item_category="Wedgewood Shirt",
                              last_given=datetime(2025, 1, 2), size_given="15"))
    db.commit()
    first = _order(api, items=[{"itemType": "Wedgewood Female", "size": "16"}])
    second = _order(api)
    orders = api.get("/stores/orders", headers=STAFF).json()
    assert [o["id"] for o in orders] == [second["id"], first["id"]]
    # The female shirt maps onto the same issuance category as the male one.
    assert orders[1]["items"][0]["lastIssued"] == {"date": "2025-01-02T00:00:00", "size": "15"}


def test_order_for_a_user_and_an_orphan_order_serialise(db):
    user = User(google_id="g", email="u@x", first_name=None, last_name=None)
    db.add(user)
    db.commit()
    by_user = StoresOrder(user_id=user.id, created_at=datetime(2026, 1, 1))
    orphan = StoresOrder(created_at=datetime(2026, 1, 1))
    db.add_all([by_user, orphan])
    db.commit()
    # A user with no name falls back to their email.
    assert stores.order_to_dict(by_user)["cadetName"] == "u@x"
    assert stores.order_to_dict(by_user)["subjectType"] == "user"
    assert stores.order_to_dict(orphan)["subjectType"] == "unknown"
    assert stores.order_to_dict(orphan)["cadetName"] == "Unknown"


def test_update_order_edits_adds_and_removes_items(api, db):
    _cadet(db)
    order = _order(api, items=[{"itemType": "Beret", "size": "55"}, {"itemType": "Tie"}])
    beret, tie = order["items"]
    notes = [{"id": "n1", "content": "chased", "timestamp": "t", "addedBy": "QM"}]
    res = api.patch(f"/stores/orders/{order['id']}", headers=STAFF, json={
        "completed": True,
        "items": [
            {"id": beret["id"], "size": "56", "qmNotes": notes},
            {"itemType": "Belt"},
        ],
    }).json()
    assert res["completed"] is True
    assert [(i["itemType"], i["size"]) for i in res["items"]] == [("Beret", "56"), ("Belt", "")]
    assert res["items"][0]["qmNotes"] == notes
    assert db.get(StoresOrderItem, int(tie["id"])) is None


def test_update_order_with_unknown_item_id_adds_a_new_item(api, db):
    _cadet(db)
    order = _order(api)
    res = api.patch(f"/stores/orders/{order['id']}", headers=STAFF,
                    json={"items": [{"id": "99999", "itemType": "Belt"}]}).json()
    assert [i["itemType"] for i in res["items"]] == ["Belt"]


def test_update_missing_order_404(api):
    assert api.patch("/stores/orders/1", json={"completed": True}, headers=STAFF).status_code == 404


def test_delete_order_cascades_items(api, db):
    _cadet(db)
    order = _order(api)
    assert api.delete(f"/stores/orders/{order['id']}", headers=STAFF).status_code == 204
    assert db.query(StoresOrderItem).count() == 0
    assert api.delete(f"/stores/orders/{order['id']}", headers=STAFF).status_code == 404


def test_kit_flight_creates_one_kitting_order_per_eligible_c_flight_cadet(api, db):
    _cadet(db, 1, flight="C")
    _cadet(db, 2, flight="C", banned=True)
    _cadet(db, 3, flight="A")
    _cadet(db, 4, flight="C")
    _order(api, cin=4, kitting=True)

    created = api.post("/stores/orders/kit-flight", headers=STAFF)
    assert created.status_code == 201
    orders = created.json()
    assert [o["cadetCin"] for o in orders] == [1]
    assert orders[0]["kitting"] is True
    assert [i["itemType"] for i in orders[0]["items"]] == list(stores.KIT_FLIGHT_ITEMS)

    # Running it again is idempotent.
    assert api.post("/stores/orders/kit-flight", headers=STAFF).json() == []


# ── form import ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("configured,sent", [(None, "k"), ("k", None), ("k", "wrong"), ("", "")])
def test_form_import_rejects_bad_keys(api, monkeypatch, configured, sent):
    monkeypatch.setattr(stores, "UNIFORM_FORM_API_KEY", configured)
    headers = {"X-Import-Key": sent} if sent is not None else {}
    assert api.post("/stores/orders/form-import", json={"rows": []}, headers=headers).status_code == 401


def test_form_import_results_per_row(api, db, monkeypatch):
    monkeypatch.setattr(stores, "UNIFORM_FORM_API_KEY", "secret")
    _cadet(db, 1, email="Amy@317ATC.co.uk")
    rows = [
        {"email": " AMY@317atc.co.uk ", "items": [{"itemType": "Beret", "size": "55"}],
         "timestamp": "2026-02-03T10:00:00"},
        {"email": "amy@317atc.co.uk", "items": [{"itemType": "Tie"}], "timestamp": "not a date"},
        {"email": "", "items": [{"itemType": "Tie"}]},
        {"email": None},
        {"email": "ghost@317atc.co.uk", "items": [{"itemType": "Tie"}]},
        {"email": "amy@317atc.co.uk", "items": []},
    ]
    res = api.post("/stores/orders/form-import", json={"rows": rows}, headers={"X-Import-Key": "secret"})
    assert res.status_code == 201
    statuses = [(r["status"], r.get("detail")) for r in res.json()["results"]]
    assert statuses == [
        ("created", None), ("created", None),
        ("error", "Missing email"), ("error", "Missing email"),
        ("error", "Cadet not found"), ("skipped", "No items"),
    ]
    orders = db.query(StoresOrder).order_by(StoresOrder.id).all()
    assert orders[0].created_at == datetime(2026, 2, 3, 10, 0)
    # An unparseable timestamp falls back to now rather than failing the row.
    assert orders[1].created_at.year >= 2026


def test_form_import_rows_must_be_a_list(api, monkeypatch):
    monkeypatch.setattr(stores, "UNIFORM_FORM_API_KEY", "secret")
    res = api.post("/stores/orders/form-import", json={"rows": "x"}, headers={"X-Import-Key": "secret"})
    assert res.status_code == 400


# ── ready to collect ──────────────────────────────────────────────────────────

def test_mark_ready_stamps_and_emails_the_cadet(api, db, outbox):
    _cadet(db, rank="Cpl")
    order = _order(api)
    item_id = order["items"][0]["id"]
    res = api.post(f"/stores/orders/{order['id']}/items/{item_id}/mark-ready", headers=STAFF)
    assert res.status_code == 200
    assert res.json()["items"][0]["readyToCollect"] is not None
    assert len(outbox) == 1
    assert outbox[0]["to"] == "c1001@317atc.co.uk"
    assert "Cpl Adams" in outbox[0]["html"] and "Beret" in outbox[0]["html"]


def test_mark_ready_without_a_cadet_email_sends_nothing(api, db, outbox):
    _cadet(db, email=None)
    order = _order(api)
    item_id = order["items"][0]["id"]
    assert api.post(f"/stores/orders/{order['id']}/items/{item_id}/mark-ready", headers=STAFF).status_code == 200
    assert outbox == []


def test_mark_ready_errors(api, db):
    _cadet(db)
    a = _order(api)
    b = _order(api)
    assert api.post("/stores/orders/999/items/1/mark-ready", headers=STAFF).status_code == 404
    # An item from a different order is not found under this one.
    other_item = b["items"][0]["id"]
    assert api.post(f"/stores/orders/{a['id']}/items/{other_item}/mark-ready", headers=STAFF).status_code == 404


# ── take from / return to stock ───────────────────────────────────────────────

def _stock_call(api, order, body):
    item_id = order["items"][0]["id"]
    return api.post(f"/stores/orders/{order['id']}/items/{item_id}/stock", json=body, headers=STAFF)


def test_remove_then_return_keeps_count_and_history_in_step(api, db):
    _cadet(db)
    stock = _stock(db, _box(db), qty=1)
    order = _order(api)

    removed = _stock_call(api, order, {"action": "remove", "stockItemId": stock.id})
    assert removed.status_code == 200
    assert removed.json()["stockItem"]["quantity"] == 0
    history = removed.json()["order"]["items"][0]["stockEvents"]
    assert [e["action"] for e in history] == ["removed"]
    assert history[0]["by"] == "staff@317atc.co.uk"
    # Location keys are internal and never reach the UI.
    assert set(history[0]) == {"id", "action", "timestamp", "by"}

    # Can't take the same order item twice.
    assert _stock_call(api, order, {"action": "remove", "stockItemId": stock.id}).status_code == 400

    returned = _stock_call(api, order, {"action": "return", "by": "QM Bob"})
    assert returned.json()["stockItem"]["quantity"] == 1
    events = returned.json()["order"]["items"][0]["stockEvents"]
    assert [(e["action"], e["by"]) for e in events] == [("removed", "staff@317atc.co.uk"), ("returned", "QM Bob")]

    # ...and can't return what isn't out.
    assert _stock_call(api, order, {"action": "return"}).status_code == 400


def test_remove_errors(api, db):
    _cadet(db)
    empty = _stock(db, _box(db), qty=0)
    order = _order(api)
    assert _stock_call(api, order, {"action": "steal"}).status_code == 400
    assert _stock_call(api, order, {"action": "remove", "stockItemId": 999}).status_code == 404
    assert _stock_call(api, order, {"action": "remove"}).status_code == 404
    out = _stock_call(api, order, {"action": "remove", "stockItemId": empty.id})
    assert out.status_code == 400 and out.json()["detail"] == "Out of stock"
    assert api.post("/stores/orders/999/items/1/stock", json={"action": "remove"}, headers=STAFF).status_code == 404
    assert api.post(f"/stores/orders/{order['id']}/items/999/stock", json={"action": "remove"},
                    headers=STAFF).status_code == 404


def test_return_falls_back_to_matching_stock_when_the_row_is_gone(api, db):
    _cadet(db)
    box = _box(db, sections=("1", "2"))
    box_id = box.id
    original = _stock(db, box, "1", qty=1)
    order = _order(api)
    _stock_call(api, order, {"action": "remove", "stockItemId": original.id})

    # The row it came from is deleted, but the same item lives elsewhere.
    api.delete(f"/stores/stock/{original.id}", headers=STAFF)
    # SQLite reuses the freed id; drop the stale identity so the insert is clean.
    db.expunge_all()
    elsewhere = _stock(db, db.get(StoresBox, box_id), "2", qty=4)
    res = _stock_call(api, order, {"action": "return"})
    assert res.status_code == 200
    assert res.json()["stockItem"]["id"] == str(elsewhere.id)
    assert res.json()["stockItem"]["quantity"] == 5


def test_return_with_nowhere_to_put_it_is_a_clear_400(api, db):
    _cadet(db)
    original = _stock(db, _box(db), qty=1)
    order = _order(api)
    _stock_call(api, order, {"action": "remove", "stockItemId": original.id})
    api.delete(f"/stores/stock/{original.id}", headers=STAFF)
    res = _stock_call(api, order, {"action": "return"})
    assert res.status_code == 400
    assert "no longer exists" in res.json()["detail"]


def test_corrupt_stock_events_are_treated_as_empty(api, db):
    _cadet(db)
    order = _order(api)
    item = db.get(StoresOrderItem, int(order["items"][0]["id"]))
    item.stock_events = "garbage"
    item.qm_notes = "not json"
    db.commit()
    got = api.get("/stores/orders", headers=STAFF).json()[0]["items"][0]
    assert got["stockEvents"] == [] and got["qmNotes"] == []


# ── issuances ─────────────────────────────────────────────────────────────────

def test_cadet_issuances_upsert_by_category_and_stamp_the_order_item(api, db):
    _cadet(db)
    order = _order(api, items=[{"itemType": "Working Blue Male", "size": "15"}])
    oi = order["items"][0]["id"]

    res = api.post("/stores/issuances/1001", headers=STAFF, json={"givenBy": "QM", "items": [
        {"itemType": "Working Blue Male", "size": "15", "orderItemId": oi, "lastGiven": "2026-03-01T00:00:00"},
        {"itemType": "Not a uniform item"},
        {"itemCategory": "Beret", "sizeGiven": "56", "lastGiven": "garbage"},
    ]})
    assert res.status_code == 200
    got = {i["itemCategory"]: i for i in res.json()}
    assert set(got) == {"Working Blue Shirt", "Beret"}
    assert got["Working Blue Shirt"]["lastGiven"] == "2026-03-01T00:00:00"
    assert got["Beret"]["sizeGiven"] == "56"

    stamped = db.get(StoresOrderItem, int(oi))
    db.refresh(stamped)
    assert stamped.given_by == "QM" and stamped.given_at == datetime(2026, 3, 1)

    # A second issue of the same category updates rather than duplicates.
    api.post("/stores/issuances/1001", headers=STAFF, json={"items": [
        {"itemType": "Working Blue Female", "size": "16"}]})
    listing = api.get("/stores/issuances/1001", headers=STAFF).json()
    assert len(listing) == 2
    shirt = next(i for i in listing if i["itemCategory"] == "Working Blue Shirt")
    assert shirt["sizeGiven"] == "16"


def test_user_issuances_map_item_types_and_accept_categories(api, db):
    user = User(google_id="g1", email="adult@x")
    db.add(user)
    db.commit()
    res = api.post(f"/stores/issuances/user/{user.id}", headers=STAFF, json={"items": [
        {"itemCategory": "Trousers"}, {"itemCategory": "Tie"}, {"itemCategory": ""}]})
    assert sorted(i["itemCategory"] for i in res.json()) == ["Slacks/Trousers", "Tie"]
    assert len(api.get(f"/stores/issuances/user/{user.id}", headers=STAFF).json()) == 2


def test_issuance_subject_must_exist(api):
    assert api.get("/stores/issuances/1", headers=STAFF).status_code == 404
    assert api.post("/stores/issuances/1", json={"items": []}, headers=STAFF).status_code == 404
    assert api.get("/stores/issuances/user/1", headers=STAFF).status_code == 404
    assert api.post("/stores/issuances/user/1", json={"items": []}, headers=STAFF).status_code == 404


def test_delete_issuance(api, db):
    _cadet(db)
    issued = api.post("/stores/issuances/1001", headers=STAFF, json={"items": [{"itemCategory": "Beret"}]}).json()
    assert api.delete(f"/stores/issuances/{issued[0]['id']}", headers=STAFF).status_code == 204
    assert api.delete(f"/stores/issuances/{issued[0]['id']}", headers=STAFF).status_code == 404


# ── logs forms ────────────────────────────────────────────────────────────────

def _add_to_logs(api, order, idx=0, **extra):
    return api.post("/stores/logs-forms/entries", headers=STAFF,
                    json={"orderItemId": order["items"][idx]["id"], **extra})


def test_logs_form_lifecycle(api, db):
    _cadet(db, rank="Sgt")
    order = _order(api, items=[
        {"itemType": "Beret", "size": "55"},
        {"itemType": "Tie"},
        {"itemType": "Belt"},
    ])
    form = _add_to_logs(api, order, 0)
    assert form.status_code == 201
    form = _add_to_logs(api, order, 1, tieVariant="Short").json()
    form = _add_to_logs(api, order, 2).json()
    assert [(e["itemType"], e["size"]) for e in form["entries"]] == [
        ("Beret", "55"), ("Tie", "Short"), ("Belt", "64-114cm")]
    assert form["orderedAt"] is None
    assert all(e["cadetName"] == "Amy Adams" for e in form["entries"])

    # The same item can't go on twice.
    assert _add_to_logs(api, order, 0).status_code == 409

    download = api.get(f"/stores/logs-forms/{form['id']}/download", headers=STAFF)
    assert download.status_code == 200
    assert "Logs Form 202" in download.headers["content-disposition"]
    openpyxl.load_workbook(io.BytesIO(download.content))

    ordered = api.post(f"/stores/logs-forms/{form['id']}/mark-ordered", headers=STAFF).json()
    assert ordered["orderedAt"] is not None
    assert api.post(f"/stores/logs-forms/{form['id']}/mark-ordered", headers=STAFF).status_code == 400

    # An ordered form is frozen.
    entry_id = ordered["entries"][0]["id"]
    assert api.delete(f"/stores/logs-forms/entries/{entry_id}", headers=STAFF).status_code == 400

    # The next item starts a fresh batch.
    more = _order(api, items=[{"itemType": "Jumper", "size": "96"}])
    new_form = _add_to_logs(api, more).json()
    assert new_form["id"] != form["id"]
    assert [f["id"] for f in api.get("/stores/logs-forms", headers=STAFF).json()] == [new_form["id"], form["id"]]


@pytest.mark.parametrize("item,extra,detail", [
    ({"itemType": "Brassard"}, {}, "cannot go on a logs form"),
    ({"itemType": "Tie"}, {}, "tieVariant"),
    ({"itemType": "Tie"}, {"tieVariant": "Long"}, "tieVariant"),
    ({"itemType": "Beret", "size": ""}, {}, "no size"),
    ({"itemType": "Beret", "size": "55", "needSizing": True}, {}, "no size"),
])
def test_logs_form_entry_rejections(api, db, item, extra, detail):
    _cadet(db)
    order = _order(api, items=[item])
    res = _add_to_logs(api, order, **extra)
    assert res.status_code == 400
    assert detail in res.json()["detail"]


def test_logs_form_entry_lookup_errors(api):
    assert api.post("/stores/logs-forms/entries", json={}, headers=STAFF).status_code == 400
    assert api.post("/stores/logs-forms/entries", json={"orderItemId": 5}, headers=STAFF).status_code == 404
    assert api.delete("/stores/logs-forms/entries/5", headers=STAFF).status_code == 404
    assert api.post("/stores/logs-forms/5/mark-ordered", headers=STAFF).status_code == 404
    assert api.get("/stores/logs-forms/5/download", headers=STAFF).status_code == 404


def test_logs_form_entry_survives_its_order_being_deleted(api, db):
    _cadet(db)
    order = _order(api)
    form = _add_to_logs(api, order).json()
    api.delete(f"/stores/orders/{order['id']}", headers=STAFF)
    entry = api.get("/stores/logs-forms", headers=STAFF).json()[0]["entries"][0]
    assert entry["id"] == form["entries"][0]["id"]
    assert entry["itemType"] == "Beret" and entry["cadetName"] == "Amy Adams"


def test_delete_entry_from_open_form_then_download_empty_is_400(api, db):
    _cadet(db)
    order = _order(api)
    form = _add_to_logs(api, order).json()
    assert api.delete(f"/stores/logs-forms/entries/{form['entries'][0]['id']}", headers=STAFF).status_code == 204
    assert api.get(f"/stores/logs-forms/{form['id']}/download", headers=STAFF).status_code == 400


def test_logs_form_download_nominal_roll_marks_exchange_vs_initial(api, db, monkeypatch):
    captured = {}

    def fake_generate(entries, nominal_roll):
        captured["entries"], captured["roll"] = entries, nominal_roll
        return b"xlsx"

    import form_generators.logs_form_gen as gen
    monkeypatch.setattr(gen, "generate_logs_form", fake_generate)

    _cadet(db, 1, first_name="New", last_name="Cadet", rank="Cdt")
    _cadet(db, 2, first_name="Old", last_name="Hand", rank=None)
    db.add(StoresItemIssuance(cadet_id=2, item_category="Beret", last_given=datetime(2024, 1, 1)))
    staff_user = User(google_id="g", email="adult@x", first_name="Ad", last_name="Ult")
    db.add(staff_user)
    db.commit()
    adult_order = StoresOrder(user_id=staff_user.id, created_at=datetime.now())
    db.add(adult_order)
    db.flush()
    db.add(StoresOrderItem(order_id=adult_order.id, item_type="Beret", size="57"))
    db.commit()

    o1 = _order(api, cin=1, items=[{"itemType": "Beret", "size": "55"}, {"itemType": "Belt"}])
    o2 = _order(api, cin=2)
    _add_to_logs(api, o1, 0)
    _add_to_logs(api, o1, 1)
    _add_to_logs(api, o2)
    adult_item = db.query(StoresOrderItem).filter(StoresOrderItem.order_id == adult_order.id).one()
    form = api.post("/stores/logs-forms/entries", json={"orderItemId": adult_item.id}, headers=STAFF).json()

    res = api.get(f"/stores/logs-forms/{form['id']}/download", headers=STAFF)
    assert res.status_code == 200 and res.content == b"xlsx"
    assert len(captured["entries"]) == 4
    # One line per person, not per item; adults get no rank/issue type.
    assert captured["roll"] == [
        ("Cdt", "New Cadet", "Initial Issue"),
        ("", "Old Hand", "Exchange"),
        ("", "Ad Ult", ""),
    ]


def test_order_subject_helper():
    assert stores._order_subject(StoresOrder()) == ("Unknown", None)


def test_qm_notes_and_events_parsing_helpers():
    assert stores._qm_notes_list(None) == []
    assert stores._qm_notes_list("  [1]") == [1]
    assert stores._qm_notes_list("{}") == []
    events = [stock_events.new_event(stock_events.REMOVED, "", stockItemId=1)]
    assert events[0]["by"] is None
    assert stock_events.public_events(json.dumps(events))[0]["action"] == "removed"


def test_box_dict_defaults_for_null_layout_columns(db):
    box = StoresBox(label="N", shelf_level=None, shelf_position=None, box_width=None, top_end=None)
    db.add(box)
    db.flush()
    db.add(StoresSection(box_id=box.id, label="s", position=None, section_row=None, section_width=None))
    db.commit()
    db.refresh(box)
    d = stores._box_to_dict(box)
    assert (d["shelfLevel"], d["shelfPosition"], d["boxWidth"], d["topEnd"]) == (1, 0, 100, "left")
    assert d["sections"] == [{"label": "s", "row": 0, "position": 0, "sectionWidth": 100}]


def test_logs_form_model_defaults(db):
    form = LogsForm(created_at=datetime(2026, 1, 1))
    db.add(form)
    db.commit()
    assert stores._logs_form_to_dict(form) == {
        "id": str(form.id), "createdAt": "2026-01-01T00:00:00", "orderedAt": None, "entries": []}
