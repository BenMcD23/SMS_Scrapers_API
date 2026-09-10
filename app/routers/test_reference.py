from fastapi.testclient import TestClient

from api import app
from core.catalogue import (
    BADGE_CATEGORIES,
    NO_SIZE_ITEMS,
    SIZING_FIELDS,
    UNIFORM_ITEM_TYPES,
    UNIFORM_SIZES,
)
from core.security import require_user

client = TestClient(app)


def test_reference_requires_a_token():
    assert client.get("/reference").status_code == 401


def test_reference_returns_the_catalogue(monkeypatch):
    app.dependency_overrides[require_user] = lambda: {"email": "cadet@317atc.co.uk"}
    try:
        res = client.get("/reference")
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    body = res.json()
    assert body["uniform"]["itemTypes"] == list(UNIFORM_ITEM_TYPES)
    assert body["badges"]["categories"][0]["id"] == "core"
    assert "max-age" in res.headers["cache-control"]


def test_every_sized_item_has_sizes_and_sizing_fields():
    for item in UNIFORM_ITEM_TYPES:
        if item in NO_SIZE_ITEMS:
            continue
        assert UNIFORM_SIZES[item], item
    for item in SIZING_FIELDS:
        assert item in UNIFORM_ITEM_TYPES


def test_badge_categories_have_either_items_or_levels():
    for cat in BADGE_CATEGORIES:
        assert ("items" in cat) != ("levels" in cat), cat["id"]
        if "levels" in cat:
            assert cat["prefix"]
