from datetime import datetime

import pytest
from fastapi import BackgroundTasks, HTTPException

from core import given_events
from core.order_items import get_order_item, greeting_name, mark_ready, undo_given
from database.models import BadgeOrder, BadgeOrderItem, Cadet, StoresOrder, StoresOrderItem


@pytest.fixture
def orders(db):
    db.add_all([
        Cadet(cin=1, first_name="Amy", last_name="Able", rank="Cpl", email="amy@x"),
        Cadet(cin=2, first_name="Zoë", last_name="Ünal", rank=None, email=None),
    ])
    a = StoresOrder(cadet_id=1, created_at=datetime(2026, 10, 1))
    b = StoresOrder(cadet_id=2, created_at=datetime(2026, 10, 1))
    db.add_all([a, b])
    db.flush()
    db.add_all([
        StoresOrderItem(order_id=a.id, item_type="Beret", size="56"),
        StoresOrderItem(order_id=b.id, item_type="Tie"),
    ])
    db.commit()
    return a, b


def test_an_item_is_found_through_its_order(db, orders):
    a, _ = orders
    order, item = get_order_item(db, StoresOrder, StoresOrderItem, a.id, a.order_items[0].id)
    assert (order.id, item.item_type) == (a.id, "Beret")


def test_another_orders_item_is_not_found(db, orders):
    # Not someone else's item through this order's URL.
    a, b = orders
    with pytest.raises(HTTPException) as e:
        get_order_item(db, StoresOrder, StoresOrderItem, a.id, b.order_items[0].id)
    assert (e.value.status_code, e.value.detail) == (404, "Item not found")


def test_a_missing_order_says_so(db, orders):
    with pytest.raises(HTTPException) as e:
        get_order_item(db, BadgeOrder, BadgeOrderItem, 999, 1)
    assert (e.value.status_code, e.value.detail) == (404, "Order not found")


def test_the_greeting_uses_the_rank_when_there_is_one(orders):
    a, b = orders
    assert greeting_name(a.cadet) == "Cpl Able"
    assert greeting_name(b.cadet) == "Ünal"


def test_ready_emails_the_cadet_after_the_response(db, orders, outbox):
    a, _ = orders
    background = BackgroundTasks()
    assert mark_ready(db, a, a.order_items[0], background, subject="Ready", item_name="Beret", item_kind="uniform",
                      size="56") is True
    assert a.order_items[0].ready_to_collect is not None
    # Queued, not sent: the request isn't kept waiting on Gmail.
    assert outbox == [] and len(background.tasks) == 1

    import asyncio
    asyncio.run(background())
    assert [(m["to"], m["subject"]) for m in outbox] == [("amy@x", "Ready")]
    assert "Cpl Able" in outbox[0]["html"] and "56" in outbox[0]["html"]


def test_ready_with_no_email_on_file_still_stamps_the_item(db, orders, outbox):
    _, b = orders
    background = BackgroundTasks()
    assert mark_ready(db, b, b.order_items[0], background, subject="Ready", item_name="Tie", item_kind="uniform") is False
    assert b.order_items[0].ready_to_collect is not None and background.tasks == []


def test_undo_keeps_the_give_in_the_history_and_returns_it(orders):
    a, _ = orders
    item = a.order_items[0]
    item.given_at, item.given_by = datetime(2026, 10, 6), "Sgt Smith"
    item.given_events = given_events.dump([given_events.new_event(given_events.GIVEN, "Sgt Smith", category="Beret")])

    given = undo_given(a, item, "Sgt Jones", noun="an item")

    assert given["category"] == "Beret"
    assert (item.given_at, item.given_by) == (None, None)
    assert [e["action"] for e in given_events.events_list(item.given_events)] == ["given", "undone"]


def test_an_item_given_before_the_history_existed_can_still_be_undone(orders):
    a, _ = orders
    item = a.order_items[0]
    item.given_at = datetime(2025, 1, 1)
    assert undo_given(a, item, "Sgt Jones", noun="an item") is None
    assert item.given_at is None


@pytest.mark.parametrize("noun, completed, given, detail", [
    ("an item", True, True, "Reopen the order before undoing an item"),
    ("a badge", True, True, "Reopen the order before undoing a badge"),
    ("an item", False, False, "This item hasn't been marked as given"),
    ("a badge", False, False, "This badge hasn't been marked as given"),
])
def test_undo_refusals(orders, noun, completed, given, detail):
    a, _ = orders
    a.completed = completed
    a.order_items[0].given_at = datetime(2026, 10, 6) if given else None
    with pytest.raises(HTTPException) as e:
        undo_given(a, a.order_items[0], "x", noun=noun)
    assert (e.value.status_code, e.value.detail) == (409, detail)
