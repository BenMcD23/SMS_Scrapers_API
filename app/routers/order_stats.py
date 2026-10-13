"""Stats over stores orders — what gets ordered most and in which sizes.

Read-only roll-ups of uniform and badge orders for the QM, so stock can be
bought to match demand. Orders are kept indefinitely, so "all time" is real
history; `days` narrows it to a recent window.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload

from core.db import get_db
from core.security import require_staff
from database.models import BadgeOrder, StoresOrder

router = APIRouter()

# Uniform items with no size recorded yet (still waiting to be fitted).
UNSIZED = "Not sized yet"
TOP_SIZES_LIMIT = 12


def _since(days: int | None) -> datetime | None:
    return datetime.now() - timedelta(days=days) if days else None


def _monthly(stamps: list[datetime]) -> list[dict]:
    """Orders per calendar month, oldest first, with empty months filled in so
    a quiet month shows as a gap in the chart instead of vanishing."""
    if not stamps:
        return []
    counts = Counter((s.year, s.month) for s in stamps)
    year, month = min(counts)
    last = max(counts)
    out = []
    while (year, month) <= last:
        out.append({"month": f"{year:04d}-{month:02d}", "orders": counts.get((year, month), 0)})
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _ranked(counter: Counter) -> list[dict]:
    """Highest count first; ties alphabetical so the order is stable."""
    return [{"label": k, "count": v} for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


@router.get("/stores/orders/stats")
def uniform_order_stats(
    days: int | None = Query(None, ge=1, le=36500, description="Only orders from the last N days; omit for all time"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    query = db.query(StoresOrder).options(selectinload(StoresOrder.order_items))
    since = _since(days)
    if since:
        query = query.filter(StoresOrder.created_at >= since)
    orders = query.all()

    per_item: dict[str, dict] = defaultdict(lambda: {"count": 0, "given": 0, "orders": set(), "sizes": Counter()})
    total_items = 0
    for order in orders:
        for oi in order.order_items:
            total_items += 1
            row = per_item[(oi.item_type or "").strip() or "Unknown"]
            row["count"] += 1
            row["orders"].add(order.id)
            if oi.given_at:
                row["given"] += 1
            size = (oi.size or "").strip()
            row["sizes"][size or UNSIZED] += 1

    items = []
    for name, row in per_item.items():
        sizes = _ranked(row["sizes"])
        items.append({
            "item_type": name,
            "count": row["count"],
            "orders": len(row["orders"]),
            "given": row["given"],
            "top_size": next((s["label"] for s in sizes if s["label"] != UNSIZED), None),
            "sizes": sizes[:TOP_SIZES_LIMIT],
        })
    items.sort(key=lambda r: (-r["count"], r["item_type"]))

    return {
        "days": days,
        "total_orders": len(orders),
        "completed_orders": sum(1 for o in orders if o.completed),
        "kitting_orders": sum(1 for o in orders if o.kitting),
        "total_items": total_items,
        "items": items,
        "monthly": _monthly([o.created_at for o in orders if o.created_at]),
    }


@router.get("/stores/badges/orders/stats")
def badge_order_stats(
    days: int | None = Query(None, ge=1, le=36500, description="Only orders from the last N days; omit for all time"),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    query = db.query(BadgeOrder).options(selectinload(BadgeOrder.order_items))
    since = _since(days)
    if since:
        query = query.filter(BadgeOrder.created_at >= since)
    orders = query.all()

    per_badge: dict[str, dict] = defaultdict(lambda: {"count": 0, "replacements": 0, "given": 0, "orders": set()})
    gained_where: Counter = Counter()
    total_items = replacements = 0
    for order in orders:
        for oi in order.order_items:
            total_items += 1
            row = per_badge[(oi.badge_name or "").strip() or "Unknown"]
            row["count"] += 1
            row["orders"].add(order.id)
            if oi.replacement:
                row["replacements"] += 1
                replacements += 1
            if oi.given_at:
                row["given"] += 1
            if oi.gained_where:
                gained_where[oi.gained_where] += 1

    badges = [
        {
            "badge_name": name,
            "count": row["count"],
            "orders": len(row["orders"]),
            "replacements": row["replacements"],
            "given": row["given"],
        }
        for name, row in per_badge.items()
    ]
    badges.sort(key=lambda r: (-r["count"], r["badge_name"]))

    return {
        "days": days,
        "total_orders": len(orders),
        "completed_orders": sum(1 for o in orders if o.completed),
        "total_items": total_items,
        "replacements": replacements,
        "badges": badges,
        "gained_where": _ranked(gained_where),
        "monthly": _monthly([o.created_at for o in orders if o.created_at]),
    }
