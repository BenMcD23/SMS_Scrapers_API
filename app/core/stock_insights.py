"""What the uniform stores give out, what they hold, and what to order.

The QM's question is "what should we order?". Answering it means putting two
things side by side that the stores pages only show separately: demand (every
order item asked for in the window, by item and size) and supply (the shelf
count for that item and size, summed across every box it sits in).

This module is the arithmetic, kept free of the database so the rules can be
tested with plain rows; ``routers/stores.py`` gathers the rows and serves it.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from core.catalogue import NO_SIZE_ITEMS, UNIFORM_SIZES

# A size the catalogue doesn't list (an old spelling, a typo on an order) still
# counts, it just sorts after the catalogue's own sizes.
_UNKNOWN_SIZE = 10_000


@dataclass(frozen=True)
class DemandRow:
    """One order item. ``size`` is "" when the item has no size yet."""
    item_type: str
    size: str
    need_sizing: bool
    requested_at: datetime
    given: bool
    # Still to be handed over from the shelf: not given, order still open, not
    # already taken off the shelf, and not being ordered in by a logs form.
    waiting: bool


@dataclass(frozen=True)
class StockRow:
    item_type: str
    size: str
    quantity: int


def size_key(item_type: str, size: str) -> str:
    """One-size items (belt, brassard, tie) are one line each. Orders rarely
    carry a size for them, and the shelf might say "Short" or "64-114cm", so
    splitting by what was typed would show demand and stock on different rows."""
    return "" if item_type in NO_SIZE_ITEMS else size.strip()


def demand_size(item_type: str, size: str, need_sizing: bool) -> str | None:
    """The size an order item counts under, or None while it still needs
    measuring: without a size it can't be matched to a shelf."""
    if item_type in NO_SIZE_ITEMS:
        return ""
    size = size.strip()
    return size if size and not need_sizing else None


def _size_order(item_type: str, size: str) -> tuple[int, str]:
    sizes = UNIFORM_SIZES.get(item_type, ())
    return (sizes.index(size) if size in sizes else _UNKNOWN_SIZE, size)


def _status(requested: int, waiting: int, in_stock: int, to_order: int) -> str:
    """One word for the row, worst first, so the table can sort and colour by it.

    "short": cadets are already waiting for more than the shelf holds.
    "out":   asked for in the window and none on the shelf.
    "low":   on the shelf, but not enough to cover the next few months.
    "rare":  asked for now and then, too seldom to be worth keeping one in;
             order it when a cadet needs it.
    "unused": on the shelf but nobody asked for it in the window.
    """
    if waiting > in_stock:
        return "short"
    if to_order > 0:
        return "out" if in_stock == 0 else "low"
    if requested and in_stock == 0:
        return "rare"
    if not requested and in_stock > 0:
        return "unused"
    return "ok"


STATUS_RANK = {"short": 0, "out": 1, "low": 2, "rare": 3, "ok": 4, "unused": 5}


def build_insights(
    demand: list[DemandRow],
    stock: list[StockRow],
    *,
    since: datetime,
    months: int,
    cover_months: int,
    last_requested: dict[tuple[str, str], datetime] | None = None,
) -> dict:
    """Combine demand since ``since`` with current stock.

    ``months`` is the window length the rates are worked out over; an order
    suggestion covers ``cover_months`` of that rate plus everyone already
    waiting, less what is on the shelf. ``last_requested`` is the all-time most
    recent request per (item, size), so a line nobody asked for this window can
    still say when it last went out.
    """
    last_requested = last_requested or {}
    rows: dict[tuple[str, str], dict] = {}
    items: dict[str, dict] = {}

    def row(item_type: str, size: str) -> dict:
        return rows.setdefault((item_type, size), {
            "itemType": item_type, "size": size,
            "requested": 0, "given": 0, "waiting": 0, "inStock": 0,
        })

    def item(item_type: str) -> dict:
        return items.setdefault(item_type, {
            "itemType": item_type, "requested": 0, "given": 0, "waiting": 0,
            "awaitingSizing": 0, "inStock": 0,
        })

    for d in demand:
        # Waiting counts whenever it was asked for: a cadet still waiting from
        # last year still needs one off the shelf. Requested/given are the window.
        in_window = d.requested_at >= since
        size = demand_size(d.item_type, d.size, d.need_sizing)
        unsized = size is None
        totals = item(d.item_type)
        if in_window:
            totals["requested"] += 1
            totals["given"] += d.given
        if d.waiting:
            totals["waiting"] += 1
            totals["awaitingSizing"] += unsized
        # Without a size an item can't be matched to a shelf, so it only counts
        # towards the item's totals ("3 still need measuring").
        if unsized or not (in_window or d.waiting):
            continue
        r = row(d.item_type, size)
        if in_window:
            r["requested"] += 1
            r["given"] += d.given
        r["waiting"] += d.waiting

    for s in stock:
        if s.quantity <= 0:
            continue
        row(s.item_type, size_key(s.item_type, s.size))["inStock"] += s.quantity
        item(s.item_type)["inStock"] += s.quantity

    lines = []
    for (item_type, size), r in rows.items():
        rate = r["requested"] / months
        # Round to the nearest whole item, not up: rounding up would put one
        # of every size anyone asked for once a year on the order list, and the
        # list stops being worth reading. Those rare sizes are ordered per cadet.
        need = math.floor(rate * cover_months + 0.5) + r["waiting"]
        r["monthlyRate"] = round(rate, 2)
        r["toOrder"] = max(0, need - r["inStock"])
        r["status"] = _status(r["requested"], r["waiting"], r["inStock"], r["toOrder"])
        last = last_requested.get((item_type, size))
        r["lastRequested"] = last.isoformat() if last else None
        lines.append(r)

    lines.sort(key=lambda r: (r["itemType"], _size_order(r["itemType"], r["size"])))

    for totals in items.values():
        sized = [r for r in lines if r["itemType"] == totals["itemType"]]
        totals["sizes"] = sized
        # The size most asked for, ties to the smaller size, so the card can say
        # "most common: 56".
        asked = [r for r in sized if r["requested"]]
        totals["topSize"] = max(asked, key=lambda r: r["requested"])["size"] if asked else None

    to_order = sorted(
        (r for r in lines if r["toOrder"] > 0),
        key=lambda r: (STATUS_RANK[r["status"]], -r["toOrder"], r["itemType"],
                       _size_order(r["itemType"], r["size"])),
    )
    unused = sorted(
        (r for r in lines if r["status"] == "unused"),
        key=lambda r: (-r["inStock"], r["itemType"], _size_order(r["itemType"], r["size"])),
    )

    return {
        "since": since.isoformat(),
        "months": months,
        "coverMonths": cover_months,
        "summary": {
            "requested": sum(t["requested"] for t in items.values()),
            "given": sum(t["given"] for t in items.values()),
            "waiting": sum(t["waiting"] for t in items.values()),
            "awaitingSizing": sum(t["awaitingSizing"] for t in items.values()),
            "inStock": sum(t["inStock"] for t in items.values()),
            "linesToOrder": len(to_order),
            "unitsToOrder": sum(r["toOrder"] for r in to_order),
            "unusedLines": len(unused),
            "unusedUnits": sum(r["inStock"] for r in unused),
        },
        "items": sorted(items.values(), key=lambda t: (-t["requested"], t["itemType"])),
        "toOrder": to_order,
        "unused": unused,
    }
