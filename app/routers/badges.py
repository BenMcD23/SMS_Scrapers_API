"""Badge stores — the storage grid and badge orders (staff side)."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.models import (
    Cadet, BadgeGridConfig, BadgeGridCell, BadgeItem, BadgeOrder, BadgeOrderItem,
)

from core.db import get_db
from core.emailer import send_email, ready_to_collect_email_html
from core.security import require_staff

router = APIRouter()


# ── Serialisers ───────────────────────────────────────────────────────────────

def _cell_to_dict(cell: BadgeGridCell) -> dict:
    return {
        "id": cell.id,
        "row": cell.row,
        "col": cell.col,
        "label": cell.label,
        "items": [{"id": i.id, "name": i.name, "quantity": i.quantity} for i in cell.items],
    }


def _badge_full_response(db: Session) -> dict:
    cfg = _get_or_create_badge_config(db)
    cells = db.query(BadgeGridCell).order_by(BadgeGridCell.row, BadgeGridCell.col).all()
    return {
        "config": {"numRows": cfg.num_rows, "numCols": cfg.num_cols},
        "cells": [_cell_to_dict(c) for c in cells],
    }


def _get_or_create_badge_config(db: Session) -> BadgeGridConfig:
    cfg = db.query(BadgeGridConfig).first()
    if not cfg:
        cfg = BadgeGridConfig(num_rows=1, num_cols=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def badge_order_to_dict(order: BadgeOrder) -> dict:
    return {
        "id":        str(order.id),
        "cadetName": f"{order.cadet.first_name} {order.cadet.last_name}",
        "cadetCin":  order.cadet.cin,
        "timestamp": order.created_at.isoformat(),
        "completed": bool(order.completed),
        "items": [
            {
                "id":             str(oi.id),
                "badgeName":      oi.badge_name,
                "qmNotes":        json.loads(oi.qm_notes) if oi.qm_notes and oi.qm_notes.strip().startswith("[") else [],
                "givenAt":        oi.given_at.isoformat() if oi.given_at else None,
                "givenBy":        oi.given_by,
                "readyToCollect": oi.ready_to_collect.isoformat() if oi.ready_to_collect else None,
            }
            for oi in sorted(order.order_items, key=lambda x: x.id)
        ],
    }


# ── Grid layout ───────────────────────────────────────────────────────────────

@router.get("/stores/badges")
def badge_get_all(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    return _badge_full_response(db)


@router.patch("/stores/badges/config")
def badge_patch_config(
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cfg = _get_or_create_badge_config(db)
    if "numRows" in body:
        cfg.num_rows = max(1, int(body["numRows"]))
    if "numCols" in body:
        cfg.num_cols = max(1, int(body["numCols"]))
    db.commit()

    # Ensure every position in the grid has a cell (handles gaps including (0,0) on first use)
    for r in range(cfg.num_rows):
        for c in range(cfg.num_cols):
            exists = db.query(BadgeGridCell).filter(
                BadgeGridCell.row == r, BadgeGridCell.col == c
            ).first()
            if not exists:
                db.add(BadgeGridCell(row=r, col=c, label=None))

    db.commit()
    return _badge_full_response(db)


@router.post("/stores/badges/cells", status_code=201)
def badge_create_cell(
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    row = int(body.get("row", 0))
    col = int(body.get("col", 0))
    existing = db.query(BadgeGridCell).filter(
        BadgeGridCell.row == row, BadgeGridCell.col == col
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Position already occupied")
    cell = BadgeGridCell(row=row, col=col, label=body.get("label") or None)
    db.add(cell)
    db.commit()
    db.refresh(cell)
    return _cell_to_dict(cell)


@router.delete("/stores/badges/cells/{cell_id}", status_code=204)
def badge_delete_cell(
    cell_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cell = db.query(BadgeGridCell).filter(BadgeGridCell.id == cell_id).first()
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    db.delete(cell)
    db.commit()


@router.patch("/stores/badges/cells/{cell_id}")
def badge_patch_cell(
    cell_id: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cell = db.query(BadgeGridCell).filter(BadgeGridCell.id == cell_id).first()
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")

    if "row" in body or "col" in body:
        new_row = int(body.get("row", cell.row))
        new_col = int(body.get("col", cell.col))
        # If another cell already sits there, swap positions
        other = db.query(BadgeGridCell).filter(
            BadgeGridCell.row == new_row,
            BadgeGridCell.col == new_col,
            BadgeGridCell.id != cell_id,
        ).first()
        if other:
            other.row, other.col = cell.row, cell.col
        cell.row = new_row
        cell.col = new_col

    if "label" in body:
        cell.label = body["label"] or None

    db.commit()
    return _badge_full_response(db)


@router.post("/stores/badges/cells/{cell_id}/items", status_code=201)
def badge_add_item(
    cell_id: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cell = db.query(BadgeGridCell).filter(BadgeGridCell.id == cell_id).first()
    if not cell:
        raise HTTPException(status_code=404, detail="Cell not found")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    quantity = max(1, int(body.get("quantity", 1)))
    item = BadgeItem(cell_id=cell_id, name=name, quantity=quantity)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "name": item.name, "quantity": item.quantity}


@router.patch("/stores/badges/items/{item_id}")
def badge_patch_item(
    item_id: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    item = db.query(BadgeItem).filter(BadgeItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name required")
        item.name = name
    if "quantity" in body:
        item.quantity = max(1, int(body["quantity"]))
    if "cellId" in body:
        cell = db.query(BadgeGridCell).filter(BadgeGridCell.id == int(body["cellId"])).first()
        if not cell:
            raise HTTPException(status_code=404, detail="Cell not found")
        item.cell_id = cell.id
    db.commit()
    db.refresh(item)
    return {"id": item.id, "name": item.name, "quantity": item.quantity, "cellId": item.cell_id}


@router.delete("/stores/badges/items/{item_id}", status_code=204)
def badge_delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    item = db.query(BadgeItem).filter(BadgeItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()


@router.delete("/stores/badges/rows/{row_index}")
def badge_delete_row(
    row_index: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cfg = _get_or_create_badge_config(db)
    if cfg.num_rows <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete last row")
    for cell in db.query(BadgeGridCell).filter(BadgeGridCell.row == row_index).all():
        db.delete(cell)
    for cell in db.query(BadgeGridCell).filter(BadgeGridCell.row > row_index).all():
        cell.row -= 1
    cfg.num_rows -= 1
    db.commit()
    return _badge_full_response(db)


@router.delete("/stores/badges/cols/{col_index}")
def badge_delete_col(
    col_index: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    cfg = _get_or_create_badge_config(db)
    if cfg.num_cols <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete last column")
    for cell in db.query(BadgeGridCell).filter(BadgeGridCell.col == col_index).all():
        db.delete(cell)
    for cell in db.query(BadgeGridCell).filter(BadgeGridCell.col > col_index).all():
        cell.col -= 1
    cfg.num_cols -= 1
    db.commit()
    return _badge_full_response(db)


# ── Badge orders (staff side) ─────────────────────────────────────────────────

@router.get("/stores/badges/orders")
def badge_orders_list(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    orders = db.query(BadgeOrder).order_by(BadgeOrder.created_at.desc()).all()
    return [badge_order_to_dict(o) for o in orders]


@router.post("/stores/badges/orders", status_code=201)
def badge_orders_create(
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

    order = BadgeOrder(cadet_id=cadet.cin, created_at=datetime.now())
    db.add(order)
    db.flush()

    for raw in items:
        if not raw.get("badgeName"):
            continue
        db.add(BadgeOrderItem(
            order_id   = order.id,
            badge_name = raw["badgeName"],
            qm_notes   = "[]",
        ))

    db.commit()
    db.refresh(order)
    return badge_order_to_dict(order)


@router.patch("/stores/badges/orders/{order_id}")
def badge_orders_update(
    order_id: int,
    body: dict,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    order = db.query(BadgeOrder).filter(BadgeOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if "completed" in body:
        order.completed = bool(body["completed"])

    if "items" in body:
        existing = {str(oi.id): oi for oi in order.order_items}
        for raw in body["items"]:
            raw_id = str(raw.get("id", "")) if raw.get("id") else ""
            if raw_id and raw_id in existing:
                oi = existing.pop(raw_id)
                if "badgeName" in raw:
                    oi.badge_name = raw["badgeName"]
                if "qmNotes" in raw:
                    oi.qm_notes = json.dumps(raw["qmNotes"])
                if "givenAt" in raw:
                    oi.given_at = datetime.fromisoformat(raw["givenAt"]) if raw["givenAt"] else None
                if "givenBy" in raw:
                    oi.given_by = raw["givenBy"]
            else:
                db.add(BadgeOrderItem(
                    order_id   = order.id,
                    badge_name = raw.get("badgeName", ""),
                    qm_notes   = "[]",
                ))
        for removed in existing.values():
            db.delete(removed)

    db.commit()
    db.refresh(order)
    return badge_order_to_dict(order)


@router.delete("/stores/badges/orders/{order_id}", status_code=204)
def badge_orders_delete(
    order_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    order = db.query(BadgeOrder).filter(BadgeOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    db.delete(order)
    db.commit()


@router.post("/stores/badges/orders/{order_id}/items/{item_id}/mark-ready", status_code=200)
def badge_orders_mark_item_ready(
    order_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    order = db.query(BadgeOrder).filter(BadgeOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    item = db.query(BadgeOrderItem).filter(
        BadgeOrderItem.id == item_id, BadgeOrderItem.order_id == order_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    item.ready_to_collect = datetime.utcnow()
    db.commit()

    if order.cadet and order.cadet.email:
        greeting_name = f"{order.cadet.rank} {order.cadet.last_name}" if order.cadet.rank else order.cadet.last_name
        send_email(
            to=order.cadet.email,
            subject="Your badge is ready to collect",
            html_body=ready_to_collect_email_html(cadet_name=greeting_name, item_name=item.badge_name, item_kind="badge"),
        )

    db.refresh(order)
    return badge_order_to_dict(order)
