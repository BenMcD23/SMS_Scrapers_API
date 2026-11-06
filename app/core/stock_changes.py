"""The stock change log: every change to a uniform shelf or badge grid count.

Stock rows only hold the current count, so a wrong number used to be a
mystery. Each handler that changes a count calls `record` (the caller
commits), and Stores > History lists them.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from database.models import BadgeItem, StoresItem, StoresStockChange

# What happened, as the UI labels it (lib/stock-changes.ts mirrors these).
ADDED = "added"            # a new line on the shelf / grid
TOPPED_UP = "topped_up"    # more added to a line that was already there
COUNTED = "counted"        # the count edited by hand
MOVED = "moved"            # another box, section or cell (count unchanged)
RENAMED = "renamed"        # a different item type, size or badge name
MERGED = "merged"          # an edit made it match another line, which took its count
DELETED = "deleted"        # the line removed (on its own, or with its box, section, cell, row or column)
TAKEN = "taken"            # taken off for an order
RETURNED = "returned"      # put back from an order
ACTIONS = (ADDED, TOPPED_UP, COUNTED, MOVED, RENAMED, MERGED, DELETED, TAKEN, RETURNED)


def uniform_location(item: StoresItem) -> str | None:
    box = item.box.label if item.box else None
    section = item.section.label if item.section else None
    return " / ".join(p for p in (box, section) if p) or None


def badge_location(item: BadgeItem) -> str | None:
    cell = item.cell
    if not cell:
        return None
    return cell.label or f"Row {cell.row + 1}, column {cell.col + 1}"


def record(db: Session, kind: str, action: str, *, item_name: str, by: str | None, change: int = 0,
           quantity_after: int | None = None, stock_item_id: int | None = None, size: str | None = None,
           location: str | None = None, note: str | None = None, order_id: int | None = None) -> None:
    db.add(StoresStockChange(
        kind=kind, action=action, item_name=item_name, size=size or None, location=location,
        change=change, quantity_after=quantity_after, stock_item_id=stock_item_id, note=note,
        order_id=order_id, changed_at=datetime.now(), changed_by=by,
    ))


def record_uniform(db: Session, item: StoresItem, action: str, by: str | None, *, change: int = 0,
                   gone: bool = False, note: str | None = None, order_id: int | None = None) -> None:
    """Log a change to a uniform line as it stands now. `gone` for a line
    being deleted, so its count after reads as nothing rather than what it held."""
    db.flush()  # so a new line has its id and box / section loaded
    record(db, "uniform", action, item_name=item.item_type, size=item.size, by=by, change=change,
           quantity_after=None if gone else item.quantity, stock_item_id=item.id,
           location=uniform_location(item), note=note, order_id=order_id)


def record_badge(db: Session, item: BadgeItem, action: str, by: str | None, *, change: int = 0,
                 gone: bool = False, note: str | None = None, order_id: int | None = None) -> None:
    db.flush()
    record(db, "badge", action, item_name=item.name, by=by, change=change,
           quantity_after=None if gone else item.quantity, stock_item_id=item.id,
           location=badge_location(item), note=note, order_id=order_id)


def to_dict(row: StoresStockChange) -> dict:
    return {
        "id":            row.id,
        "kind":          row.kind,
        "stockItemId":   row.stock_item_id,
        "itemName":      row.item_name,
        "size":          row.size,
        "location":      row.location,
        "action":        row.action,
        "change":        row.change,
        "quantityAfter": row.quantity_after,
        "note":          row.note,
        "orderId":       row.order_id,
        "changedAt":     row.changed_at.isoformat(),
        "changedBy":     row.changed_by,
    }
