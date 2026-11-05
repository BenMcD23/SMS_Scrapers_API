"""Recently deleted stores records, and putting them back.

Deleting a uniform or badge order, a Logs Form entry or a badge order-list
entry still removes the row, as it always has: nothing else that reads those
tables has to learn to skip deleted ones. Just before it goes, `archive` keeps
a copy of every column (and an order's items) in Stores_Deleted_Records, and
`restore` builds it again from that copy.

A restored row gets a new id. Links that pointed at the old one were already
cleared by the delete (a Logs Form entry's order item, an issuance history
entry's order item), so they stay cleared; the restored order's own items keep
their given / stock / notes history, which lives on the item.

The caller commits.
"""

import json
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import DateTime
from sqlalchemy.orm import Session

from database.models import (
    BadgeOrder,
    BadgeOrderItem,
    BadgeOrderListEntry,
    Cadet,
    LogsForm,
    LogsFormEntry,
    StoresDeletedRecord,
    StoresOrder,
    StoresOrderItem,
    User,
)

UNIFORM_ORDER = "uniform_order"
BADGE_ORDER = "badge_order"
LOGS_FORM_ENTRY = "logs_form_entry"
BADGE_ORDER_LIST_ENTRY = "badge_order_list_entry"
KINDS = (UNIFORM_ORDER, BADGE_ORDER, LOGS_FORM_ENTRY, BADGE_ORDER_LIST_ENTRY)


def _columns(row) -> dict:
    out = {}
    for column in row.__table__.columns:
        value = getattr(row, column.key)
        out[column.key] = value.isoformat() if isinstance(value, (datetime, date)) else value
    return out


def _build(model, data: dict, **overrides):
    """A new row of `model` from archived columns, minus its old id."""
    values = {}
    for column in model.__table__.columns:
        if column.key == "id" or column.key not in data:
            continue
        value = data[column.key]
        if value is not None and isinstance(column.type, DateTime):
            value = datetime.fromisoformat(value)
        values[column.key] = value
    values.update(overrides)
    return model(**values)


def _subject_name(cadet: Cadet | None, user: User | None) -> str:
    if cadet:
        return f"{cadet.first_name} {cadet.last_name}"
    if user:
        return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email
    return "someone no longer on the roster"


def _items_phrase(names: list[str]) -> str:
    if not names:
        return "no items"
    shown = ", ".join(names[:3])
    return shown + (f" and {len(names) - 3} more" if len(names) > 3 else "")


def archive(db: Session, row, by: str | None) -> StoresDeletedRecord:
    """Keep a copy of `row` before the caller deletes it."""
    if isinstance(row, StoresOrder):
        kind, items = UNIFORM_ORDER, sorted(row.order_items, key=lambda i: i.id)
        label = (f"Uniform order for {_subject_name(row.cadet, row.user)}: "
                 f"{_items_phrase([i.item_type for i in items])}")
        data = {**_columns(row), "items": [_columns(i) for i in items]}
    elif isinstance(row, BadgeOrder):
        kind, items = BADGE_ORDER, sorted(row.order_items, key=lambda i: i.id)
        label = f"Badge order for {_subject_name(row.cadet, None)}: {_items_phrase([i.badge_name for i in items])}"
        data = {**_columns(row), "items": [_columns(i) for i in items]}
    elif isinstance(row, LogsFormEntry):
        kind = LOGS_FORM_ENTRY
        size = f" {row.size}" if row.size else ""
        label = f"Logs Form entry: {row.item_type}{size} for {row.cadet_name}"
        data = _columns(row)
    elif isinstance(row, BadgeOrderListEntry):
        kind = BADGE_ORDER_LIST_ENTRY
        label = f"Badge order list entry: {row.badge_name} for {row.cadet_name}"
        data = _columns(row)
    else:  # pragma: no cover - a programming error, not a request
        raise TypeError(f"can't archive {type(row).__name__}")
    record = StoresDeletedRecord(kind=kind, label=label, data=json.dumps(data), deleted_at=datetime.now(),
                                 deleted_by=by)
    db.add(record)
    return record


def _free_order_item(db: Session, model, order_item_id, item_model, what: str):
    """The order item an entry pointed at, if it's still there and not already
    on another list; 409 when another entry has taken it since."""
    if order_item_id is None or not db.get(item_model, order_item_id):
        return None
    if db.query(model).filter(model.order_item_id == order_item_id).first():
        raise HTTPException(status_code=409, detail=f"That item is already back on the {what}")
    return order_item_id


def restore(db: Session, record: StoresDeletedRecord, by: str | None) -> int:
    """Put a deleted record back. Returns the new row's id."""
    if record.restored_at:
        raise HTTPException(status_code=409, detail="This has already been restored")
    data = json.loads(record.data)

    if record.kind in (UNIFORM_ORDER, BADGE_ORDER):
        order_model, item_model = ((StoresOrder, StoresOrderItem) if record.kind == UNIFORM_ORDER
                                   else (BadgeOrder, BadgeOrderItem))
        cadet_id, user_id = data.get("cadet_id"), data.get("user_id")
        if cadet_id is not None and not db.get(Cadet, cadet_id):
            raise HTTPException(status_code=409, detail="That cadet is no longer on the roster")
        if user_id is not None and not db.get(User, user_id):
            raise HTTPException(status_code=409, detail="That person no longer has an account")
        if cadet_id is None and user_id is None:
            raise HTTPException(status_code=409, detail="Whoever this order was for is no longer on the roster")
        order = _build(order_model, data)
        db.add(order)
        db.flush()
        for item in data.get("items", []):
            db.add(_build(item_model, item, order_id=order.id))
        new_id = order.id
    elif record.kind == LOGS_FORM_ENTRY:
        # Back on the form it came off if that's still open, else this week's open one.
        form = db.get(LogsForm, data.get("form_id"))
        if not form or form.ordered_at is not None:
            form = db.query(LogsForm).filter(LogsForm.ordered_at.is_(None)).first()
        if not form:
            form = LogsForm(created_at=datetime.now())
            db.add(form)
            db.flush()
        order_item_id = _free_order_item(db, LogsFormEntry, data.get("order_item_id"), StoresOrderItem,
                                         "Logs Form")
        entry = _build(LogsFormEntry, data, form_id=form.id, order_item_id=order_item_id)
        db.add(entry)
        db.flush()
        new_id = entry.id
    elif record.kind == BADGE_ORDER_LIST_ENTRY:
        order_item_id = _free_order_item(db, BadgeOrderListEntry, data.get("order_item_id"), BadgeOrderItem,
                                         "badge order list")
        entry = _build(BadgeOrderListEntry, data, order_item_id=order_item_id)
        db.add(entry)
        db.flush()
        new_id = entry.id
    else:
        raise HTTPException(status_code=400, detail="This kind of record can't be restored")

    record.restored_at = datetime.now()
    record.restored_by = by
    record.restored_id = new_id
    return new_id


def to_dict(record: StoresDeletedRecord) -> dict:
    return {
        "id":         record.id,
        "kind":       record.kind,
        "label":      record.label,
        "deletedAt":  record.deleted_at.isoformat(),
        "deletedBy":  record.deleted_by,
        "restoredAt": record.restored_at.isoformat() if record.restored_at else None,
        "restoredBy": record.restored_by,
        "restoredId": record.restored_id,
    }
