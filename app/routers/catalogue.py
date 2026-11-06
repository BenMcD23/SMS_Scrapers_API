"""The Catalogue page's API: staff edit the uniform items and badge categories
that the stock and order forms offer.

Everything recorded against an entry (stock rows, order items, badge orders)
stores its name as text, so edits are limited to what can't orphan that data:
names are fixed once created, levels and badges that have been ordered can't be
removed, and an entry that is in use is retired (hidden from the pickers) rather
than deleted. Nothing here touches the stores tables themselves.
"""

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from core import catalogue_store
from core.audit import record_audit
from core.db import get_db
from core.security import require_staff
from database.models import (
    BadgeItem,
    BadgeOrderItem,
    CatalogueBadgeCategory,
    CatalogueItemType,
    StoresItem,
    StoresOrderItem,
)

router = APIRouter(prefix="/catalogue", tags=["catalogue"])
logger = logging.getLogger(__name__)

MAX_ENTRIES = 300      # sizes or badge levels per entry: far above any real list
MAX_TEXT = 60


def _clean_list(values: list[str]) -> list[str]:
    """Trimmed, blanks dropped, duplicates removed with the first one kept: the
    order staff typed is the order the pickers show."""
    out: list[str] = []
    for v in values:
        v = " ".join(str(v).split())
        if v and v not in out:
            out.append(v)
    if len(out) > MAX_ENTRIES:
        raise ValueError(f"At most {MAX_ENTRIES} entries")
    if any(len(v) > MAX_TEXT for v in out):
        raise ValueError(f"Each entry must be {MAX_TEXT} characters or fewer")
    return out


def _name(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("A name is required")
    if len(v) > MAX_TEXT:
        raise ValueError(f"Names are {MAX_TEXT} characters or fewer")
    return v


class OrderBody(BaseModel):
    ids: list[int]


# ── Uniform items ─────────────────────────────────────────────────────────────

class ItemTypeFields(BaseModel):
    gender: str | None = None
    issuanceCategory: str | None = None
    noSize: bool | None = None
    stockOnly: bool | None = None
    kitFlight: bool | None = None
    onOrderList: bool | None = None
    lowStockLevel: int | None = Field(default=None, ge=0, le=10_000)
    sizes: list[str] | None = None
    sizingFields: list[str] | None = None

    @field_validator("gender")
    @classmethod
    def _gender(cls, v):
        if v is not None and v not in catalogue_store.GENDERS:
            raise ValueError("Gender must be male, female or unisex")
        return v

    @field_validator("issuanceCategory")
    @classmethod
    def _issuance(cls, v):
        # "" means "its own name", the same as leaving it unset.
        return _name(v) if v and v.strip() else None

    @field_validator("sizes", "sizingFields")
    @classmethod
    def _lists(cls, v):
        return None if v is None else _clean_list(v)


class ItemTypeCreate(ItemTypeFields):
    name: str

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return _name(v)


class ItemTypePatch(ItemTypeFields):
    active: bool | None = None
    # lowStockLevel: null means "work it out", so "not sent" has to be told apart
    # from "sent as null"; model_fields_set does that.


def _item_usage(db: Session) -> dict[str, dict[str, int]]:
    stock = dict(db.query(StoresItem.item_type, func.count()).group_by(StoresItem.item_type).all())
    orders = dict(db.query(StoresOrderItem.item_type, func.count()).group_by(StoresOrderItem.item_type).all())
    return {"stock": stock, "orders": orders}


def _item_json(row: CatalogueItemType, usage: dict) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "gender": row.gender,
        "issuanceCategory": row.issuance_category,
        "noSize": row.no_size,
        "stockOnly": row.stock_only,
        "kitFlight": row.kit_flight,
        "onOrderList": row.on_order_list,
        "lowStockLevel": row.low_stock_level,
        "sizes": catalogue_store._loads(row.sizes),
        "sizingFields": catalogue_store._loads(row.sizing_fields),
        "active": row.active,
        "stockRows": usage["stock"].get(row.name, 0),
        "orderItems": usage["orders"].get(row.name, 0),
    }


def _get_item(db: Session, item_id: int) -> CatalogueItemType:
    row = db.query(CatalogueItemType).filter(CatalogueItemType.id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")
    return row


def _apply_item_fields(row: CatalogueItemType, body: ItemTypeFields) -> None:
    sent = body.model_fields_set
    if body.gender is not None:
        row.gender = body.gender
    if "issuanceCategory" in sent:
        row.issuance_category = None if body.issuanceCategory == row.name else body.issuanceCategory
    for attr, col in (("noSize", "no_size"), ("stockOnly", "stock_only"), ("kitFlight", "kit_flight"), ("onOrderList", "on_order_list")):
        if getattr(body, attr) is not None:
            setattr(row, col, getattr(body, attr))
    if "lowStockLevel" in sent:
        row.low_stock_level = body.lowStockLevel
    if body.sizes is not None:
        row.sizes = json.dumps(body.sizes)
    if body.sizingFields is not None:
        row.sizing_fields = json.dumps(body.sizingFields)


@router.get("/uniform")
def list_uniform_items(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Every item including retired ones, with how much is recorded against each."""
    usage = _item_usage(db)
    return [_item_json(r, usage) for r in catalogue_store.item_types(db, include_retired=True)]


@router.post("/uniform", status_code=201)
def create_uniform_item(body: ItemTypeCreate, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    existing = catalogue_store.item_types(db, include_retired=True)
    if any(r.name.casefold() == body.name.casefold() for r in existing):
        raise HTTPException(status_code=409, detail=f"There is already an item called {body.name}")
    row = CatalogueItemType(name=body.name, position=max((r.position for r in existing), default=-1) + 1)
    _apply_item_fields(row, body)
    if not row.no_size and not catalogue_store._loads(row.sizes):
        # A sized item with no sizes can't be stocked: say so now, not on the stock page.
        raise HTTPException(status_code=422, detail="Add at least one size, or mark the item as having no size")
    db.add(row)
    db.commit()
    record_audit(db, idinfo, action="create", resource="catalogue_item", resource_id=row.id, detail={"name": row.name})
    return _item_json(row, _item_usage(db))


@router.patch("/uniform/{item_id}")
def update_uniform_item(item_id: int, body: ItemTypePatch, db: Session = Depends(get_db),
                        idinfo: dict = Depends(require_staff)):
    row = _get_item(db, item_id)
    _apply_item_fields(row, body)
    if body.active is not None:
        row.active = body.active
    if not row.no_size and not catalogue_store._loads(row.sizes):
        raise HTTPException(status_code=422, detail="Add at least one size, or mark the item as having no size")
    db.commit()
    record_audit(db, idinfo, action="update", resource="catalogue_item", resource_id=row.id,
                 detail={"name": row.name, "fields": sorted(body.model_fields_set)})
    return _item_json(row, _item_usage(db))


@router.delete("/uniform/{item_id}", status_code=204)
def delete_uniform_item(item_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    row = _get_item(db, item_id)
    used = _item_usage(db)
    if used["stock"].get(row.name) or used["orders"].get(row.name):
        raise HTTPException(status_code=409, detail=f"{row.name} is in stock or on orders: retire it instead")
    name = row.name
    db.delete(row)
    db.commit()
    record_audit(db, idinfo, action="delete", resource="catalogue_item", resource_id=item_id, detail={"name": name})


@router.put("/uniform-order")
def reorder_uniform_items(body: OrderBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    rows = _reorder(db, catalogue_store.item_types(db, include_retired=True), body.ids)
    return [r.id for r in rows]


def _reorder(db: Session, rows: list, ids: list[int]) -> list:
    """Give the rows the positions of ``ids``. It must name every row once, so a
    stale page (one that missed an item added since) can't silently reorder a
    subset."""
    by_id = {r.id: r for r in rows}
    if len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise HTTPException(status_code=400, detail="The list is out of date: reload and try again")
    for pos, i in enumerate(ids):
        by_id[i].position = pos
    db.commit()
    return [by_id[i] for i in ids]


# ── Badge categories ──────────────────────────────────────────────────────────

class BadgeFields(BaseModel):
    name: str | None = None
    levels: list[str] | None = None
    items: list[str] | None = None
    gainedWhere: bool | None = None

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        return None if v is None else _name(v)

    @field_validator("levels", "items")
    @classmethod
    def _lists(cls, v):
        return None if v is None else _clean_list(v)


class BadgeCreate(BadgeFields):
    name: str
    # The word before the level in each badge's name ("Music" → "Music – Gold");
    # fixed once created because every order stores the full name.
    prefix: str | None = None

    @field_validator("prefix")
    @classmethod
    def _p(cls, v):
        return None if v is None or not v.strip() else _name(v)


class BadgePatch(BadgeFields):
    active: bool | None = None


def _badge_names(prefix: str | None, levels: list[str], items: list[str]) -> list[str]:
    return items if items else [f"{prefix} – {lvl}" for lvl in levels]


def _badge_usage(db: Session, names: list[str]) -> int:
    if not names:
        return 0
    ordered = db.query(func.count(BadgeOrderItem.id)).filter(BadgeOrderItem.badge_name.in_(names)).scalar()
    held = db.query(func.count(BadgeItem.id)).filter(BadgeItem.name.in_(names)).scalar()
    return (ordered or 0) + (held or 0)


def _badge_json(db: Session, row: CatalogueBadgeCategory) -> dict:
    items, levels = catalogue_store._loads(row.items), catalogue_store._loads(row.levels)
    return {
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "kind": "items" if items else "levels",
        "prefix": None if items else (row.prefix or row.name),
        "levels": levels,
        "items": items,
        "gainedWhere": row.gained_where,
        "active": row.active,
        "inUse": _badge_usage(db, _badge_names(row.prefix or row.name, levels, items)),
    }


def _get_badge(db: Session, category_id: int) -> CatalogueBadgeCategory:
    row = db.query(CatalogueBadgeCategory).filter(CatalogueBadgeCategory.id == category_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")
    return row


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "category"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


@router.get("/badges")
def list_badge_categories(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    return [_badge_json(db, r) for r in catalogue_store.badge_categories(db, include_retired=True)]


@router.post("/badges", status_code=201)
def create_badge_category(body: BadgeCreate, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    existing = catalogue_store.badge_categories(db, include_retired=True)
    if any(r.name.casefold() == body.name.casefold() for r in existing):
        raise HTTPException(status_code=409, detail=f"There is already a category called {body.name}")
    items, levels = body.items or [], body.levels or []
    if bool(items) == bool(levels):
        raise HTTPException(status_code=422, detail="Give either badge names or levels, not both or neither")
    prefix = body.prefix or body.name
    names = _badge_names(prefix, levels, items)
    # A new category can't claim a name another one already hands out.
    for r in existing:
        taken = set(_badge_names(r.prefix or r.name, catalogue_store._loads(r.levels), catalogue_store._loads(r.items)))
        if taken & set(names):
            raise HTTPException(status_code=409, detail=f"{sorted(taken & set(names))[0]} is already in {r.name}")
    row = CatalogueBadgeCategory(
        slug=_slug(body.name, {r.slug for r in existing}),
        name=body.name,
        position=max((r.position for r in existing), default=-1) + 1,
        prefix=None if items else prefix,
        levels=json.dumps(levels),
        items=json.dumps(items),
        gained_where=True if body.gainedWhere is None else body.gainedWhere,
    )
    db.add(row)
    db.commit()
    record_audit(db, idinfo, action="create", resource="catalogue_badge", resource_id=row.id, detail={"name": row.name})
    return _badge_json(db, row)


@router.patch("/badges/{category_id}")
def update_badge_category(category_id: int, body: BadgePatch, db: Session = Depends(get_db),
                          idinfo: dict = Depends(require_staff)):
    row = _get_badge(db, category_id)
    old_items, old_levels = catalogue_store._loads(row.items), catalogue_store._loads(row.levels)
    prefix = row.prefix or row.name
    if body.name is not None and body.name.casefold() != row.name.casefold():
        others = catalogue_store.badge_categories(db, include_retired=True)
        if any(r.id != row.id and r.name.casefold() == body.name.casefold() for r in others):
            raise HTTPException(status_code=409, detail=f"There is already a category called {body.name}")
    if old_items:
        if body.levels:
            raise HTTPException(status_code=422, detail="This category lists named badges, not levels")
        new_items, new_levels = (body.items if body.items is not None else old_items), []
        if not new_items:
            raise HTTPException(status_code=422, detail="Keep at least one badge")
    else:
        if body.items:
            raise HTTPException(status_code=422, detail="This category has levels, not named badges")
        new_items, new_levels = [], (body.levels if body.levels is not None else old_levels)
        if not new_levels:
            raise HTTPException(status_code=422, detail="Keep at least one level")
    # Removing a badge that has been ordered or is on the grid would leave its
    # name matching nothing; adding and reordering are always fine.
    removed = set(_badge_names(prefix, old_levels, old_items)) - set(_badge_names(prefix, new_levels, new_items))
    if removed and _badge_usage(db, sorted(removed)):
        in_use = [n for n in sorted(removed) if _badge_usage(db, [n])]
        raise HTTPException(status_code=409, detail=f"{', '.join(in_use)} has been ordered or is in stock: it can't be removed")
    if body.name is not None:
        row.name = body.name
    row.levels, row.items = json.dumps(new_levels), json.dumps(new_items)
    if body.gainedWhere is not None:
        row.gained_where = body.gainedWhere
    if body.active is not None:
        row.active = body.active
    db.commit()
    record_audit(db, idinfo, action="update", resource="catalogue_badge", resource_id=row.id,
                 detail={"name": row.name, "fields": sorted(body.model_fields_set)})
    return _badge_json(db, row)


@router.delete("/badges/{category_id}", status_code=204)
def delete_badge_category(category_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    row = _get_badge(db, category_id)
    names = _badge_names(row.prefix or row.name, catalogue_store._loads(row.levels), catalogue_store._loads(row.items))
    if _badge_usage(db, names):
        raise HTTPException(status_code=409, detail=f"{row.name} has been ordered or is in stock: retire it instead")
    name = row.name
    db.delete(row)
    db.commit()
    record_audit(db, idinfo, action="delete", resource="catalogue_badge", resource_id=category_id, detail={"name": name})


@router.put("/badges-order")
def reorder_badge_categories(body: OrderBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    rows = _reorder(db, catalogue_store.badge_categories(db, include_retired=True), body.ids)
    return [r.id for r in rows]
