"""Push notification sign-up, for the SMS site's settings page.

A device (browser or installed app) is identified by its push endpoint. Turning
notifications on stores the endpoint with the topics chosen; turning them off
deletes it. The sending side is core/push.py.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from core import push
from core.config import VAPID_PUBLIC_KEY
from core.db import get_db
from core.security import get_user_role, require_staff_or_nco
from database.models import PushSubscription

logger = logging.getLogger(__name__)
router = APIRouter()


class Keys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class SubscriptionBody(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: Keys
    topics: list[str] = Field(default_factory=list, max_length=len(push.TOPICS))

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v: str) -> str:
        # Every real push service is https. Anything else would have us POSTing
        # to wherever the caller liked.
        if not v.startswith("https://"):
            raise ValueError("endpoint must be an https URL")
        return v


class EndpointBody(BaseModel):
    endpoint: str = Field(max_length=2000)


def _email(idinfo: dict) -> str:
    return idinfo["email"].lower()


def _own(db: Session, endpoint: str, email: str) -> PushSubscription | None:
    sub = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    return sub if sub and sub.email == email else None


@router.get("/push/config")
def push_config(idinfo: dict = Depends(require_staff_or_nco)):
    """Whether push is set up, the key a browser subscribes with, and the topics
    this person's role may choose."""
    role = get_user_role(idinfo["email"])
    return {
        "enabled": push.enabled(),
        "publicKey": VAPID_PUBLIC_KEY if push.enabled() else None,
        "topics": [
            {"key": key, "label": push.TOPICS[key].label, "description": push.TOPICS[key].description}
            for key in push.topics_for_role(role)
        ],
    }


@router.post("/push/subscriptions/lookup")
def push_lookup(body: EndpointBody, db: Session = Depends(get_db),
                idinfo: dict = Depends(require_staff_or_nco)):
    """The topics this device is signed up for. POST rather than GET because the
    endpoint is a long URL that doesn't belong in a query string or a log."""
    sub = _own(db, body.endpoint, _email(idinfo))
    return {"subscribed": sub is not None, "topics": sub.topics if sub else []}


@router.put("/push/subscriptions")
def push_subscribe(body: SubscriptionBody, request: Request, db: Session = Depends(get_db),
                   idinfo: dict = Depends(require_staff_or_nco)):
    """Sign this device up, or change its topics."""
    if not push.enabled():
        raise HTTPException(status_code=503, detail="Notifications aren't set up on the server")
    email = _email(idinfo)
    unknown = [t for t in body.topics if t not in push.TOPICS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown topic: {', '.join(unknown)}")
    allowed = push.topics_for_role(get_user_role(email))
    refused = [t for t in body.topics if t not in allowed]
    if refused:
        raise HTTPException(status_code=403, detail=f"Not available to your role: {', '.join(refused)}")

    sub = db.query(PushSubscription).filter(PushSubscription.endpoint == body.endpoint).first()
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint, created_at=datetime.now())
        db.add(sub)
    elif sub.email != email:
        # Same browser, different person signed in: the device is theirs now,
        # and the last person's notifications must stop arriving on it.
        logger.info("push: device moved from %s to %s", sub.email, email)
    sub.email = email
    sub.p256dh = body.keys.p256dh
    sub.auth = body.keys.auth
    sub.topics = [t for t in push.TOPICS if t in body.topics]  # de-duplicated, in TOPICS order
    sub.user_agent = (request.headers.get("user-agent") or "")[:300] or None
    db.commit()
    return {"subscribed": True, "topics": sub.topics}


@router.post("/push/subscriptions/delete", status_code=204)
def push_unsubscribe(body: EndpointBody, db: Session = Depends(get_db),
                     idinfo: dict = Depends(require_staff_or_nco)):
    """Turn notifications off for this device. Deleting one that is already gone
    is fine: the browser may have dropped it first."""
    sub = _own(db, body.endpoint, _email(idinfo))
    if sub:
        db.delete(sub)
        db.commit()
    return Response(status_code=204)


@router.post("/push/test")
def push_test(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff_or_nco)):
    """Send a test notification to each of the caller's devices."""
    if not push.enabled():
        raise HTTPException(status_code=503, detail="Notifications aren't set up on the server")
    email = _email(idinfo)
    if not db.query(PushSubscription).filter(PushSubscription.email == email).first():
        raise HTTPException(status_code=409, detail="Turn notifications on for this device first")
    sent = push.send_test(db, email)
    if not sent:
        raise HTTPException(status_code=502, detail="The push service didn't accept the message")
    return {"sent": sent}
