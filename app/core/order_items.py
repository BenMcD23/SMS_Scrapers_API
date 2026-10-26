"""What uniform and badge order items have in common, shared by
routers/stores.py and routers/badges.py, which each carried a copy.

Both kinds of order have items that are found by (order, item) id, marked
ready to collect (emailing the cadet), and given and un-given with a history
(`core.given_events`). The routers keep what differs: what an item is called,
how stock is taken off the shelf or the badge grid, and the uniform issuance
record a give updates.
"""

from datetime import datetime

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from core import given_events
from core.emailer import ready_to_collect_email_html, send_email


def get_order_item(db: Session, order_model, item_model, order_id: int, item_id: int):
    """The order and one of its items, or a 404 naming whichever is missing.
    An item id from another order is "not found", not someone else's item."""
    order = db.query(order_model).filter(order_model.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    item = db.query(item_model).filter(item_model.id == item_id, item_model.order_id == order_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return order, item


def greeting_name(cadet) -> str:
    """"Cpl Smith", or just "Smith" for a cadet with no rank on file."""
    return f"{cadet.rank} {cadet.last_name}" if cadet.rank else cadet.last_name


def mark_ready(db: Session, order, item, background: BackgroundTasks, *, subject: str, item_name: str,
               item_kind: str, size: str = "") -> bool:
    """Stamp the item ready to collect and email the cadet. The email is sent
    after the response, so the stores staff aren't kept waiting on Gmail.
    Returns whether there was anyone to email."""
    item.ready_to_collect = datetime.now()
    db.commit()
    cadet = order.cadet
    if not (cadet and cadet.email):
        return False
    background.add_task(
        send_email,
        to=cadet.email,
        subject=subject,
        html_body=ready_to_collect_email_html(
            cadet_name=greeting_name(cadet), item_name=item_name, item_kind=item_kind, size=size,
        ),
    )
    return True


def undo_given(order, item, by: str | None, *, noun: str) -> dict | None:
    """Take back a mark-as-given: the item goes back to waiting and the undo is
    added to its history after the give. Returns the give being undone (None
    for an item given before the history existed), so the uniform side can put
    the cadet's issuance record back. `noun` is "an item" or "a badge", for the
    refusals. The caller commits."""
    if order.completed:
        raise HTTPException(status_code=409, detail=f"Reopen the order before undoing {noun}")
    if not item.given_at:
        raise HTTPException(status_code=409, detail=f"This {noun.split(' ', 1)[1]} hasn't been marked as given")
    events = given_events.events_list(item.given_events)
    given = given_events.last_given(events)
    events.append(given_events.new_event(given_events.UNDONE, by))
    item.given_events = given_events.dump(events)
    item.given_at = None
    item.given_by = None
    return given
