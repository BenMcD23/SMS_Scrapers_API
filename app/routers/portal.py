"""Cadet portal self-service endpoints.

Cadets authenticate with their Google ID token and are matched to their Cadet
row by email — they never supply their own CIN. Staff/adult users on the portal
get the /users/me equivalents, scoped to their own User row.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from core.db import get_current_cadet, get_current_user, get_db
from core.security import get_roles_for_emails, require_staff
from database.models import (
    BadgeOrder,
    BadgeOrderItem,
    Cadet,
    Staff,
    StaffAttendance,
    StoresItemIssuance,
    StoresOrder,
    StoresOrderItem,
    User,
)
from routers.badges import _parse_timestamp, badge_order_to_dict
from routers.cadets import attendance_to_dict
from routers.stores import issuance_to_dict, order_to_dict
from texts.phone import clean_mobile
from texts.settings import community_invite_url

router = APIRouter()


class OrderItemIn(BaseModel):
    itemType: str
    size: str = ""
    needSizing: bool = False
    sizingDetails: str = ""


class OrderBody(BaseModel):
    items: list[OrderItemIn]


class BadgeOrderItemIn(BaseModel):
    badgeName: str
    replacement: bool = False  # replacement badges carry a £2 fee
    gainedWhere: str | None = None  # "camp" | "sector_training_weekend" | "wing_training_weekend" | "on_sqn" | "other"
    gainedWhereDetail: str | None = None  # free text when gainedWhere == "other"
    gainedDateFrom: str | None = None  # camp / sector training weekend dates
    gainedDateTo: str | None = None


class BadgeOrderBody(BaseModel):
    items: list[BadgeOrderItemIn]


class PhoneNumberBody(BaseModel):
    phone_number: str


def _add_uniform_items(db: Session, order: StoresOrder, items: list[OrderItemIn]):
    for item in items:
        if not item.itemType:
            continue
        db.add(StoresOrderItem(
            order_id       = order.id,
            item_type      = item.itemType,
            size           = item.size,
            need_sizing    = item.needSizing,
            sizing_details = item.sizingDetails,
            qm_notes       = "[]",
        ))


def _replace_pending_items(db: Session, order, add_items_fn):
    """Swap out an order's items, keeping anything already given out."""
    if order.completed:
        raise HTTPException(status_code=400, detail="Cannot edit a completed order")

    # Snapshot the replaceable items before adding the new ones
    old_pending = [oi for oi in order.order_items if oi.given_at is None]
    add_items_fn()
    for oi in old_pending:
        db.delete(oi)

    db.commit()
    db.refresh(order)


def _delete_order(db: Session, order):
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.completed:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed order")
    if any(oi.given_at is not None for oi in order.order_items):
        raise HTTPException(status_code=400, detail="Cannot cancel an order where items have already been given out")
    db.delete(order)
    db.commit()


# ── Cadet endpoints ───────────────────────────────────────────────────────────

@router.get("/cadets/me")
def cadet_get_me(
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    return {
        "cin":   cadet.cin,
        "name":  f"{cadet.first_name} {cadet.last_name}",
        "email": cadet.email,
        "phone_number": cadet.phone_number or "",
        # Sent alongside the number so the portal can offer the community as
        # soon as one is saved, without a second round trip.
        "whatsapp_invite_url": community_invite_url(db),
    }


@router.patch("/cadets/me/phone-number")
def cadet_set_phone_number(
    body: PhoneNumberBody,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    """The cadet's own mobile for the parade-night texts. Sending it empty takes
    them off the list — no staff round trip either way."""
    try:
        phone = clean_mobile(body.phone_number)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cadet.phone_number = phone or None
    db.commit()
    return {
        "status": "success",
        "phone_number": phone,
        "whatsapp_invite_url": community_invite_url(db),
    }


@router.get("/cadets/me/orders")
def cadet_get_orders(
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    orders = (
        db.query(StoresOrder)
        .filter(StoresOrder.cadet_id == cadet.cin)
        .options(selectinload(StoresOrder.order_items))
        .order_by(StoresOrder.created_at.desc())
        .all()
    )
    return [order_to_dict(o) for o in orders]


@router.post("/cadets/me/orders", status_code=201)
def cadet_create_order(
    body: OrderBody,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    if not body.items:
        raise HTTPException(status_code=400, detail="At least one item is required")

    order = StoresOrder(cadet_id=cadet.cin, created_at=datetime.now())
    db.add(order)
    db.flush()
    _add_uniform_items(db, order, body.items)

    db.commit()
    db.refresh(order)
    return order_to_dict(order)


@router.patch("/cadets/me/orders/{order_id}")
def cadet_patch_order(
    order_id: int,
    body: OrderBody,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    order = db.query(StoresOrder).filter(
        StoresOrder.id == order_id,
        StoresOrder.cadet_id == cadet.cin,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    _replace_pending_items(db, order, lambda: _add_uniform_items(db, order, body.items))
    return order_to_dict(order)


@router.delete("/cadets/me/orders/{order_id}", status_code=204)
def cadet_delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    order = db.query(StoresOrder).filter(
        StoresOrder.id == order_id,
        StoresOrder.cadet_id == cadet.cin,
    ).first()
    _delete_order(db, order)


@router.get("/cadets/me/issuances")
def cadet_get_my_issuances(
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    issuances = (
        db.query(StoresItemIssuance)
        .filter(StoresItemIssuance.cadet_id == cadet.cin)
        .all()
    )
    return [issuance_to_dict(i) for i in issuances]


# ── Cadet badge orders ────────────────────────────────────────────────────────

@router.get("/cadets/me/badge-orders")
def cadet_get_badge_orders(
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    orders = (
        db.query(BadgeOrder)
        .filter(BadgeOrder.cadet_id == cadet.cin)
        .options(selectinload(BadgeOrder.order_items))
        .order_by(BadgeOrder.created_at.desc())
        .all()
    )
    return [badge_order_to_dict(o) for o in orders]


@router.post("/cadets/me/badge-orders", status_code=201)
def cadet_create_badge_order(
    body: BadgeOrderBody,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    if not body.items:
        raise HTTPException(status_code=400, detail="At least one badge is required")

    order = BadgeOrder(cadet_id=cadet.cin, created_at=datetime.now())
    db.add(order)
    db.flush()

    for item in body.items:
        if item.badgeName:
            db.add(BadgeOrderItem(
                order_id            = order.id,
                badge_name          = item.badgeName,
                replacement         = item.replacement,
                qm_notes            = "[]",
                gained_where        = item.gainedWhere,
                gained_where_detail = item.gainedWhereDetail,
                gained_date_from    = _parse_timestamp(item.gainedDateFrom),
                gained_date_to      = _parse_timestamp(item.gainedDateTo),
            ))

    db.commit()
    db.refresh(order)
    return badge_order_to_dict(order)


@router.patch("/cadets/me/badge-orders/{order_id}")
def cadet_patch_badge_order(
    order_id: int,
    body: BadgeOrderBody,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    order = db.query(BadgeOrder).filter(
        BadgeOrder.id == order_id,
        BadgeOrder.cadet_id == cadet.cin,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    def add_items():
        for item in body.items:
            if item.badgeName:
                db.add(BadgeOrderItem(
                    order_id            = order.id,
                    badge_name          = item.badgeName,
                    replacement         = item.replacement,
                    qm_notes            = "[]",
                    gained_where        = item.gainedWhere,
                    gained_where_detail = item.gainedWhereDetail,
                    gained_date_from    = _parse_timestamp(item.gainedDateFrom),
                    gained_date_to      = _parse_timestamp(item.gainedDateTo),
                ))

    _replace_pending_items(db, order, add_items)
    return badge_order_to_dict(order)


@router.delete("/cadets/me/badge-orders/{order_id}", status_code=204)
def cadet_delete_badge_order(
    order_id: int,
    db: Session = Depends(get_db),
    cadet: Cadet = Depends(get_current_cadet),
):
    order = db.query(BadgeOrder).filter(
        BadgeOrder.id == order_id,
        BadgeOrder.cadet_id == cadet.cin,
    ).first()
    _delete_order(db, order)


# ── User (staff/adult) endpoints ──────────────────────────────────────────────

@router.get("/users/me/orders")
def user_get_orders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    orders = (
        db.query(StoresOrder)
        .filter(StoresOrder.user_id == user.id)
        .options(selectinload(StoresOrder.order_items))
        .order_by(StoresOrder.created_at.desc())
        .all()
    )
    return [order_to_dict(o) for o in orders]


@router.post("/users/me/orders", status_code=201)
def user_create_order(
    body: OrderBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not body.items:
        raise HTTPException(status_code=400, detail="At least one item is required")

    order = StoresOrder(user_id=user.id, cadet_id=None, created_at=datetime.now())
    db.add(order)
    db.flush()
    _add_uniform_items(db, order, body.items)

    db.commit()
    db.refresh(order)
    return order_to_dict(order)


@router.patch("/users/me/orders/{order_id}")
def user_patch_order(
    order_id: int,
    body: OrderBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.query(StoresOrder).filter(
        StoresOrder.id == order_id,
        StoresOrder.user_id == user.id,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    _replace_pending_items(db, order, lambda: _add_uniform_items(db, order, body.items))
    return order_to_dict(order)


@router.delete("/users/me/orders/{order_id}", status_code=204)
def user_delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.query(StoresOrder).filter(
        StoresOrder.id == order_id,
        StoresOrder.user_id == user.id,
    ).first()
    _delete_order(db, order)


@router.get("/users/me/issuances")
def user_get_my_issuances(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    issuances = (
        db.query(StoresItemIssuance)
        .filter(StoresItemIssuance.user_id == user.id)
        .all()
    )
    return [issuance_to_dict(i) for i in issuances]


# ── Staff admin: list all users ───────────────────────────────────────────────

@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    users = db.query(User).all()
    # Resolve every role in one batched group lookup — get_user_role per user
    # was a sequential admin-API round-trip each.
    roles = get_roles_for_emails([u.email for u in users])
    return [
        {
            "id":        u.id,
            "email":     u.email,
            "firstName": u.first_name,
            "lastName":  u.last_name,
            "role":      roles.get(u.email),
        }
        for u in users
    ]


@router.get("/staff")
def list_staff(
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Scraped SMS staff roster. `userId` is set only where a portal User
    shares the email, so the detail page can show their uniform issuances."""
    user_by_email = {
        u.email.lower(): u.id for u in db.query(User).all() if u.email
    }
    return [
        {
            "cin":       s.cin,
            "firstName": s.first_name,
            "lastName":  s.last_name,
            "rank":      s.rank,
            "email":     s.email,
            "phoneNumber": s.phone_number,
            "address":   s.address,
            "attendance": s.attendance,
            "userId":    user_by_email.get((s.email or "").lower()),
        }
        for s in db.query(Staff).all()
    ]


@router.get("/staff/{cin}/attendance")
def staff_attendance(
    cin: int,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """Full scraped attendance register, newest first. Same shape as the cadet
    endpoint so both feed the same UI."""
    rows = (
        db.query(StaffAttendance)
        .filter(StaffAttendance.staff_id == cin)
        .order_by(StaffAttendance.date.desc())
        .all()
    )
    return [attendance_to_dict(r) for r in rows]
