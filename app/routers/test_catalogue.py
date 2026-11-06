"""The editable catalogue: who may edit it, what each edit does to the pickers
and the stock insights, and the rules that stop an edit orphaning data already
recorded against an item or a badge."""

import json
from datetime import datetime

import pytest

from core import catalogue
from database.models import (
    ISSUANCE_CATEGORIES,
    BadgeItem,
    BadgeOrder,
    BadgeOrderItem,
    Cadet,
    CatalogueBadgeCategory,
    CatalogueItemType,
    StoresBox,
    StoresItem,
    StoresOrder,
    StoresOrderItem,
    StoresSection,
)

# (method, path, json) for every catalogue endpoint: all staff only.
ENDPOINTS = [
    ("get", "/catalogue/uniform", None),
    ("post", "/catalogue/uniform", {"name": "Fleece", "noSize": True}),
    ("patch", "/catalogue/uniform/1", {"active": False}),
    ("delete", "/catalogue/uniform/1", None),
    ("put", "/catalogue/uniform-order", {"ids": []}),
    ("get", "/catalogue/badges", None),
    ("post", "/catalogue/badges", {"name": "Band", "levels": ["Blue"]}),
    ("patch", "/catalogue/badges/1", {"active": False}),
    ("delete", "/catalogue/badges/1", None),
    ("put", "/catalogue/badges-order", {"ids": []}),
]


def _call(api, method, path, body, headers=None):
    kwargs = {"headers": headers} if headers is not None else {}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)(path, **kwargs)


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_no_token_is_401(api, method, path, body):
    assert _call(api, method, path, body).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_only_staff_may_edit_the_catalogue(api, persona, method, path, body):
    assert _call(api, method, path, body, api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_account_can_read_it(api, persona):
    res = api.get("/catalogue/uniform", headers=api.as_(persona))
    assert res.status_code == 200 and res.json()


def _staff(api):
    return api.as_("staff")


def _items(api):
    return {i["name"]: i for i in api.get("/catalogue/uniform", headers=_staff(api)).json()}


def _badges(api):
    return {c["slug"]: c for c in api.get("/catalogue/badges", headers=_staff(api)).json()}


def _reference(api):
    return api.get("/reference", headers=_staff(api)).json()


# ── the starting point ────────────────────────────────────────────────────────

def test_the_first_read_starts_from_the_squadrons_original_lists(api):
    items = _items(api)
    assert list(items) == list(catalogue.STOCK_ITEM_TYPES)
    assert items["Polo"]["stockOnly"] and not items["Beret"]["stockOnly"]
    assert items["Beret"]["sizes"] == list(catalogue.UNIFORM_SIZES["Beret"])
    assert items["Belt"]["noSize"] and items["Belt"]["onOrderList"] is False
    assert items["Slacks"]["issuanceCategory"] == "Slacks/Trousers"
    assert items["Jumper"]["issuanceCategory"] is None
    assert [c["slug"] for c in _badges(api).values()] == [c["id"] for c in catalogue.BADGE_CATEGORIES]


def test_reference_serves_the_same_shape_it_always_did(api):
    ref = _reference(api)
    uniform = ref["uniform"]
    assert uniform["itemTypes"] == list(catalogue.UNIFORM_ITEM_TYPES)
    assert uniform["noSizeItems"] == sorted(catalogue.NO_SIZE_ITEMS)
    assert uniform["sizes"]["Tie"] == ["Short", "Standard"]
    assert uniform["sizingFields"] == {k: list(v) for k, v in catalogue.SIZING_FIELDS.items()}
    assert uniform["gender"]["Skirt"] == "female"
    assert uniform["issuanceCategories"] == ISSUANCE_CATEGORIES
    assert uniform["issuanceCategoryByItem"]["Trousers"] == "Slacks/Trousers"
    # Same items; they now follow the catalogue's order rather than the old list's.
    assert sorted(uniform["kitFlightItems"]) == sorted(catalogue.KIT_FLIGHT_ITEMS)
    badges = ref["badges"]
    assert badges["categories"] == catalogue.BADGE_CATEGORIES
    assert badges["categoriesWithoutGainedWhere"] == sorted(catalogue.BADGE_CATEGORIES_WITHOUT_GAINED_WHERE)


def test_seeding_twice_does_not_duplicate(api, db):
    _items(api)
    _items(api)
    assert db.query(CatalogueItemType).count() == len(catalogue.STOCK_ITEM_TYPES)


def test_an_item_can_be_made_stock_only_and_back(api):
    created = api.post("/catalogue/uniform", headers=_staff(api),
                       json={"name": "Hoodie", "sizes": ["M"], "stockOnly": True, "kitFlight": True}).json()
    uniform = _reference(api)["uniform"]
    # Stocked but never ordered, and kit can't include what can't be ordered.
    assert "Hoodie" in uniform["stockItemTypes"] and "Hoodie" not in uniform["itemTypes"]
    assert "Hoodie" not in uniform["kitFlightItems"]
    api.patch(f"/catalogue/uniform/{created['id']}", headers=_staff(api), json={"stockOnly": False})
    assert "Hoodie" in _reference(api)["uniform"]["itemTypes"]


# ── uniform items ─────────────────────────────────────────────────────────────

def test_a_new_item_appears_in_the_pickers_with_its_sizes(api):
    res = api.post("/catalogue/uniform", headers=_staff(api), json={
        "name": "  Fleece ", "gender": "unisex", "sizes": ["S", "M", " M ", "L", ""], "kitFlight": True,
    })
    assert res.status_code == 201, res.text
    body = res.json()
    # Trimmed, blanks and repeats dropped, the typed order kept.
    assert body["name"] == "Fleece" and body["sizes"] == ["S", "M", "L"]
    uniform = _reference(api)["uniform"]
    assert uniform["itemTypes"][-1] == "Fleece"
    assert uniform["sizes"]["Fleece"] == ["S", "M", "L"]
    assert "Fleece" in uniform["kitFlightItems"]
    # With no issuance line of its own it is recorded under its own name.
    assert uniform["issuanceCategoryByItem"]["Fleece"] == "Fleece"
    assert "Fleece" in uniform["issuanceCategories"]


def test_a_new_item_stocks_and_orders_like_a_built_in_one(api, db):
    api.post("/catalogue/uniform", headers=_staff(api),
             json={"name": "Fleece", "gender": "female", "sizes": ["S", "M"]})
    api.post("/stores/structure", headers=_staff(api), json={"action": "add-box", "box": "A"})
    api.post("/stores/structure", headers=_staff(api), json={"action": "add-section", "box": "A", "section": "1"})
    res = api.post("/stores/stock", headers=_staff(api), json={
        "itemType": "Fleece", "size": "M", "box": "A", "section": "1", "quantity": 3})
    assert res.status_code in (200, 201), res.text
    # The gender the QM chose is what the stock row carries.
    assert res.json()["gender"] == "female"


@pytest.mark.parametrize("body,field", [
    ({"name": ""}, "name"),
    ({"name": "   "}, "name"),
    ({"name": "x" * 61, "noSize": True}, "name"),
    ({"name": "Fleece", "gender": "robot"}, "gender"),
    ({"name": "Fleece", "lowStockLevel": -1}, "lowStockLevel"),
    ({"name": "Fleece", "sizes": ["x" * 61]}, "sizes"),
    ({"name": "Fleece", "sizes": [str(n) for n in range(301)]}, "sizes"),
])
def test_a_bad_item_is_rejected_with_422(api, body, field):
    res = api.post("/catalogue/uniform", headers=_staff(api), json=body)
    assert res.status_code == 422
    assert field in json.dumps(res.json()["detail"])
    assert "Fleece" not in _items(api)


def test_a_sized_item_needs_sizes(api):
    # Without them it could be added to the list but never stocked or ordered.
    res = api.post("/catalogue/uniform", headers=_staff(api), json={"name": "Fleece"})
    assert res.status_code == 422 and "size" in res.json()["detail"]
    assert api.post("/catalogue/uniform", headers=_staff(api),
                    json={"name": "Fleece", "noSize": True}).status_code == 201


def test_emptying_the_sizes_of_a_sized_item_is_rejected(api):
    item = _items(api)["Beret"]
    res = api.patch(f"/catalogue/uniform/{item['id']}", headers=_staff(api), json={"sizes": []})
    assert res.status_code == 422
    assert _items(api)["Beret"]["sizes"] == item["sizes"]


@pytest.mark.parametrize("name", ["Beret", "beret", " BERET "])
def test_a_duplicate_name_is_409_whatever_the_case(api, name):
    res = api.post("/catalogue/uniform", headers=_staff(api), json={"name": name, "noSize": True})
    assert res.status_code == 409


def test_non_ascii_names_and_sizes_are_kept(api):
    res = api.post("/catalogue/uniform", headers=_staff(api),
                   json={"name": "Écharpe Ó", "sizes": ["Petit—S", "Grand ½"]})
    assert res.status_code == 201
    assert _reference(api)["uniform"]["sizes"]["Écharpe Ó"] == ["Petit—S", "Grand ½"]


def test_editing_sizes_changes_the_pickers_and_nothing_else(api):
    item = _items(api)["Beret"]
    res = api.patch(f"/catalogue/uniform/{item['id']}", headers=_staff(api), json={"sizes": ["50", "52", "64"]})
    assert res.status_code == 200
    assert _reference(api)["uniform"]["sizes"]["Beret"] == ["50", "52", "64"]
    assert _items(api)["Beret"]["name"] == "Beret"


def test_the_name_cannot_be_changed(api):
    # Stock rows and orders store the name as text; renaming would orphan them.
    item = _items(api)["Beret"]
    api.patch(f"/catalogue/uniform/{item['id']}", headers=_staff(api), json={"name": "Hat"})
    assert "Beret" in _items(api) and "Hat" not in _items(api)


def test_a_missing_item_is_404(api):
    assert api.patch("/catalogue/uniform/9999", headers=_staff(api), json={"active": False}).status_code == 404
    assert api.delete("/catalogue/uniform/9999", headers=_staff(api)).status_code == 404


def test_a_retired_item_leaves_the_pickers_but_stays_on_the_page(api):
    item = _items(api)["Skirt"]
    api.patch(f"/catalogue/uniform/{item['id']}", headers=_staff(api), json={"active": False})
    uniform = _reference(api)["uniform"]
    assert "Skirt" not in uniform["itemTypes"] and "Skirt" not in uniform["sizes"]
    assert "Skirt" not in uniform["kitFlightItems"]
    assert _items(api)["Skirt"]["active"] is False
    api.patch(f"/catalogue/uniform/{item['id']}", headers=_staff(api), json={"active": True})
    assert "Skirt" in _reference(api)["uniform"]["itemTypes"]


def test_a_retired_item_still_maps_to_its_issuance_line(api, db):
    # A cadet's old Slacks record must keep showing under "Slacks/Trousers".
    item = _items(api)["Slacks"]
    api.patch(f"/catalogue/uniform/{item['id']}", headers=_staff(api), json={"active": False})
    db.add(Cadet(cin=1, first_name="A", last_name="B", email="a@b.c"))
    db.commit()
    res = api.post("/stores/issuances/1", headers=_staff(api), json={
        "givenBy": "QM", "items": [{"itemType": "Slacks", "sizeGiven": "70/60/84"}]})
    assert res.status_code == 200
    assert res.json()[0]["itemCategory"] == "Slacks/Trousers"


def _stock_one(db, item_type="Beret"):
    box = StoresBox(label="A")
    db.add(box)
    db.flush()
    section = StoresSection(box_id=box.id, label="1")
    db.add(section)
    db.flush()
    db.add(StoresItem(item_type=item_type, size="56", quantity=1, gender="unisex",
                      box_id=box.id, section_id=section.id))
    db.commit()


def test_an_item_in_stock_cannot_be_deleted_only_retired(api, db):
    _stock_one(db)
    item = _items(api)["Beret"]
    assert item["stockRows"] == 1
    res = api.delete(f"/catalogue/uniform/{item['id']}", headers=_staff(api))
    assert res.status_code == 409 and "retire" in res.json()["detail"]
    assert "Beret" in _items(api)


def test_an_item_on_an_order_cannot_be_deleted(api, db):
    db.add(Cadet(cin=1, first_name="A", last_name="B", email="a@b.c"))
    db.flush()
    order = StoresOrder(cadet_id=1, created_at=datetime.now())
    db.add(order)
    db.flush()
    db.add(StoresOrderItem(order_id=order.id, item_type="Beret", size="56"))
    db.commit()
    item = _items(api)["Beret"]
    assert item["orderItems"] == 1
    assert api.delete(f"/catalogue/uniform/{item['id']}", headers=_staff(api)).status_code == 409


def test_an_unused_item_can_be_deleted(api):
    created = api.post("/catalogue/uniform", headers=_staff(api), json={"name": "Typo", "noSize": True}).json()
    assert api.delete(f"/catalogue/uniform/{created['id']}", headers=_staff(api)).status_code == 204
    assert "Typo" not in _items(api)


def test_reordering_changes_the_picker_order(api):
    ids = [i["id"] for i in _items(api).values()]
    res = api.put("/catalogue/uniform-order", headers=_staff(api), json={"ids": list(reversed(ids))})
    assert res.status_code == 200
    assert _reference(api)["uniform"]["itemTypes"][0] == catalogue.UNIFORM_ITEM_TYPES[-1]


@pytest.mark.parametrize("make_ids", [
    lambda ids: ids[:-1],            # misses one: a stale page
    lambda ids: ids + [ids[0]],      # repeats one
    lambda ids: ids + [99999],       # names one that doesn't exist
    lambda ids: [],
])
def test_a_reorder_that_is_not_the_whole_list_is_400(api, make_ids):
    ids = [i["id"] for i in _items(api).values()]
    res = api.put("/catalogue/uniform-order", headers=_staff(api), json={"ids": make_ids(ids)})
    assert res.status_code == 400
    assert [i["id"] for i in _items(api).values()] == ids


def test_an_issuance_line_can_be_shared_and_cleared(api):
    created = api.post("/catalogue/uniform", headers=_staff(api), json={
        "name": "Fleece", "noSize": True, "issuanceCategory": "Jumper"}).json()
    assert _reference(api)["uniform"]["issuanceCategoryByItem"]["Fleece"] == "Jumper"
    api.patch(f"/catalogue/uniform/{created['id']}", headers=_staff(api), json={"issuanceCategory": ""})
    assert _reference(api)["uniform"]["issuanceCategoryByItem"]["Fleece"] == "Fleece"


def test_kit_flight_orders_use_the_items_ticked_for_it(api, db):
    db.add(Cadet(cin=1, first_name="A", last_name="B", email="a@b.c", flight="C"))
    db.commit()
    for item in _items(api).values():
        want = item["name"] in ("Beret", "Belt")
        api.patch(f"/catalogue/uniform/{item['id']}", headers=_staff(api), json={"kitFlight": want})
    res = api.post("/stores/orders/kit-flight", headers=_staff(api))
    assert res.status_code in (200, 201), res.text
    assert sorted(i["itemType"] for i in res.json()[0]["items"]) == ["Belt", "Beret"]


# ── stock levels ──────────────────────────────────────────────────────────────

def _insights(api):
    return api.get("/stores/insights", headers=_staff(api)).json()


def _shelf(db, item_type, size, qty):
    box = db.query(StoresBox).first() or StoresBox(label="A")
    db.add(box)
    db.flush()
    section = db.query(StoresSection).first() or StoresSection(box_id=box.id, label="1")
    db.add(section)
    db.flush()
    db.add(StoresItem(item_type=item_type, size=size, quantity=qty, gender="unisex",
                      box_id=box.id, section_id=section.id))
    db.commit()


def test_a_stock_level_orders_a_size_nobody_has_asked_for_lately(api, db):
    # Demand alone says "unused": nobody asked, so nothing to order.
    _shelf(db, "Jumper", "94", 1)
    assert _insights(api)["toOrder"] == []
    jumper = _items(api)["Jumper"]
    res = api.patch(f"/catalogue/uniform/{jumper['id']}", headers=_staff(api), json={"lowStockLevel": 4})
    assert res.status_code == 200 and res.json()["lowStockLevel"] == 4
    lines = _insights(api)["toOrder"]
    assert [(r["itemType"], r["size"], r["toOrder"], r["status"]) for r in lines] == [("Jumper", "94", 3, "low")]


def test_a_stock_level_is_met_when_the_shelf_holds_enough(api, db):
    _shelf(db, "Jumper", "94", 4)
    jumper = _items(api)["Jumper"]
    api.patch(f"/catalogue/uniform/{jumper['id']}", headers=_staff(api), json={"lowStockLevel": 4})
    assert _insights(api)["toOrder"] == []


def test_clearing_a_stock_level_goes_back_to_working_it_out(api, db):
    _shelf(db, "Jumper", "94", 1)
    jumper = _items(api)["Jumper"]
    api.patch(f"/catalogue/uniform/{jumper['id']}", headers=_staff(api), json={"lowStockLevel": 4})
    api.patch(f"/catalogue/uniform/{jumper['id']}", headers=_staff(api), json={"lowStockLevel": None})
    assert _items(api)["Jumper"]["lowStockLevel"] is None
    assert _insights(api)["toOrder"] == []


def test_an_edit_that_leaves_the_level_out_does_not_clear_it(api):
    jumper = _items(api)["Jumper"]
    api.patch(f"/catalogue/uniform/{jumper['id']}", headers=_staff(api), json={"lowStockLevel": 4})
    api.patch(f"/catalogue/uniform/{jumper['id']}", headers=_staff(api), json={"gender": "male"})
    assert _items(api)["Jumper"]["lowStockLevel"] == 4


def test_an_item_off_the_order_list_stays_off_even_with_a_level(api, db):
    _shelf(db, "Beret", "56", 0)
    beret = _items(api)["Beret"]
    assert beret["onOrderList"] is False
    api.patch(f"/catalogue/uniform/{beret['id']}", headers=_staff(api), json={"lowStockLevel": 5})
    assert _insights(api)["toOrder"] == []
    api.patch(f"/catalogue/uniform/{beret['id']}", headers=_staff(api), json={"onOrderList": True})
    assert _insights(api)["notOnOrderList"] == ["Tie", "Brassard", "Belt"]


def test_insights_follows_the_sizes_staff_put_in_order(api, db):
    _shelf(db, "Beret", "56", 1)
    _shelf(db, "Beret", "50", 1)
    beret = _items(api)["Beret"]
    api.patch(f"/catalogue/uniform/{beret['id']}", headers=_staff(api), json={"sizes": ["56", "50"]})
    item = next(i for i in _insights(api)["items"] if i["itemType"] == "Beret")
    assert [r["size"] for r in item["sizes"]] == ["56", "50"]


def test_a_custom_item_with_no_size_is_one_line_in_the_insights(api, db):
    api.post("/catalogue/uniform", headers=_staff(api), json={"name": "Lanyard", "noSize": True})
    _shelf(db, "Lanyard", "whatever", 2)
    item = next(i for i in _insights(api)["items"] if i["itemType"] == "Lanyard")
    assert [r["size"] for r in item["sizes"]] == [""] and item["inStock"] == 2


# ── badges ────────────────────────────────────────────────────────────────────

def test_a_new_badge_category_reaches_the_order_forms(api):
    res = api.post("/catalogue/badges", headers=_staff(api), json={
        "name": "Band Badges", "prefix": "Band", "levels": ["Blue", "Bronze"], "gainedWhere": False})
    assert res.status_code == 201, res.text
    assert res.json()["slug"] == "band-badges"
    badges = _reference(api)["badges"]
    assert badges["categories"][-1] == {"id": "band-badges", "name": "Band Badges",
                                        "prefix": "Band", "levels": ["Blue", "Bronze"]}
    assert "band-badges" in badges["categoriesWithoutGainedWhere"]


def test_a_category_of_named_badges(api):
    res = api.post("/catalogue/badges", headers=_staff(api),
                   json={"name": "Awards", "items": ["Duke of Edinburgh Gold", "Cadet of the Year"]})
    assert res.status_code == 201
    assert _reference(api)["badges"]["categories"][-1]["items"] == ["Duke of Edinburgh Gold", "Cadet of the Year"]


@pytest.mark.parametrize("body", [
    {"name": "Band"},                                              # neither
    {"name": "Band", "levels": ["Blue"], "items": ["A"]},          # both
    {"name": "Band", "levels": [" ", ""]},                         # blanks only
    {"name": ""},
])
def test_a_badge_category_needs_levels_or_names_not_both(api, body):
    assert api.post("/catalogue/badges", headers=_staff(api), json=body).status_code == 422


def test_a_duplicate_category_is_409(api):
    assert api.post("/catalogue/badges", headers=_staff(api),
                    json={"name": "music badges", "levels": ["Blue"]}).status_code == 409


def test_a_category_cannot_claim_a_badge_another_category_already_has(api):
    res = api.post("/catalogue/badges", headers=_staff(api),
                   json={"name": "More music", "prefix": "Music", "levels": ["Gold"]})
    assert res.status_code == 409 and "Music – Gold" in res.json()["detail"]


def test_two_categories_with_the_same_slug_get_distinct_ids(api):
    first = api.post("/catalogue/badges", headers=_staff(api), json={"name": "Band!", "levels": ["Blue"]}).json()
    second = api.post("/catalogue/badges", headers=_staff(api), json={"name": "Band?", "levels": ["Blue"]})
    assert first["slug"] == "band" and second.status_code == 201 and second.json()["slug"] == "band-2"


def test_levels_can_be_added_to_a_category(api):
    cyber = _badges(api)["cyber"]
    res = api.patch(f"/catalogue/badges/{cyber['id']}", headers=_staff(api),
                    json={"levels": ["Blue", "Bronze", "Silver", "Gold"]})
    assert res.status_code == 200
    cat = next(c for c in _reference(api)["badges"]["categories"] if c["id"] == "cyber")
    assert cat["levels"][0] == "Blue" and cat["prefix"] == "Cyber"


def _order_badge(db, name):
    db.add(Cadet(cin=1, first_name="A", last_name="B", email="a@b.c"))
    db.flush()
    order = BadgeOrder(cadet_id=1, created_at=datetime.now())
    db.add(order)
    db.flush()
    db.add(BadgeOrderItem(order_id=order.id, badge_name=name))
    db.commit()


def test_a_level_that_has_been_ordered_cannot_be_removed(api, db):
    _order_badge(db, "Music – Gold")
    music = _badges(api)["music"]
    res = api.patch(f"/catalogue/badges/{music['id']}", headers=_staff(api), json={"levels": ["Blue", "Bronze", "Silver"]})
    assert res.status_code == 409 and "Music – Gold" in res.json()["detail"]
    assert _badges(api)["music"]["levels"] == music["levels"]


def test_a_level_nobody_has_ordered_can_be_removed(api, db):
    _order_badge(db, "Music – Gold")
    music = _badges(api)["music"]
    res = api.patch(f"/catalogue/badges/{music['id']}", headers=_staff(api), json={"levels": ["Blue", "Gold"]})
    assert res.status_code == 200 and res.json()["levels"] == ["Blue", "Gold"]


def test_a_badge_on_the_grid_counts_as_in_use(api, db):
    from database.models import BadgeGridCell
    cell = BadgeGridCell(row=0, col=0)
    db.add(cell)
    db.flush()
    db.add(BadgeItem(cell_id=cell.id, name="Radio – Blue", quantity=2))
    db.commit()
    radio = _badges(api)["radio"]
    assert radio["inUse"] == 1
    res = api.patch(f"/catalogue/badges/{radio['id']}", headers=_staff(api), json={"levels": ["Bronze"]})
    assert res.status_code == 409


def test_a_category_cannot_become_the_other_kind_or_empty(api):
    music = _badges(api)["music"]
    core = _badges(api)["core"]
    assert api.patch(f"/catalogue/badges/{music['id']}", headers=_staff(api), json={"items": ["X"]}).status_code == 422
    assert api.patch(f"/catalogue/badges/{core['id']}", headers=_staff(api), json={"levels": ["X"]}).status_code == 422
    assert api.patch(f"/catalogue/badges/{music['id']}", headers=_staff(api), json={"levels": []}).status_code == 422
    assert api.patch(f"/catalogue/badges/{core['id']}", headers=_staff(api), json={"items": []}).status_code == 422


def test_renaming_a_category_keeps_its_badge_names(api):
    # The display name is not part of a badge's name ("<prefix> – <level>").
    music = _badges(api)["music"]
    api.patch(f"/catalogue/badges/{music['id']}", headers=_staff(api), json={"name": "Band & Music"})
    cat = next(c for c in _reference(api)["badges"]["categories"] if c["id"] == "music")
    assert cat["name"] == "Band & Music" and cat["prefix"] == "Music"


def test_renaming_to_another_categorys_name_is_409(api):
    music = _badges(api)["music"]
    res = api.patch(f"/catalogue/badges/{music['id']}", headers=_staff(api), json={"name": "Radio Badges"})
    assert res.status_code == 409


def test_a_retired_category_leaves_the_forms(api):
    music = _badges(api)["music"]
    api.patch(f"/catalogue/badges/{music['id']}", headers=_staff(api), json={"active": False})
    ids = [c["id"] for c in _reference(api)["badges"]["categories"]]
    assert "music" not in ids
    assert _badges(api)["music"]["active"] is False


def test_a_category_in_use_cannot_be_deleted_but_an_unused_one_can(api, db):
    _order_badge(db, "Music – Gold")
    music = _badges(api)["music"]
    assert api.delete(f"/catalogue/badges/{music['id']}", headers=_staff(api)).status_code == 409
    space = _badges(api)["space"]
    assert api.delete(f"/catalogue/badges/{space['id']}", headers=_staff(api)).status_code == 204
    assert "space" not in _badges(api)


def test_a_missing_category_is_404(api):
    assert api.patch("/catalogue/badges/9999", headers=_staff(api), json={"active": False}).status_code == 404
    assert api.delete("/catalogue/badges/9999", headers=_staff(api)).status_code == 404


def test_badge_categories_can_be_reordered(api):
    ids = [c["id"] for c in _badges(api).values()]
    assert api.put("/catalogue/badges-order", headers=_staff(api), json={"ids": list(reversed(ids))}).status_code == 200
    assert _reference(api)["badges"]["categories"][0]["id"] == catalogue.BADGE_CATEGORIES[-1]["id"]
    assert api.put("/catalogue/badges-order", headers=_staff(api), json={"ids": ids[:-1]}).status_code == 400


def test_a_reference_with_corrupt_stored_lists_degrades_to_empty(api, db):
    # A hand-edited row must not 500 every form in the app.
    api.get("/reference", headers=_staff(api))
    db.query(CatalogueItemType).filter_by(name="Beret").update({"sizes": "not json"})
    db.query(CatalogueBadgeCategory).filter_by(slug="music").update({"levels": '{"a": 1}'})
    db.commit()
    res = api.get("/reference", headers=_staff(api))
    assert res.status_code == 200
    assert "Beret" not in res.json()["uniform"]["sizes"]


# ── stock-only items (Polo) ───────────────────────────────────────────────────

def test_polo_is_stock_only_with_its_own_sizes_and_stocks_as_unisex(api, db):
    polo = _items(api)["Polo"]
    assert polo["stockOnly"] and polo["gender"] == "unisex" and polo["sizes"] == ["XS", "S", "M", "L", "XL"]
    api.post("/stores/structure", headers=_staff(api), json={"action": "add-box", "box": "A"})
    api.post("/stores/structure", headers=_staff(api), json={"action": "add-section", "box": "A", "section": "1"})
    res = api.post("/stores/stock", headers=_staff(api),
                   json={"itemType": "Polo", "size": "XL", "box": "A", "section": "1", "quantity": 2})
    assert res.status_code == 201 and res.json()["gender"] == "unisex"


def test_polo_sizes_sort_by_the_catalogue_not_the_alphabet_in_insights(api, db):
    for size in ("XS", "XL", "M"):
        _shelf(db, "Polo", size, 1)
    polo = next(i for i in _insights(api)["items"] if i["itemType"] == "Polo")
    assert [r["size"] for r in polo["sizes"]] == ["XS", "M", "XL"]


def test_polo_is_never_kitted_or_offered_to_cadets(api, db):
    # Even if someone ticks "part of a new cadet's kit", a stock-only item stays off the order.
    polo = _items(api)["Polo"]
    api.patch(f"/catalogue/uniform/{polo['id']}", headers=_staff(api), json={"kitFlight": True})
    uniform = _reference(api)["uniform"]
    assert "Polo" not in uniform["kitFlightItems"] and "Polo" not in uniform["itemTypes"]
    db.add(Cadet(cin=1, first_name="A", last_name="B", email="a@b.c", flight="C"))
    db.commit()
    res = api.post("/stores/orders/kit-flight", headers=_staff(api))
    assert "Polo" not in [i["itemType"] for i in res.json()[0]["items"]]


def test_retiring_polo_takes_it_off_the_stock_pages_too(api):
    polo = _items(api)["Polo"]
    api.patch(f"/catalogue/uniform/{polo['id']}", headers=_staff(api), json={"active": False})
    assert "Polo" not in _reference(api)["uniform"]["stockItemTypes"]
