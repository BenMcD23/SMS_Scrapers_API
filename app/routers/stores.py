"""Stores — shelf/box structure, stock, uniform orders, and issuances."""

import hmac
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    Cadet, User, ITEM_GENDER_MAP, ISSUANCE_ITEM_TYPE_MAP,
    StoresBox, StoresSection, StoresItem, StoresOrder, StoresOrderItem, StoresItemIssuance,
    LogsForm, LogsFormEntry,
)

from core.config import UNIFORM_FORM_API_KEY
from core.db import get_db
from core.emailer import send_email, ready_to_collect_email_html
from core.security import require_staff

router = APIRouter()


# ── Serialisers ───────────────────────────────────────────────────────────────

def _qm_notes_list(raw: str | None) -> list:
    return json.loads(raw) if raw and raw.strip().startswith("[") else []


def _item_to_dict(item: StoresItem) -> dict:
    return {
        "id":       str(item.id),
        "itemType": item.item_type,
        "size":     item.size,
        "box":      item.box.label,
        "section":  item.section.label,
        "quantity": item.quantity,
        "gender":   item.gender,
    }


def _box_to_dict(box: StoresBox) -> dict:
    sections_sorted = sorted(
        box.sections,
        key=lambda s: ((s.section_row or 0), (s.position or 0), s.label),
    )
    return {
        "label":         box.label,
        "shelfLevel":    box.shelf_level    if box.shelf_level    is not None else 1,
        "shelfPosition": box.shelf_position if box.shelf_position is not None else 0,
        "boxWidth":      box.box_width      if box.box_width      is not None else 100,
        "topEnd":        box.top_end        if box.top_end        is not None else "left",
        "sections": [
            {
                "label":        s.label,
                "row":          s.section_row   if s.section_row   is not None else 0,
                "position":     s.position      if s.position      is not None else 0,
                "sectionWidth": s.section_width if s.section_width is not None else 100,
            }
            for s in sections_sorted
        ],
    }


def _full_structure(db: Session) -> dict:
    boxes = (
        db.query(StoresBox)
        .order_by(StoresBox.shelf_level, StoresBox.shelf_position, StoresBox.label)
        .all()
    )
    return {"boxes": [_box_to_dict(b) for b in boxes]}


def order_to_dict(order: StoresOrder) -> dict:
    if order.cadet_id is not None and order.cadet:
        subject_name = f"{order.cadet.first_name} {order.cadet.last_name}"
        subject_type = "cadet"
    elif order.user_id is not None and order.user:
        subject_name = f"{order.user.first_name or ''} {order.user.last_name or ''}".strip() or order.user.email
        subject_type = "user"
    else:
        subject_name = "Unknown"
        subject_type = "unknown"
    return {
        "id":          str(order.id),
        "cadetName":   subject_name,
        "cadetCin":    order.cadet_id,
        "userId":      order.user_id,
        "subjectType": subject_type,
        "timestamp":   order.created_at.isoformat(),
        "completed":   bool(getattr(order, "completed", False)),
        "items": [
            {
                "id":             str(oi.id),
                "itemType":       oi.item_type,
                "size":           oi.size,
                "needSizing":     oi.need_sizing,
                "sizingDetails":  getattr(oi, "sizing_details", ""),
                "qmNotes":        _qm_notes_list(getattr(oi, "qm_notes", None)),
                "givenAt":        oi.given_at.isoformat() if oi.given_at else None,
                "givenBy":        oi.given_by,
                "readyToCollect": oi.ready_to_collect.isoformat() if oi.ready_to_collect else None,
            }
            for oi in sorted(order.order_items, key=lambda x: x.id)
        ],
    }


def issuance_to_dict(issuance: StoresItemIssuance) -> dict:
    return {
        "id": issuance.id,
        "itemCategory": issuance.item_category,
        "lastGiven": issuance.last_given.isoformat(),
        "sizeGiven": issuance.size_given,
    }


def add_order_items(db: Session, order: StoresOrder, items: list[dict]):
    for raw in items:
        if not raw.get("itemType", "").strip():
            continue
        db.add(StoresOrderItem(
            order_id       = order.id,
            item_type      = raw["itemType"],
            size           = raw.get("size", ""),
            need_sizing    = bool(raw.get("needSizing", False)),
            sizing_details = raw.get("sizingDetails", ""),
            qm_notes       = "[]",
        ))


# ── Structure ─────────────────────────────────────────────────────────────────

@router.get("/stores/structure")
def stores_get_structure(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return _full_structure(db)


@router.post("/stores/structure")
def stores_post_structure(
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    action  = body.get("action")
    box_lbl = body.get("box", "").strip().upper()
    sec_lbl = body.get("section", "").strip() if body.get("section") else None
    new_lbl = body.get("newLabel", "").strip() if body.get("newLabel") else None

    if action == "add-box":
        if not box_lbl:
            raise HTTPException(status_code=400, detail="Box label required")
        if db.query(StoresBox).filter(StoresBox.label == box_lbl).first():
            raise HTTPException(status_code=400, detail="Box already exists")
        level1_max = db.query(func.max(StoresBox.shelf_position)).filter(
            StoresBox.shelf_level == 1
        ).scalar()
        new_pos = (level1_max if level1_max is not None else -1) + 1
        db.add(StoresBox(label=box_lbl, shelf_level=1, shelf_position=new_pos, top_end='left'))
        db.commit()

    elif action == "add-area":
        if not box_lbl:
            raise HTTPException(status_code=400, detail="Area label required")
        if db.query(StoresBox).filter(StoresBox.label == box_lbl).first():
            raise HTTPException(status_code=400, detail="Label already exists")
        misc_max = db.query(func.max(StoresBox.shelf_position)).filter(
            StoresBox.shelf_level == 0
        ).scalar()
        new_pos = (misc_max if misc_max is not None else -1) + 1
        db.add(StoresBox(label=box_lbl, shelf_level=0, shelf_position=new_pos, top_end='left'))
        db.commit()

    elif action == "delete-box":
        box = db.query(StoresBox).filter(StoresBox.label == box_lbl).first()
        if not box:
            raise HTTPException(status_code=404, detail="Box not found")
        old_level = box.shelf_level if box.shelf_level is not None else 1
        db.delete(box)
        db.commit()
        # Compact shelf_position on that level
        remaining = (
            db.query(StoresBox)
            .filter(StoresBox.shelf_level == old_level)
            .order_by(StoresBox.shelf_position)
            .all()
        )
        for i, b in enumerate(remaining):
            b.shelf_position = i
        db.commit()

    elif action == "add-section":
        if not box_lbl or not sec_lbl:
            raise HTTPException(status_code=400, detail="Box and section required")
        box = db.query(StoresBox).filter(StoresBox.label == box_lbl).first()
        if not box:
            raise HTTPException(status_code=404, detail="Box not found")
        if any(s.label == sec_lbl for s in box.sections):
            raise HTTPException(status_code=400, detail="Section already exists")
        max_pos = db.query(func.max(StoresSection.position)).filter(
            StoresSection.box_id == box.id
        ).scalar()
        new_pos = (max_pos if max_pos is not None else -1) + 1
        db.add(StoresSection(box_id=box.id, label=sec_lbl, position=new_pos, section_row=0, section_width=100))
        db.commit()

    elif action == "delete-section":
        if not box_lbl or not sec_lbl:
            raise HTTPException(status_code=400, detail="Box and section required")
        box = db.query(StoresBox).filter(StoresBox.label == box_lbl).first()
        if not box:
            raise HTTPException(status_code=404, detail="Box not found")
        section = next((s for s in box.sections if s.label == sec_lbl), None)
        if not section:
            raise HTTPException(status_code=404, detail="Section not found")
        box_id = box.id
        db.delete(section)
        db.commit()
        # Compact section positions for this box
        remaining = (
            db.query(StoresSection)
            .filter(StoresSection.box_id == box_id)
            .order_by(StoresSection.position)
            .all()
        )
        for i, s in enumerate(remaining):
            s.position = i
        db.commit()

    elif action == "rename-box":
        new_box_lbl = (new_lbl or "").upper()
        if not box_lbl or not new_box_lbl:
            raise HTTPException(status_code=400, detail="Box and new label required")
        box = db.query(StoresBox).filter(StoresBox.label == box_lbl).first()
        if not box:
            raise HTTPException(status_code=404, detail="Box not found")
        if new_box_lbl != box_lbl and db.query(StoresBox).filter(StoresBox.label == new_box_lbl).first():
            raise HTTPException(status_code=400, detail="Label already exists")
        box.label = new_box_lbl
        db.commit()

    elif action == "rename-section":
        if not box_lbl or not sec_lbl or not new_lbl:
            raise HTTPException(status_code=400, detail="Box, section and new label required")
        box = db.query(StoresBox).filter(StoresBox.label == box_lbl).first()
        if not box:
            raise HTTPException(status_code=404, detail="Box not found")
        section = next((s for s in box.sections if s.label == sec_lbl), None)
        if not section:
            raise HTTPException(status_code=404, detail="Section not found")
        if new_lbl != sec_lbl and any(s.label == new_lbl for s in box.sections):
            raise HTTPException(status_code=400, detail="Section already exists")
        section.label = new_lbl
        db.commit()

    else:
        raise HTTPException(status_code=400, detail="Unknown action")

    return _full_structure(db)


@router.patch("/stores/boxes/{box_label}/layout")
def stores_patch_box_layout(
    box_label: str,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    box = db.query(StoresBox).filter(StoresBox.label == box_label.upper()).first()
    if not box:
        raise HTTPException(status_code=404, detail="Box not found")

    if "topEnd" in body:
        if body["topEnd"] not in ("left", "right"):
            raise HTTPException(status_code=400, detail="topEnd must be 'left' or 'right'")
        box.top_end = body["topEnd"]

    if "boxWidth" in body:
        box.box_width = max(10, int(body["boxWidth"]))

    if "shelfLevel" in body or "shelfPosition" in body:
        new_level = int(body.get("shelfLevel", box.shelf_level or 1))
        new_pos   = int(body.get("shelfPosition", box.shelf_position or 0))

        if new_level not in (1, 2, 3):
            raise HTTPException(status_code=400, detail="shelfLevel must be 1, 2, or 3")

        old_level = box.shelf_level or 1

        if old_level != new_level:
            # Compact old level after removal
            others_old = (
                db.query(StoresBox)
                .filter(StoresBox.shelf_level == old_level, StoresBox.id != box.id)
                .order_by(StoresBox.shelf_position)
                .all()
            )
            for i, b in enumerate(others_old):
                b.shelf_position = i

        # Shift boxes on destination level to make room
        others_new = (
            db.query(StoresBox)
            .filter(StoresBox.shelf_level == new_level, StoresBox.id != box.id)
            .order_by(StoresBox.shelf_position)
            .all()
        )
        new_pos = max(0, min(new_pos, len(others_new)))
        for i, b in enumerate(others_new):
            b.shelf_position = i if i < new_pos else i + 1

        box.shelf_level    = new_level
        box.shelf_position = new_pos

    db.commit()
    db.refresh(box)
    return _full_structure(db)


@router.patch("/stores/boxes/{box_label}/sections/reorder")
def stores_reorder_sections(
    box_label: str,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    box = db.query(StoresBox).filter(StoresBox.label == box_label.upper()).first()
    if not box:
        raise HTTPException(status_code=404, detail="Box not found")

    sections_data: list = body.get("sections", [])
    section_map = {s.label: s for s in box.sections}

    incoming_labels = {str(sd["label"]) for sd in sections_data}
    if incoming_labels != set(section_map.keys()):
        raise HTTPException(
            status_code=400,
            detail="sections must contain exactly the existing section labels",
        )

    for sd in sections_data:
        s = section_map[str(sd["label"])]
        s.section_row   = int(sd.get("row", 0))
        s.position      = int(sd.get("position", 0))
        s.section_width = max(10, int(sd.get("sectionWidth", 100)))

    db.commit()
    return _full_structure(db)


# ── Stock ─────────────────────────────────────────────────────────────────────

@router.get("/stores/stock")
def stores_get_stock(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    items = db.query(StoresItem).all()
    return [_item_to_dict(i) for i in items]


@router.post("/stores/stock", status_code=201)
def stores_create_stock(
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    item_type = body.get("itemType", "").strip()
    size      = body.get("size",     "").strip()
    box_lbl   = body.get("box",      "").strip().upper()
    sec_lbl   = body.get("section",  "").strip()
    quantity  = body.get("quantity", 0)

    if not item_type or not size or not box_lbl or not sec_lbl:
        raise HTTPException(status_code=400, detail="Missing required fields")

    box = db.query(StoresBox).filter(StoresBox.label == box_lbl).first()
    if not box:
        raise HTTPException(status_code=404, detail="Box not found")
    section = next((s for s in box.sections if s.label == sec_lbl), None)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Same item in the same place just tops up the quantity
    existing = db.query(StoresItem).filter(
        StoresItem.item_type  == item_type,
        StoresItem.size       == size,
        StoresItem.box_id     == box.id,
        StoresItem.section_id == section.id,
    ).first()

    if existing:
        existing.quantity += int(quantity)
        db.commit()
        db.refresh(existing)
        return _item_to_dict(existing)

    item = StoresItem(
        item_type  = item_type,
        size       = size,
        quantity   = int(quantity),
        gender     = ITEM_GENDER_MAP.get(item_type, "unisex"),
        box_id     = box.id,
        section_id = section.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _item_to_dict(item)


@router.patch("/stores/stock/{item_id}")
def stores_update_stock(
    item_id: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    item = db.query(StoresItem).filter(StoresItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if "quantity" in body:
        item.quantity = int(body["quantity"])
    if "itemType" in body:
        item.item_type = body["itemType"]
        item.gender    = ITEM_GENDER_MAP.get(body["itemType"], "unisex")
    if "size" in body:
        item.size = body["size"]
    if "box" in body:
        box = db.query(StoresBox).filter(StoresBox.label == body["box"].strip().upper()).first()
        if not box:
            raise HTTPException(status_code=404, detail="Box not found")
        item.box_id = box.id
        # reset section if box changed
        item.section_id = box.sections[0].id if box.sections else item.section_id
    if "section" in body:
        box = db.query(StoresBox).filter(StoresBox.id == item.box_id).first()
        section = next((s for s in box.sections if s.label == body["section"]), None) if box else None
        if not section:
            raise HTTPException(status_code=404, detail="Section not found")
        item.section_id = section.id

    # If the edited item now matches another existing item, merge them
    duplicate = db.query(StoresItem).filter(
        StoresItem.id         != item.id,
        StoresItem.item_type  == item.item_type,
        StoresItem.size       == item.size,
        StoresItem.box_id     == item.box_id,
        StoresItem.section_id == item.section_id,
    ).first()

    if duplicate:
        duplicate.quantity += item.quantity
        db.delete(item)
        db.commit()
        db.refresh(duplicate)
        return _item_to_dict(duplicate)

    db.commit()
    db.refresh(item)
    return _item_to_dict(item)


@router.delete("/stores/stock/{item_id}", status_code=204)
def stores_delete_stock(
    item_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    item = db.query(StoresItem).filter(StoresItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()


# ── Orders (staff side) ───────────────────────────────────────────────────────

@router.get("/stores/orders")
def stores_get_orders(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    orders = db.query(StoresOrder).order_by(StoresOrder.created_at.desc()).all()
    return [order_to_dict(o) for o in orders]


@router.post("/stores/orders", status_code=201)
def stores_create_order(
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet_cin = body.get("cadetCin")
    items     = body.get("items", [])

    if not cadet_cin or not isinstance(items, list):
        raise HTTPException(status_code=400, detail="cadetCin and items required")

    cadet = db.query(Cadet).filter(Cadet.cin == int(cadet_cin)).first()
    if not cadet:
        raise HTTPException(status_code=404, detail="Cadet not found")

    order = StoresOrder(cadet_id=cadet.cin, created_at=datetime.now())
    db.add(order)
    db.flush()
    add_order_items(db, order, items)

    db.commit()
    db.refresh(order)
    return order_to_dict(order)


@router.patch("/stores/orders/{order_id}")
def stores_update_order(
    order_id: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    order = db.query(StoresOrder).filter(StoresOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if "completed" in body:
        order.completed = bool(body["completed"])

    if "items" in body:
        # Replace all order items with the new list.
        # Items with a numeric-string id are matched to existing rows; others are new.
        existing = {str(oi.id): oi for oi in order.order_items}
        for raw in body["items"]:
            raw_id = str(raw.get("id", "")) if raw.get("id") else ""
            if raw_id and raw_id in existing:
                oi = existing.pop(raw_id)
                oi.item_type      = raw.get("itemType",      oi.item_type)
                oi.size           = raw.get("size",           oi.size)
                oi.need_sizing    = bool(raw.get("needSizing", oi.need_sizing))
                oi.sizing_details = raw.get("sizingDetails",  oi.sizing_details)
                if "qmNotes" in raw:
                    oi.qm_notes = json.dumps(raw["qmNotes"])
            else:
                db.add(StoresOrderItem(
                    order_id       = order.id,
                    item_type      = raw.get("itemType", ""),
                    size           = raw.get("size", ""),
                    need_sizing    = bool(raw.get("needSizing", False)),
                    sizing_details = raw.get("sizingDetails", ""),
                    qm_notes       = "[]",
                ))
        # Delete items that were removed
        for removed in existing.values():
            db.delete(removed)

    db.commit()
    db.refresh(order)
    return order_to_dict(order)


@router.post("/stores/orders/form-import", status_code=201)
def stores_form_import(
    body: dict,
    db: Session = Depends(get_db),
    x_import_key: str = Header(None),
):
    """Import a batch of orders from the Google Form response sheet.

    Authenticated with the pre-shared UNIFORM_FORM_API_KEY instead of a user token.
    Each row matches a cadet by email and creates one order with the provided items.
    """
    if not UNIFORM_FORM_API_KEY or not x_import_key or not hmac.compare_digest(x_import_key, UNIFORM_FORM_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid import key")

    rows = body.get("rows", [])
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="rows must be a list")

    results = []
    for row in rows:
        email     = (row.get("email") or "").strip().lower()
        items     = row.get("items", [])
        timestamp = row.get("timestamp")

        if not email:
            results.append({"email": email, "status": "error", "detail": "Missing email"})
            continue

        cadet = db.query(Cadet).filter(func.lower(Cadet.email) == email).first()
        if not cadet:
            results.append({"email": email, "status": "error", "detail": "Cadet not found"})
            continue

        if not items:
            results.append({"email": email, "status": "skipped", "detail": "No items"})
            continue

        created_at = datetime.now()
        if timestamp:
            try:
                created_at = datetime.fromisoformat(timestamp)
            except ValueError:
                pass

        order = StoresOrder(cadet_id=cadet.cin, created_at=created_at)
        db.add(order)
        db.flush()
        add_order_items(db, order, items)

        db.commit()
        db.refresh(order)
        results.append({"email": email, "status": "created", "orderId": order.id})

    return {"results": results}


@router.delete("/stores/orders/{order_id}", status_code=204)
def stores_delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    order = db.query(StoresOrder).filter(StoresOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    db.delete(order)
    db.commit()


@router.post("/stores/orders/{order_id}/items/{item_id}/mark-ready", status_code=200)
def stores_mark_item_ready(
    order_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    order = db.query(StoresOrder).filter(StoresOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    item = db.query(StoresOrderItem).filter(
        StoresOrderItem.id == item_id, StoresOrderItem.order_id == order_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    item.ready_to_collect = datetime.utcnow()
    db.commit()

    if order.cadet and order.cadet.email:
        greeting_name = f"{order.cadet.rank} {order.cadet.last_name}" if order.cadet.rank else order.cadet.last_name
        send_email(
            to=order.cadet.email,
            subject="Your uniform item is ready to collect",
            html_body=ready_to_collect_email_html(
                cadet_name=greeting_name, item_name=item.item_type, item_kind="uniform", size=item.size or "",
            ),
        )

    db.refresh(order)
    return order_to_dict(order)


# ── Issuances ─────────────────────────────────────────────────────────────────

def _upsert_issuances(db: Session, items: list[dict], given_by: str,
                      cadet_cin: int = None, user_id: int = None) -> list[StoresItemIssuance]:
    updated = []
    for item in items:
        # Accept either a direct itemCategory or map from itemType
        if cadet_cin is not None:
            category = item.get("itemCategory") or ISSUANCE_ITEM_TYPE_MAP.get(item.get("itemType", ""))
        else:
            raw_category = item.get("itemCategory", "")
            category = ISSUANCE_ITEM_TYPE_MAP.get(raw_category, raw_category)
        if not category:
            continue

        size = item.get("sizeGiven") or item.get("size") or None
        raw_date = item.get("lastGiven")
        try:
            given_at = datetime.fromisoformat(raw_date) if raw_date else datetime.utcnow()
        except (ValueError, TypeError):
            given_at = datetime.utcnow()

        query = db.query(StoresItemIssuance).filter(StoresItemIssuance.item_category == category)
        if cadet_cin is not None:
            query = query.filter(StoresItemIssuance.cadet_id == cadet_cin)
        else:
            query = query.filter(StoresItemIssuance.user_id == user_id)

        existing = query.first()
        if existing:
            existing.last_given = given_at
            existing.size_given = size
            updated.append(existing)
        else:
            new_issuance = StoresItemIssuance(
                cadet_id=cadet_cin,
                user_id=user_id,
                item_category=category,
                last_given=given_at,
                size_given=size,
            )
            db.add(new_issuance)
            updated.append(new_issuance)

        # Stamp the order item it came from
        order_item_id = item.get("orderItemId")
        if order_item_id:
            order_item = db.query(StoresOrderItem).filter(StoresOrderItem.id == int(order_item_id)).first()
            if order_item:
                order_item.given_at = given_at
                order_item.given_by = given_by

    db.commit()
    for i in updated:
        db.refresh(i)
    return updated


@router.get("/stores/issuances/user/{user_id}")
def stores_get_user_issuances(
    user_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    issuances = (
        db.query(StoresItemIssuance)
        .filter(StoresItemIssuance.user_id == user_id)
        .all()
    )
    return [issuance_to_dict(i) for i in issuances]


@router.post("/stores/issuances/user/{user_id}", status_code=200)
def stores_mark_user_as_given(
    user_id: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updated = _upsert_issuances(
        db, body.get("items", []), body.get("givenBy") or "Unknown", user_id=user_id,
    )
    return [issuance_to_dict(i) for i in updated]


@router.get("/stores/issuances/{cadet_cin}")
def stores_get_issuances(
    cadet_cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet = db.query(Cadet).filter(Cadet.cin == cadet_cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail="Cadet not found")
    issuances = (
        db.query(StoresItemIssuance)
        .filter(StoresItemIssuance.cadet_id == cadet_cin)
        .all()
    )
    return [issuance_to_dict(i) for i in issuances]


@router.post("/stores/issuances/{cadet_cin}", status_code=200)
def stores_mark_as_given(
    cadet_cin: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cadet = db.query(Cadet).filter(Cadet.cin == cadet_cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail="Cadet not found")

    updated = _upsert_issuances(
        db, body.get("items", []), body.get("givenBy") or "Unknown", cadet_cin=cadet_cin,
    )
    return [issuance_to_dict(i) for i in updated]


@router.delete("/stores/issuances/{issuance_id}", status_code=204)
def stores_delete_issuance(
    issuance_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    issuance = db.query(StoresItemIssuance).filter(StoresItemIssuance.id == issuance_id).first()
    if not issuance:
        raise HTTPException(status_code=404, detail="Issuance record not found")
    db.delete(issuance)
    db.commit()


# ── Logs Form (RAFAC demand batches) ─────────────────────────────────────────

TIE_VARIANTS = {"Short", "Standard"}


def _logs_form_to_dict(form: LogsForm) -> dict:
    return {
        "id":        str(form.id),
        "createdAt": form.created_at.isoformat(),
        "orderedAt": form.ordered_at.isoformat() if form.ordered_at else None,
        "entries": [
            {
                "id":          str(e.id),
                "orderItemId": str(e.order_item_id) if e.order_item_id is not None else None,
                "itemType":    e.item_type,
                "size":        e.size,
                "cadetName":   e.cadet_name,
            }
            for e in sorted(form.entries, key=lambda x: x.id)
        ],
    }


def _order_subject(order: StoresOrder) -> tuple[str, int | None]:
    if order.cadet_id is not None and order.cadet:
        return f"{order.cadet.first_name} {order.cadet.last_name}", order.cadet_id
    if order.user_id is not None and order.user:
        name = f"{order.user.first_name or ''} {order.user.last_name or ''}".strip()
        return name or order.user.email, None
    return "Unknown", None


@router.get("/stores/logs-forms")
def logs_forms_list(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    forms = db.query(LogsForm).order_by(LogsForm.created_at.desc()).all()
    return [_logs_form_to_dict(f) for f in forms]


@router.post("/stores/logs-forms/entries", status_code=201)
def logs_forms_add_entry(
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    from form_generators.logs_form_gen import DESCRIPTION_TEMPLATES

    if not body.get("orderItemId"):
        raise HTTPException(status_code=400, detail="orderItemId required")
    item = db.query(StoresOrderItem).filter(StoresOrderItem.id == int(body["orderItemId"])).first()
    if not item:
        raise HTTPException(status_code=404, detail="Order item not found")

    if item.item_type not in DESCRIPTION_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"{item.item_type} cannot go on a logs form")

    if item.item_type == "Tie":
        size = body.get("tieVariant")
        if size not in TIE_VARIANTS:
            raise HTTPException(status_code=400, detail="tieVariant must be Short or Standard")
    elif item.item_type == "Belt":
        size = "64-114cm"
    else:
        if item.need_sizing or not item.size:
            raise HTTPException(status_code=400, detail="Item has no size yet")
        size = item.size

    existing = db.query(LogsFormEntry).filter(LogsFormEntry.order_item_id == item.id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Item is already on a logs form")

    cadet_name, cadet_cin = _order_subject(item.order)

    form = db.query(LogsForm).filter(LogsForm.ordered_at.is_(None)).first()
    if not form:
        form = LogsForm(created_at=datetime.now())
        db.add(form)
        db.flush()

    db.add(LogsFormEntry(
        form_id       = form.id,
        order_item_id = item.id,
        item_type     = item.item_type,
        size          = size,
        cadet_name    = cadet_name,
        cadet_cin     = cadet_cin,
        created_at    = datetime.now(),
    ))
    db.commit()
    db.refresh(form)
    return _logs_form_to_dict(form)


@router.delete("/stores/logs-forms/entries/{entry_id}", status_code=204)
def logs_forms_delete_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    entry = db.query(LogsFormEntry).filter(LogsFormEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.form.ordered_at is not None:
        raise HTTPException(status_code=400, detail="Form has been ordered and cannot be edited")
    db.delete(entry)
    db.commit()


@router.post("/stores/logs-forms/{form_id}/mark-ordered")
def logs_forms_mark_ordered(
    form_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    form = db.query(LogsForm).filter(LogsForm.id == form_id).first()
    if not form:
        raise HTTPException(status_code=404, detail="Logs form not found")
    if form.ordered_at is not None:
        raise HTTPException(status_code=400, detail="Form is already marked as ordered")
    form.ordered_at = datetime.now()
    db.commit()
    db.refresh(form)
    return _logs_form_to_dict(form)


@router.get("/stores/logs-forms/{form_id}/download")
def logs_forms_download(
    form_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    from form_generators.logs_form_gen import generate_logs_form

    form = db.query(LogsForm).filter(LogsForm.id == form_id).first()
    if not form:
        raise HTTPException(status_code=404, detail="Logs form not found")
    entries = sorted(form.entries, key=lambda x: x.id)
    if not entries:
        raise HTTPException(status_code=400, detail="Logs form is empty")

    nominal_roll = []
    seen: set[str] = set()
    for e in entries:
        key = f"{e.cadet_cin}|{e.cadet_name}"
        if key in seen:
            continue
        seen.add(key)
        rank, issue = "", ""
        if e.cadet_cin is not None:
            cadet = db.query(Cadet).filter(Cadet.cin == e.cadet_cin).first()
            rank = (cadet.rank if cadet else "") or ""
            has_issuance = (
                db.query(StoresItemIssuance)
                .filter(StoresItemIssuance.cadet_id == e.cadet_cin)
                .first()
            )
            issue = "Exchange" if has_issuance else "Initial Issue"
        nominal_roll.append((rank, e.cadet_name, issue))

    xlsx_bytes = generate_logs_form([(e.item_type, e.size) for e in entries], nominal_roll)
    filename = f"Logs Form 202 - {form.created_at.strftime('%d %b %Y')}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
