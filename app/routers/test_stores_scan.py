"""Booking stock in by scanning, over HTTP: looking a stock number up,
teaching one the form doesn't list, reading a label photo and booking in."""

import json

import httpx
import pytest

from core import label_reader, nsn
from database.models import StoresBox, StoresItem, StoresNsn, StoresSection, StoresStockChange

STAFF = {"Authorization": "Bearer staff"}
SHIRT = "8405998456634"  # Shirt Long Sleeve Mid Blue Male Size 110/40
OLD_SHIRT = "8410998696671"  # a women's shirt label the current form doesn't list

ENDPOINTS = [
    ("get", "/stores/nsns", None),
    ("get", f"/stores/nsns/{SHIRT}", None),
    ("put", f"/stores/nsns/{OLD_SHIRT}", {"itemType": "Beret", "size": "56"}),
    ("delete", f"/stores/nsns/{OLD_SHIRT}", None),
    ("post", "/stores/nsns/read-label", None),
    ("post", "/stores/stock/book-in", {"nsn": SHIRT, "box": "A", "section": "1"}),
]


def _call(api, method, path, body, headers):
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)(path, **kwargs)


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
@pytest.mark.parametrize("persona,code", [(None, 401), ("cadet", 403), ("nco", 403), ("snco", 403)])
def test_only_staff_get_in(api, method, path, body, persona, code):
    headers = api.as_(persona) if persona else {}
    assert _call(api, method, path, body, headers).status_code == code


@pytest.fixture
def shelf(db):
    box = StoresBox(label="A", shelf_level=1, shelf_position=0)
    db.add(box)
    db.flush()
    db.add_all([StoresSection(box_id=box.id, label="1", position=0),
                StoresSection(box_id=box.id, label="2", position=1)])
    db.commit()
    return box


def _book(api, persona="staff", **body):
    return api.post("/stores/stock/book-in", json={"box": "A", "section": "1", **body}, headers=api.as_(persona))


# ── looking up ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [SHIRT, "8405-99-845-6634"])
def test_a_form_number_is_looked_up(api, raw):
    res = api.get(f"/stores/nsns/{raw}", headers=STAFF)
    assert res.status_code == 200
    assert (res.json()["itemType"], res.json()["size"], res.json()["source"]) == \
        ("Working Blue Male", "110/40", "logs_form")


def test_an_unknown_number_is_404_with_the_number_in_words(api):
    res = api.get(f"/stores/nsns/{OLD_SHIRT}", headers=STAFF)
    assert res.status_code == 404
    assert res.json()["detail"] == "8410-99-869-6671 isn't a stock number we know yet"


@pytest.mark.parametrize("raw", ["1", "EACH", "84059984566349"])
def test_a_barcode_that_isnt_a_stock_number_is_400(api, raw):
    assert api.get(f"/stores/nsns/{raw}", headers=STAFF).status_code == 400


# ── teaching ──────────────────────────────────────────────────────────────────

def test_a_taught_number_is_known_from_then_on(api):
    res = api.put(f"/stores/nsns/{OLD_SHIRT}", json={"itemType": "Working Blue Female", "size": "95/36"},
                  headers=STAFF)
    assert res.status_code == 200
    assert (res.json()["source"], res.json()["addedBy"]) == ("added", "Sam Staff")
    assert api.get(f"/stores/nsns/{OLD_SHIRT}", headers=STAFF).json()["size"] == "95/36"
    listed = api.get("/stores/nsns", headers=STAFF).json()["added"]
    assert [(n["formatted"], n["itemType"]) for n in listed] == [("8410-99-869-6671", "Working Blue Female")]


def test_a_form_number_cant_be_taught_as_something_else(api):
    res = api.put(f"/stores/nsns/{SHIRT}", json={"itemType": "Beret", "size": "56"}, headers=STAFF)
    assert res.status_code == 409
    assert "Shirt Long Sleeve Mid Blue Male Size 110/40" in res.json()["detail"]


@pytest.mark.parametrize("body,code", [
    ({"itemType": "Cravat", "size": "M"}, 400),
    ({"itemType": "Beret", "size": "99"}, 400),
    ({"itemType": "Beret"}, 400),
    ({"size": "56"}, 422),
    ({"itemType": "", "size": "56"}, 422),
])
def test_teaching_rejects_what_the_catalogue_doesnt_have(api, db, body, code):
    assert api.put(f"/stores/nsns/{OLD_SHIRT}", json=body, headers=STAFF).status_code == code
    assert db.query(StoresNsn).count() == 0


def test_a_taught_number_can_be_forgotten(api, db):
    api.put(f"/stores/nsns/{OLD_SHIRT}", json={"itemType": "Beret", "size": "56"}, headers=STAFF)
    assert api.delete(f"/stores/nsns/{OLD_SHIRT}", headers=STAFF).status_code == 204
    assert api.get(f"/stores/nsns/{OLD_SHIRT}", headers=STAFF).status_code == 404
    assert api.delete(f"/stores/nsns/{OLD_SHIRT}", headers=STAFF).status_code == 404


def test_a_form_number_cant_be_forgotten(api):
    # It was never stored, so there's nothing to delete; the form still knows it.
    assert api.delete(f"/stores/nsns/{SHIRT}", headers=STAFF).status_code == 404
    assert api.get(f"/stores/nsns/{SHIRT}", headers=STAFF).status_code == 200


def test_the_list_says_when_label_photos_cant_be_read(api, monkeypatch):
    monkeypatch.setattr(label_reader, "GEMINI_API_KEY", None)
    assert api.get("/stores/nsns", headers=STAFF).json() == {"labelReading": False, "added": []}
    monkeypatch.setattr(label_reader, "GEMINI_API_KEY", "k")
    assert api.get("/stores/nsns", headers=STAFF).json()["labelReading"] is True


# ── booking in ────────────────────────────────────────────────────────────────

def test_a_scan_starts_a_new_line_on_the_shelf(api, db, shelf):
    res = _book(api, nsn="8405-99-845-6634")
    assert res.status_code == 200
    item = res.json()["item"]
    assert (item["itemType"], item["size"], item["quantity"], item["box"], item["section"]) == \
        ("Working Blue Male", "110/40", 1, "A", "1")
    assert db.query(StoresItem).one().gender == "male"
    change = db.query(StoresStockChange).one()
    assert (change.action, change.change, change.quantity_after, change.changed_by, change.note) == \
        ("added", 1, 1, "Sam Staff", "Booked in by scanning 8405-99-845-6634")


def test_another_scan_tops_the_same_line_up(api, db, shelf):
    _book(api, nsn=SHIRT)
    res = _book(api, nsn=SHIRT)
    assert res.json()["item"]["quantity"] == 2
    assert db.query(StoresItem).count() == 1
    assert [c.action for c in db.query(StoresStockChange).order_by(StoresStockChange.id)] == ["added", "topped_up"]


def test_the_same_item_in_another_section_is_its_own_line(api, db, shelf):
    _book(api, nsn=SHIRT)
    _book(api, nsn=SHIRT, section="2")
    assert sorted((i.section.label, i.quantity) for i in db.query(StoresItem)) == [("1", 1), ("2", 1)]


def test_a_quantity_books_a_box_of_them_in_at_once(api, shelf):
    assert _book(api, nsn=SHIRT, quantity=12).json()["item"]["quantity"] == 12


def test_booking_in_joins_a_line_the_stock_page_made(api, db, shelf):
    api.post("/stores/stock", json={"itemType": "Working Blue Male", "size": "110/40", "box": "A",
                                    "section": "1", "quantity": 3}, headers=STAFF)
    assert _book(api, nsn=SHIRT).json()["item"]["quantity"] == 4


def test_an_unsized_item_lands_on_the_n_a_line(api, db, shelf):
    tie = next(k for k, d in nsn.form_lines().items() if "Necktie" in d)
    item = _book(api, nsn=tie).json()["item"]
    assert (item["itemType"], item["size"]) == ("Tie", "N/A")


def test_a_label_that_wouldnt_read_is_booked_in_by_choosing_the_item(api, db, shelf):
    item = _book(api, itemType="Beret", size="56").json()["item"]
    assert (item["itemType"], item["size"]) == ("Beret", "56")
    assert db.query(StoresStockChange).one().note == "Booked in"


def test_a_taught_number_books_in(api, shelf):
    api.put(f"/stores/nsns/{OLD_SHIRT}", json={"itemType": "Working Blue Female", "size": "95/36"},
            headers=STAFF)
    assert _book(api, nsn=OLD_SHIRT).json()["lookup"]["source"] == "added"


def test_undoing_a_booking_takes_it_back_off(api, db, shelf):
    _book(api, nsn=SHIRT, quantity=2)
    res = _book(api, nsn=SHIRT, quantity=-1)
    assert res.json()["item"]["quantity"] == 1
    last = db.query(StoresStockChange).order_by(StoresStockChange.id.desc()).first()
    assert (last.action, last.change, last.note) == ("counted", -1, "Booking in undone")


def test_an_undo_never_takes_a_line_below_zero(api, db, shelf):
    _book(api, nsn=SHIRT)
    assert _book(api, nsn=SHIRT, quantity=-2).status_code == 409
    # Someone handed the shirt out in between: the undo can't take a second one.
    _book(api, nsn=SHIRT, quantity=-1)
    assert _book(api, nsn=SHIRT, quantity=-1).status_code == 409
    assert db.query(StoresItem).one().quantity == 0


def test_undoing_where_nothing_was_booked_is_409(api, db, shelf):
    assert _book(api, nsn=SHIRT, quantity=-1).status_code == 409
    assert db.query(StoresItem).count() == 0


@pytest.mark.parametrize("body,code,detail", [
    ({"nsn": OLD_SHIRT}, 404, "isn't a stock number we know yet"),
    ({"nsn": "EACH"}, 400, "13 digits"),
    ({}, 400, "Scan a stock number, or choose an item"),
    ({"itemType": "Cravat", "size": "M"}, 400, "choose an item"),
    ({"itemType": "Beret"}, 400, "Choose a size"),
    ({"nsn": SHIRT, "box": "Z"}, 404, "Box not found"),
    ({"nsn": SHIRT, "section": "9"}, 404, "Section not found"),
])
def test_a_booking_that_cant_go_ahead_changes_nothing(api, db, shelf, body, code, detail):
    res = _book(api, **body)
    assert res.status_code == code
    assert detail in res.json()["detail"]
    assert db.query(StoresItem).count() == 0 and db.query(StoresStockChange).count() == 0


def test_a_form_line_the_stores_dont_keep_is_refused_by_name(api, db, shelf):
    gloves = next(k for k, d in nsn.form_lines().items()
                  if d.startswith("Gloves Men"))
    res = _book(api, nsn=gloves)
    assert res.status_code == 409
    assert "Gloves Men" in res.json()["detail"] and "stores don't keep" in res.json()["detail"]


@pytest.mark.parametrize("quantity", [0, 101, -101, "two"])
def test_a_quantity_out_of_range_is_422(api, db, shelf, quantity):
    assert _book(api, nsn=SHIRT, quantity=quantity).status_code == 422
    assert db.query(StoresItem).count() == 0


def test_the_box_is_found_whatever_its_case(api, shelf):
    assert _book(api, nsn=SHIRT, box=" a ").status_code == 200


# ── reading a label photo ─────────────────────────────────────────────────────

class FakeGemini:
    def __init__(self):
        self.reply = {"nsn": "8405-99-845-6634", "size": "110/40(16)", "description": "SHIRT, MAN'S"}
        self.fail = False
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        if self.fail:
            return httpx.Response(500, text="down")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(self.reply)}]}}]})


@pytest.fixture
def gemini(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(label_reader, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", fake)
    return fake


def _photo(api, data=b"\xff\xd8jpeg", content_type="image/jpeg", persona="staff"):
    return api.post("/stores/nsns/read-label", headers=api.as_(persona),
                    files={"photo": ("label.jpg", data, content_type)})


def test_a_photo_of_a_known_label_comes_back_matched(api, gemini):
    res = _photo(api)
    assert res.status_code == 200
    body = res.json()
    assert body["read"] == {"nsn": SHIRT, "size": "110/40(16)", "description": "SHIRT, MAN'S"}
    assert (body["match"]["itemType"], body["match"]["size"]) == ("Working Blue Male", "110/40")
    assert body["model"] == label_reader.GEMINI_MODELS[0]


def test_a_photo_of_an_unknown_label_comes_back_with_what_was_read(api, gemini):
    # The page uses the size it read to fill in the "what is this?" form.
    gemini.reply = {"nsn": "8410-99-869-6671", "size": "35/96 Collar", "description": None}
    body = _photo(api).json()
    assert body["read"]["nsn"] == OLD_SHIRT and body["read"]["size"] == "35/96 Collar"
    assert body["match"] is None


def test_a_misread_number_is_not_matched_to_anything(api, gemini):
    # One digit short: never "close enough" to some other line.
    gemini.reply = {"nsn": "8405-99-845-663", "size": None, "description": None}
    body = _photo(api).json()
    assert body["read"]["nsn"] is None and body["match"] is None


def test_a_photo_that_cant_be_read_says_what_to_do_instead(api, gemini):
    gemini.fail = True
    res = _photo(api)
    assert res.status_code == 502
    assert res.json()["detail"] == "Couldn't read the label. Type the stock number, or choose the item"


def test_without_a_key_the_photo_is_refused_cleanly(api, monkeypatch):
    monkeypatch.setattr(label_reader, "GEMINI_API_KEY", None)
    assert _photo(api).status_code == 502


@pytest.mark.parametrize("data,content_type,code", [
    (b"%PDF", "application/pdf", 415),
    (b"", "image/jpeg", 400),
    (b"x" * (10 * 1024 * 1024 + 1), "image/jpeg", 413),
])
def test_a_bad_upload_is_refused_without_calling_gemini(api, gemini, data, content_type, code):
    assert _photo(api, data=data, content_type=content_type).status_code == code
    assert gemini.calls == 0


def test_a_heic_photo_straight_off_an_iphone_is_accepted(api, gemini):
    assert _photo(api, content_type="image/heic").status_code == 200


def test_photo_reads_are_rate_limited(api, gemini):
    codes = [_photo(api).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
