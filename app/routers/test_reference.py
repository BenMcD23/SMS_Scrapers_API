from core.catalogue import (
    BADGE_CATEGORIES,
    NO_SIZE_ITEMS,
    SIZING_FIELDS,
    UNIFORM_ITEM_TYPES,
    UNIFORM_SIZES,
)


def test_reference_requires_a_token(api):
    assert api.get("/reference").status_code == 401


def test_every_signed_in_role_can_read_the_reference(api):
    # The cadet portal reads it too, so it is not staff-only.
    for persona in ("cadet", "nco", "snco", "staff"):
        res = api.get("/reference", headers=api.as_(persona))
        assert res.status_code == 200, persona
        assert res.json()["uniform"]["itemTypes"] == list(UNIFORM_ITEM_TYPES)


def test_reference_returns_the_catalogue_briefly_cached(api):
    res = api.get("/reference", headers=api.as_("cadet"))
    body = res.json()
    assert body["badges"]["categories"][0]["id"] == "core"
    # Staff can edit it now, so it may only be cached for a minute, not an hour.
    assert "max-age=60" in res.headers["cache-control"]


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
