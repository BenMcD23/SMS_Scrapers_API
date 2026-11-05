"""Uniform issuance history: every give kept, the "last issued" record derived from it.

A cadet's (or adult's) Uniform tab shows one "last issued" row per category
(StoresItemIssuance). It used to be overwritten on every give, so a mistake
lost the record before it and "Clear" lost everything. Now each give is a
StoresIssuanceHistory row that is never edited or deleted, only marked undone,
and the summary row is always the newest entry that hasn't been undone.
Undoing therefore steps back one give, and the undone one stays visible.

Records from before the history existed are copied in by the migration; for
any that slip past it (rows written straight to the table), `_backfill` adds
the missing entry on first touch so they can be undone like the rest.

The caller commits.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from database.models import StoresIssuanceHistory, StoresItemIssuance


def _subject_filter(query, model, cadet_cin: int | None, user_id: int | None):
    return (query.filter(model.cadet_id == cadet_cin) if cadet_cin is not None
            else query.filter(model.user_id == user_id))


def current_record(db: Session, category: str, *, cadet_cin=None, user_id=None) -> StoresItemIssuance | None:
    query = db.query(StoresItemIssuance).filter(StoresItemIssuance.item_category == category)
    return _subject_filter(query, StoresItemIssuance, cadet_cin, user_id).first()


def _entries(db: Session, category: str, cadet_cin, user_id):
    query = db.query(StoresIssuanceHistory).filter(StoresIssuanceHistory.item_category == category)
    return _subject_filter(query, StoresIssuanceHistory, cadet_cin, user_id)


def latest_active(db: Session, category: str, *, cadet_cin=None, user_id=None) -> StoresIssuanceHistory | None:
    """The give the "last issued" record should show. Newest *recorded* wins, not
    newest date: correcting a mistyped date to an earlier one must take effect."""
    return (_entries(db, category, cadet_cin, user_id)
            .filter(StoresIssuanceHistory.undone_at.is_(None))
            .order_by(StoresIssuanceHistory.id.desc())
            .first())


def _backfill(db: Session, category: str, cadet_cin, user_id) -> None:
    """Give a "last issued" record with no history an entry of its own, so it
    isn't silently dropped the first time the history is consulted."""
    current = current_record(db, category, cadet_cin=cadet_cin, user_id=user_id)
    if current and not _entries(db, category, cadet_cin, user_id).first():
        db.add(StoresIssuanceHistory(
            cadet_id=cadet_cin, user_id=user_id, item_category=category,
            given_at=current.last_given, size_given=current.size_given, recorded_at=current.last_given,
        ))
        db.flush()


def sync_current(db: Session, category: str, *, cadet_cin=None, user_id=None) -> StoresItemIssuance | None:
    """Point the "last issued" record at the newest give still standing, or
    remove it when every give has been undone."""
    entry = latest_active(db, category, cadet_cin=cadet_cin, user_id=user_id)
    current = current_record(db, category, cadet_cin=cadet_cin, user_id=user_id)
    if entry is None:
        if current:
            db.delete(current)
        return None
    if current is None:
        current = StoresItemIssuance(cadet_id=cadet_cin, user_id=user_id, item_category=category)
        db.add(current)
    current.last_given = entry.given_at
    current.size_given = entry.size_given
    db.flush()
    return current


def record_give(db: Session, category: str, given_at: datetime, size: str | None, by: str | None, *,
                cadet_cin=None, user_id=None, order_item_id: int | None = None) -> StoresItemIssuance:
    """Add a give to the history and make it the "last issued" record."""
    _backfill(db, category, cadet_cin, user_id)
    db.add(StoresIssuanceHistory(
        cadet_id=cadet_cin, user_id=user_id, item_category=category, given_at=given_at, size_given=size,
        recorded_at=datetime.now(), recorded_by=by, order_item_id=order_item_id,
    ))
    db.flush()
    return sync_current(db, category, cadet_cin=cadet_cin, user_id=user_id)


def undo_entry(db: Session, entry: StoresIssuanceHistory, by: str | None) -> StoresItemIssuance | None:
    """Mark one give undone and step the "last issued" record back past it."""
    entry.undone_at = datetime.now()
    entry.undone_by = by
    db.flush()
    return sync_current(db, entry.item_category, cadet_cin=entry.cadet_id, user_id=entry.user_id)


def undo_latest(db: Session, category: str, by: str | None, *,
                cadet_cin=None, user_id=None) -> StoresIssuanceHistory | None:
    """Undo the give the "last issued" record is showing. Returns the entry
    undone, or None when nothing is left to undo."""
    _backfill(db, category, cadet_cin, user_id)
    entry = latest_active(db, category, cadet_cin=cadet_cin, user_id=user_id)
    if entry:
        undo_entry(db, entry, by)
    return entry


def entry_for_order_item(db: Session, order_item_id: int) -> StoresIssuanceHistory | None:
    """The standing give made against an order item, newest first."""
    return (db.query(StoresIssuanceHistory)
            .filter(StoresIssuanceHistory.order_item_id == order_item_id,
                    StoresIssuanceHistory.undone_at.is_(None))
            .order_by(StoresIssuanceHistory.id.desc())
            .first())


def history(db: Session, *, cadet_cin=None, user_id=None) -> list[StoresIssuanceHistory]:
    """Everything recorded for one person, newest first."""
    query = _subject_filter(db.query(StoresIssuanceHistory), StoresIssuanceHistory, cadet_cin, user_id)
    return query.order_by(StoresIssuanceHistory.id.desc()).all()


def to_dict(entry: StoresIssuanceHistory, *, current: bool = False) -> dict:
    """`current` marks the give the "last issued" record is showing, which is
    the one an undo would take back."""
    return {
        "id":           entry.id,
        "itemCategory": entry.item_category,
        "lastGiven":    entry.given_at.isoformat(),
        "sizeGiven":    entry.size_given,
        "recordedAt":   entry.recorded_at.isoformat(),
        "recordedBy":   entry.recorded_by,
        "orderItemId":  str(entry.order_item_id) if entry.order_item_id is not None else None,
        "undoneAt":     entry.undone_at.isoformat() if entry.undone_at else None,
        "undoneBy":     entry.undone_by,
        "current":      current,
    }


def to_dicts(entries: list[StoresIssuanceHistory]) -> list[dict]:
    """Newest-first entries, with the newest standing one per person and
    category flagged current."""
    seen: set[tuple] = set()
    out = []
    for entry in entries:
        key = (entry.cadet_id, entry.user_id, entry.item_category)
        is_current = entry.undone_at is None and key not in seen
        if is_current:
            seen.add(key)
        out.append(to_dict(entry, current=is_current))
    return out
