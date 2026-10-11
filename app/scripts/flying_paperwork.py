"""Flying paperwork: which cadets on an AEF or gliding event have handed in
their TG 21, TG 23 and AV Med form.

The event scraper rebuilds All_Events from scratch every run, so the ticks
can't hang off those rows. Flying events are instead kept in their own table
keyed on Bader's event id and reconciled against each scrape here.
"""

from datetime import datetime

from sqlalchemy.orm import selectinload

from database.models import FlyingEvent, FlyingPaperwork
from scripts.event_scraper import parse_event_date

# Bader's category names for the events that need flying paperwork.
FLYING_CATEGORIES = ("RAFAC Air Experience Flying (AEF)", "RAFAC Gliding")
# The forms tracked per cadet, as FlyingPaperwork columns.
PAPERWORK_FIELDS = ("tg21", "tg23", "av_med")

_FLYING = {c.casefold() for c in FLYING_CATEGORIES}


def is_flying_category(category: str | None) -> bool:
    return bool(category) and category.casefold() in _FLYING


def has_ticks(row: FlyingPaperwork) -> bool:
    return any(getattr(row, field) for field in PAPERWORK_FIELDS)


def event_has_ticks(event: FlyingEvent) -> bool:
    return any(has_ticks(row) for row in event.cadets)


def _sync_cadets(event: FlyingEvent, cins: set[int]) -> None:
    """Add cadets who've joined, drop untouched ones who've left, and keep
    anyone with paperwork ticked off but flag them as no longer on it."""
    rows = {row.cadet_id: row for row in event.cadets}
    for cin in cins - rows.keys():
        event.cadets.append(FlyingPaperwork(cadet_id=cin, on_event=True))
    for cin, row in rows.items():
        if cin in cins:
            row.on_event = True
        elif has_ticks(row):
            row.on_event = False
        else:
            event.cadets.remove(row)


def sync_flying_events(db_session, events: list[dict], now: datetime | None = None) -> dict:
    """Reconcile the tracked flying events with one event scrape.

    ``events`` is every event the scrape saw: ``{"event_id", "title",
    "category", "date_from", "date_to", "cadets"}``, with ``category`` None
    when its detail page couldn't be read and ``cadets`` the matched CINs.

    - A flying event is added, or has its details and cadets refreshed.
    - A tracked event the scrape no longer finds (it's been and gone, or was
      cancelled) is deleted if nothing on it was ever ticked off; otherwise it
      stays, marked as off Bader, until staff close it.
    - An event whose category is unknown this run is left exactly as it is —
      a failed page fetch must never delete someone's ticks or cadets.

    Returns counts for the scraper log.
    """
    now = now or datetime.now()
    seen = {e["event_id"]: e for e in events if e.get("event_id")}
    counts = {"tracked": 0, "added": 0, "removed": 0, "kept": 0}
    # A scrape that came back empty says nothing about what's on Bader.
    if not seen:
        return counts

    existing = {
        fe.bader_event_id: fe
        for fe in db_session.query(FlyingEvent).options(selectinload(FlyingEvent.cadets)).all()
    }

    for event_id, e in seen.items():
        if not is_flying_category(e.get("category")):
            continue
        fe = existing.get(event_id)
        if fe is None:
            fe = FlyingEvent(bader_event_id=event_id, created_at=now)
            db_session.add(fe)
            counts["added"] += 1
        fe.title = e.get("title") or fe.title or "Untitled"
        fe.category = e["category"]
        fe.date_from = parse_event_date(e.get("date_from")) or fe.date_from
        fe.date_to = parse_event_date(e.get("date_to")) or fe.date_to
        fe.on_bader = True
        fe.last_seen_at = now
        _sync_cadets(fe, set(e.get("cadets") or ()))
        counts["tracked"] += 1

    for event_id, fe in existing.items():
        e = seen.get(event_id)
        if e is not None and (e.get("category") is None or is_flying_category(e["category"])):
            continue  # still on Bader, or we couldn't tell this run
        if event_has_ticks(fe):
            fe.on_bader = False
            counts["kept"] += 1
        else:
            db_session.delete(fe)
            counts["removed"] += 1

    db_session.commit()
    return counts
