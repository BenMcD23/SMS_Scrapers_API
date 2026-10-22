"""Web Push notifications to phones and the installed SMS app.

A notification belongs to a topic (new orders, session plans, …). Each device
someone turns notifications on for picks the topics it wants, and a topic says
which roles may have it, so an NCO's phone can never be signed up for staff
alerts. Roles are checked again when sending, from the same group snapshot the
role checks use, so someone who leaves the staff group stops getting staff
alerts without anyone tidying up their subscriptions.

Push is an extra, never the only channel: everything sent here also goes by
email. So ``notify`` never raises. A push service that is down or a key that is
wrong is logged and the request that triggered it carries on.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from pywebpush import WebPushException, webpush

from core.config import VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_SUBJECT
from core.security import get_roles_for_emails
from database.database import SessionLocal
from database.models import PushSubscription

logger = logging.getLogger(__name__)

STAFF = ("staff",)
EVERYONE = ("staff", "snco", "nco")

# A push service holds an undelivered message this long (seconds) for a phone
# that is off, then drops it. A day covers overnight without a stale "new
# order" turning up a week later.
TTL_S = 24 * 60 * 60
SEND_TIMEOUT_S = 10


@dataclass(frozen=True)
class Topic:
    label: str
    description: str
    roles: tuple[str, ...]


# The site lists these on its settings page in this order.
TOPICS: dict[str, Topic] = {
    "orders": Topic("New orders", "A cadet or staff member orders uniform or badges", STAFF),
    "session_plans": Topic("Session plans to review", "An NCO submits a session plan for approval", STAFF),
    "qualifications": Topic("Expiring qualifications", "Friday's list of qualifications expiring in 3 months", STAFF),
    "my_session_plans": Topic("My session plans", "Your session plan is approved or sent back", EVERYONE),
}


def enabled() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def topics_for_role(role: str | None) -> list[str]:
    return [key for key, topic in TOPICS.items() if role in topic.roles]


def _deliver(db, sub: PushSubscription, payload: str) -> bool:
    """Send one message. A subscription the push service says is gone (the app
    was uninstalled, or permission withdrawn) is deleted so we stop trying."""
    try:
        webpush(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
            ttl=TTL_S,
            timeout=SEND_TIMEOUT_S,
        )
    except WebPushException as e:
        if e.status_code in (404, 410):
            logger.info("push: subscription %s for %s has gone, removing it", sub.id, sub.email)
            db.delete(sub)
        else:
            logger.warning("push: send to %s failed (%s): %s", sub.email, e.status_code, e.message)
        return False
    except Exception as e:
        # Timeouts, DNS, a malformed key: one bad device mustn't stop the rest.
        logger.warning("push: send to %s failed: %s: %s", sub.email, type(e).__name__, e)
        return False
    sub.last_sent_at = datetime.now()
    return True


def notify(topic: str, title: str, body: str, url: str = "/", *, emails: list[str] | None = None) -> int:
    """Push to every device signed up for ``topic`` whose owner still holds a
    role allowed it. ``emails`` narrows it to those people, for a notification
    about their own work. Returns how many devices it reached.

    Opens its own session so routers can hand it to BackgroundTasks and the
    request isn't held up by the push services."""
    if not enabled():
        return 0
    if topic not in TOPICS:
        logger.error("push: unknown topic %r", topic)
        return 0
    allowed = set(TOPICS[topic].roles)
    wanted = {e.lower() for e in emails} if emails is not None else None
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": topic})

    db = SessionLocal()
    sent = 0
    try:
        # Topics are a JSON list, filtered here rather than in SQL so the same
        # code runs on SQLite (tests, dev server) and Postgres.
        subs = [s for s in db.query(PushSubscription).all()
                if topic in (s.topics or []) and (wanted is None or s.email in wanted)]
        if not subs:
            return 0
        roles = get_roles_for_emails(sorted({s.email for s in subs}))
        for sub in subs:
            if roles.get(sub.email) not in allowed:
                continue
            sent += _deliver(db, sub, payload)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("push: %s notification failed", topic)
    finally:
        db.close()
    logger.info("push: %s → %d device(s)", topic, sent)
    return sent


def send_test(db, email: str) -> int:
    """A test message to every device ``email`` has signed up, whatever its
    topics, so someone can check notifications reach their phone."""
    payload = json.dumps({"title": "317 SMS", "body": "Notifications are working on this device.",
                          "url": "/settings", "tag": "test"})
    subs = db.query(PushSubscription).filter(PushSubscription.email == email.lower()).all()
    sent = sum(_deliver(db, sub, payload) for sub in subs)
    db.commit()
    return sent
