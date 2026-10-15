"""Order stats: what is ordered most, in which sizes, and how badge orders split."""

from datetime import datetime, timedelta

import pytest

from database.models import (
    BadgeOrder,
    BadgeOrderItem,
    Cadet,
    StoresOrder,
    StoresOrderItem,
)

UNIFORM = "/stores/orders/stats"
BADGES = "/stores/badges/orders/stats"
ENDPOINTS = [UNIFORM, BADGES]


def _item(order_id, item_type, size="", **kw):
    return StoresOrderItem(order_id=order_id, item_type=item_type, size=size, **kw)


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ENDPOINTS)
def test_order_stats_need_a_token(api, path):
    assert api.get(path).status_code == 401


@pytest.mark.parametrize("persona", ["cadet", "cadet2", "nco", "snco"])
@pytest.mark.parametrize("path", ENDPOINTS)
def test_only_staff_can_read_order_stats(api, path, persona):
    assert api.get(path, headers=api.as_(persona)).status_code == 403


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
@pytest.mark.parametrize("path", ENDPOINTS)
def test_staff_can_read_order_stats(api, path, persona):
    assert api.get(path, headers=api.as_(persona)).status_code == 200


@pytest.mark.parametrize("query", ["days=0", "days=-1", "days=abc", "days=99999"])
@pytest.mark.parametrize("path", ENDPOINTS)
def test_a_bad_window_is_rejected(api, path, query):
    assert api.get(f"{path}?{query}", headers=api.as_("staff")).status_code == 422


# ── uniform ───────────────────────────────────────────────────────────────────

def test_no_orders_gives_empty_stats(api):
    body = api.get(UNIFORM, headers=api.as_("staff")).json()
    assert body["total_orders"] == 0 and body["items"] == [] and body["monthly"] == []


def test_uniform_items_are_ranked_with_common_sizes(api, db):
    now = datetime.now()
    db.add_all([
        StoresOrder(id=1, created_at=now, completed=True),
        StoresOrder(id=2, created_at=now, kitting=True),
        StoresOrder(id=3, created_at=now),
    ])
    db.flush()
    db.add_all([
        _item(1, "Beret", "58", given_at=now),
        _item(2, "Beret", "58"),
        _item(3, "Beret", "56"),
        _item(1, "Tie"),
        _item(2, "Tie", "  "),  # blank sizes are the same as unsized
        _item(3, "Wedgewood Shirt", "M"),
    ])
    db.commit()

    body = api.get(UNIFORM, headers=api.as_("staff")).json()
    assert body["total_orders"] == 3 and body["total_items"] == 6
    assert body["completed_orders"] == 1 and body["kitting_orders"] == 1
    assert [i["item_type"] for i in body["items"]] == ["Beret", "Tie", "Wedgewood Shirt"]
    beret = body["items"][0]
    assert beret["count"] == 3 and beret["orders"] == 3 and beret["given"] == 1
    assert beret["top_size"] == "58"
    assert beret["sizes"] == [{"label": "58", "count": 2}, {"label": "56", "count": 1}]
    tie = body["items"][1]
    # Unsized items are counted but never reported as the "common size".
    assert tie["sizes"] == [{"label": "Not sized yet", "count": 2}] and tie["top_size"] is None


def test_uniform_window_excludes_older_orders(api, db):
    db.add_all([
        StoresOrder(id=1, created_at=datetime.now() - timedelta(days=400)),
        StoresOrder(id=2, created_at=datetime.now() - timedelta(days=5)),
    ])
    db.flush()
    db.add_all([_item(1, "Beret", "55"), _item(2, "Tie")])
    db.commit()
    everything = api.get(UNIFORM, headers=api.as_("staff")).json()
    recent = api.get(f"{UNIFORM}?days=30", headers=api.as_("staff")).json()
    assert everything["total_orders"] == 2  # orders are kept indefinitely
    assert recent["total_orders"] == 1 and [i["item_type"] for i in recent["items"]] == ["Tie"]


def test_uniform_orders_without_items_are_counted_not_crashed(api, db):
    db.add(StoresOrder(id=1, created_at=datetime.now()))
    db.commit()
    body = api.get(UNIFORM, headers=api.as_("staff")).json()
    assert body["total_orders"] == 1 and body["total_items"] == 0 and body["items"] == []


def test_monthly_fills_quiet_months(api, db):
    db.add_all([
        StoresOrder(id=1, created_at=datetime(2026, 1, 10)),
        StoresOrder(id=2, created_at=datetime(2026, 1, 20)),
        StoresOrder(id=3, created_at=datetime(2026, 4, 2)),
    ])
    db.commit()
    monthly = api.get(UNIFORM, headers=api.as_("staff")).json()["monthly"]
    assert monthly == [
        {"month": "2026-01", "orders": 2},
        {"month": "2026-02", "orders": 0},
        {"month": "2026-03", "orders": 0},
        {"month": "2026-04", "orders": 1},
    ]


def test_monthly_crosses_a_year_boundary(api, db):
    db.add_all([
        StoresOrder(id=1, created_at=datetime(2025, 12, 31)),
        StoresOrder(id=2, created_at=datetime(2026, 1, 1)),
    ])
    db.commit()
    months = [m["month"] for m in api.get(UNIFORM, headers=api.as_("staff")).json()["monthly"]]
    assert months == ["2025-12", "2026-01"]


def test_non_ascii_item_and_size_names_survive(api, db):
    db.add(StoresOrder(id=1, created_at=datetime.now()))
    db.flush()
    db.add(_item(1, "Béret", "½"))
    db.commit()
    item = api.get(UNIFORM, headers=api.as_("staff")).json()["items"][0]
    assert item["item_type"] == "Béret" and item["top_size"] == "½"


# ── badges ────────────────────────────────────────────────────────────────────

def _badge_order(db, oid, created=None, completed=False):
    if not db.get(Cadet, 1):
        db.add(Cadet(cin=1, first_name="Zoë", last_name="Ó Briain"))
    db.add(BadgeOrder(id=oid, cadet_id=1, created_at=created or datetime.now(), completed=completed))
    db.flush()


def test_no_badge_orders_gives_empty_stats(api):
    body = api.get(BADGES, headers=api.as_("staff")).json()
    assert body["total_orders"] == 0 and body["badges"] == [] and body["gained_where"] == []


def test_badges_are_ranked_with_replacements_and_where_gained(api, db):
    _badge_order(db, 1, completed=True)
    _badge_order(db, 2)
    db.add_all([
        BadgeOrderItem(order_id=1, badge_name="Blue Badge", gained_where="camp", given_at=datetime.now()),
        BadgeOrderItem(order_id=2, badge_name="Blue Badge", replacement=True, gained_where="camp"),
        BadgeOrderItem(order_id=2, badge_name="Cyber Badge", gained_where="on_sqn"),
        BadgeOrderItem(order_id=2, badge_name="Rare Badge"),  # no "gained where" recorded
    ])
    db.commit()

    body = api.get(BADGES, headers=api.as_("staff")).json()
    assert body["total_orders"] == 2 and body["completed_orders"] == 1
    assert body["total_items"] == 4 and body["replacements"] == 1
    assert body["badges"][0] == {
        "badge_name": "Blue Badge", "count": 2, "orders": 2, "replacements": 1, "given": 1,
    }
    assert [b["badge_name"] for b in body["badges"][1:]] == ["Cyber Badge", "Rare Badge"]
    assert body["gained_where"] == [{"label": "camp", "count": 2}, {"label": "on_sqn", "count": 1}]


def test_badge_window_excludes_older_orders(api, db):
    _badge_order(db, 1, created=datetime.now() - timedelta(days=400))
    _badge_order(db, 2)
    db.add_all([
        BadgeOrderItem(order_id=1, badge_name="Old Badge"),
        BadgeOrderItem(order_id=2, badge_name="New Badge"),
    ])
    db.commit()
    body = api.get(f"{BADGES}?days=30", headers=api.as_("staff")).json()
    assert [b["badge_name"] for b in body["badges"]] == ["New Badge"]
