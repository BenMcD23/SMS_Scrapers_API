"""Booking stock in by scanning: what a label's stock number means, and
adding the item it names to a shelf.

The Book in page reads a stock number (the bag's barcode, the number typed, or
a photo of the sewn-in label read by core/label_reader) and books one item in
per scan. The number is resolved here rather than trusted from the page, so a
stale page can't book in the wrong item, and each booking goes through the
same stock change log as the Stock page's own edits.
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core import catalogue_store, label_reader, nsn, stock_changes
from core.actor import actor_name
from core.db import get_db
from core.ratelimit import limiter
from core.security import require_staff
from database.models import StoresBox, StoresItem, StoresNsn
from routers.stores import _item_to_dict

router = APIRouter()
logger = logging.getLogger(__name__)

# A phone photo can be several MB; the page shrinks it first, this is the backstop.
MAX_PHOTO_BYTES = 10 * 1024 * 1024
# One scan is one item; a quantity is for a box of the same thing typed in.
MAX_BOOK_IN = 100


def _nsn_or_400(raw: str) -> str:
    value = nsn.normalise(raw)
    if not value:
        raise HTTPException(status_code=400, detail="A stock number is 13 digits, like 8405-99-845-6634")
    return value


def _taught_dict(row: StoresNsn) -> dict:
    return {"nsn": row.nsn, "formatted": nsn.formatted(row.nsn), "itemType": row.item_type,
            "size": row.size, "addedBy": row.added_by, "addedAt": row.added_at.isoformat()}


@router.get("/stores/nsns")
def stores_nsns(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """The numbers staff have taught, and whether label photos can be read."""
    rows = db.query(StoresNsn).order_by(StoresNsn.item_type, StoresNsn.size, StoresNsn.nsn).all()
    return {"labelReading": label_reader.available(), "added": [_taught_dict(r) for r in rows]}


@router.get("/stores/nsns/{raw}")
def stores_nsn_lookup(raw: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    value = _nsn_or_400(raw)
    found = nsn.lookup(db, value)
    if not found:
        raise HTTPException(status_code=404, detail=f"{nsn.formatted(value)} isn't a stock number we know yet")
    return found


class TeachBody(BaseModel):
    itemType: str = Field(min_length=1, max_length=100)
    size: str = Field(default="", max_length=50)


@router.put("/stores/nsns/{raw}")
def stores_nsn_teach(raw: str, body: TeachBody, db: Session = Depends(get_db),
                     idinfo: dict = Depends(require_staff)):
    """Say what a number the form doesn't list is; again to correct it."""
    value = _nsn_or_400(raw)
    try:
        nsn.teach(db, value, body.itemType, body.size, actor_name(idinfo))
    except nsn.TeachError as e:
        status = 409 if "logs form" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))
    db.commit()
    logger.info("stock number %s taught as %s %s by %s", value, body.itemType, body.size, idinfo.get("email"))
    return nsn.lookup(db, value)


@router.delete("/stores/nsns/{raw}", status_code=204)
def stores_nsn_forget(raw: str, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    value = _nsn_or_400(raw)
    row = db.query(StoresNsn).filter(StoresNsn.nsn == value).first()
    if not row:
        raise HTTPException(status_code=404, detail="That number wasn't added by staff")
    db.delete(row)
    db.commit()


@router.post("/stores/nsns/read-label")
# Each read is a Gemini call on a free tier shared with the texts and the
# assistant, so a stuck retry loop mustn't eat the day's quota.
@limiter.limit("20/minute")
def stores_read_label(  # sync on purpose: the slow call runs in the threadpool
    request: Request,
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    """The stock number and size read off a label photo, and what that number
    means. The photo is read into memory, sent on and dropped."""
    if label_reader.base_type(photo.content_type) not in label_reader.IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Send a photo (JPEG, PNG, WebP or HEIC)")
    data = photo.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="That photo is too big. Try again closer to the label")
    if not data:
        raise HTTPException(status_code=400, detail="The photo was empty")
    try:
        read, model = label_reader.read_label(data, photo.content_type)
    except Exception as e:
        logger.error(f"label read failed: {e}")
        raise HTTPException(status_code=502,
                            detail="Couldn't read the label. Type the stock number, or choose the item")
    value = nsn.normalise(read["nsn"])
    return {
        "read": {**read, "nsn": value},
        "match": nsn.lookup(db, value) if value else None,
        "model": model,
        "modelLabel": label_reader.model_label(model),
    }


class BookInBody(BaseModel):
    """One scan: a stock number, or (for a label that wouldn't read) the item
    and size chosen by hand. A negative quantity undoes a booking."""
    nsn: str | None = Field(default=None, max_length=40)
    itemType: str | None = Field(default=None, max_length=100)
    size: str | None = Field(default=None, max_length=50)
    box: str = Field(min_length=1, max_length=50)
    section: str = Field(min_length=1, max_length=50)
    quantity: int = Field(default=1, ge=-MAX_BOOK_IN, le=MAX_BOOK_IN)


def _resolve_item(db: Session, body: BookInBody) -> tuple[str, str, dict | None]:
    if body.nsn:
        value = _nsn_or_400(body.nsn)
        found = nsn.lookup(db, value)
        if not found:
            raise HTTPException(status_code=404, detail=f"{nsn.formatted(value)} isn't a stock number we know yet")
        if not found["itemType"]:
            raise HTTPException(status_code=409,
                                detail=f"The logs form lists this as {found['description']}, "
                                       "which the stores don't keep")
        return found["itemType"], found["size"], found
    rules = catalogue_store.uniform_rules(db)
    if not body.itemType or body.itemType not in rules.known:
        raise HTTPException(status_code=400, detail="Scan a stock number, or choose an item")
    if body.itemType in rules.no_size:
        return body.itemType, nsn.NO_SIZE, None
    size = (body.size or "").strip()
    if not size:
        raise HTTPException(status_code=400, detail="Choose a size")
    return body.itemType, size, None


@router.post("/stores/stock/book-in")
def stores_book_in(body: BookInBody, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)):
    """Add the scanned item to a shelf section: onto its line there, or as a
    new line. A negative quantity takes a booking back off, never below 0."""
    if body.quantity == 0:
        raise HTTPException(status_code=422, detail="Book in at least 1")
    item_type, size, found = _resolve_item(db, body)

    box = db.query(StoresBox).filter(StoresBox.label == body.box.strip().upper()).first()
    if not box:
        raise HTTPException(status_code=404, detail="Box not found")
    section = next((s for s in box.sections if s.label == body.section.strip()), None)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    line = db.query(StoresItem).filter(
        StoresItem.item_type == item_type, StoresItem.size == size,
        StoresItem.box_id == box.id, StoresItem.section_id == section.id,
    ).first()
    by = actor_name(idinfo)
    scanned = f" by scanning {found['formatted']}" if found else ""

    if body.quantity < 0:
        if not line or line.quantity < -body.quantity:
            raise HTTPException(status_code=409, detail="There aren't that many on the shelf to take back off")
        line.quantity += body.quantity
        stock_changes.record_uniform(db, line, stock_changes.COUNTED, by, change=body.quantity,
                                     note="Booking in undone")
    elif line:
        line.quantity += body.quantity
        stock_changes.record_uniform(db, line, stock_changes.TOPPED_UP, by, change=body.quantity,
                                     note=f"Booked in{scanned}")
    else:
        line = StoresItem(item_type=item_type, size=size, quantity=body.quantity,
                          gender=catalogue_store.uniform_rules(db).gender.get(item_type, "unisex"),
                          box_id=box.id, section_id=section.id)
        db.add(line)
        stock_changes.record_uniform(db, line, stock_changes.ADDED, by, change=body.quantity,
                                     note=f"Booked in{scanned}")
    db.commit()
    db.refresh(line)
    logger.info("book in: %+d %s %s at %s/%s by %s", body.quantity, item_type, size,
                box.label, section.label, idinfo.get("email"))
    return {"item": _item_to_dict(line), "lookup": found}
