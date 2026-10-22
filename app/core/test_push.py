"""Sending push notifications: who gets them, and what happens when a push
service fails."""

from base64 import urlsafe_b64decode
from datetime import datetime, timedelta

import pytest
from py_vapid import Vapid02

from core import jobs, push
from database.models import Cadet, CadetQualification, PushSubscription
from scripts.gen_vapid_keys import generate


def add_device(db, name, email, topics):
    db.add(PushSubscription(endpoint=f"https://push.example/{name}", email=email, p256dh="k", auth="a",
                            topics=topics, created_at=datetime.now()))
    db.commit()


def to(pushes, name):
    return pushes.to(f"https://push.example/{name}")


def test_nothing_is_sent_while_push_is_switched_off(db, auth, monkeypatch):
    sent = []
    monkeypatch.setattr(push, "webpush", lambda *a, **k: sent.append(a))
    add_device(db, "phone", "staff@317atc.co.uk", ["orders"])
    assert push.notify("orders", "t", "b") == 0 and sent == []


def test_notify_reaches_devices_on_the_topic_and_stamps_them(db, auth, pushes):
    add_device(db, "on", "staff@317atc.co.uk", ["orders", "qualifications"])
    add_device(db, "off", "staff2@317atc.co.uk", ["qualifications"])
    add_device(db, "none", "owner@317atc.co.uk", [])

    assert push.notify("orders", "New order", "Two berets", "/stores/uniform/orders") == 1
    assert to(pushes, "on") == [{"title": "New order", "body": "Two berets",
                                 "url": "/stores/uniform/orders", "tag": "orders"}]
    assert to(pushes, "off") == [] and to(pushes, "none") == []
    # Held a day for a phone that's off, and never left waiting on a slow service.
    assert pushes.sent[0]["ttl"] == push.TTL_S and pushes.sent[0]["timeout"] == push.SEND_TIMEOUT_S
    db.expire_all()
    assert db.query(PushSubscription).filter_by(email="staff@317atc.co.uk").one().last_sent_at is not None


def test_someone_who_left_the_staff_group_stops_getting_staff_alerts(db, auth, pushes):
    # Signed up while staff; the group snapshot now has them as an NCO (or no one).
    add_device(db, "demoted", "nco@317atc.co.uk", ["orders"])
    add_device(db, "gone", "left@317atc.co.uk", ["orders"])
    add_device(db, "staff", "staff@317atc.co.uk", ["orders"])
    assert push.notify("orders", "t", "b") == 1
    assert [m["endpoint"] for m in pushes.sent] == ["https://push.example/staff"]


def test_emails_narrow_it_to_those_people_case_insensitively(db, auth, pushes):
    add_device(db, "author", "nco@317atc.co.uk", ["my_session_plans"])
    add_device(db, "other", "nco2@317atc.co.uk", ["my_session_plans"])
    assert push.notify("my_session_plans", "Approved", "Map reading", emails=["NCO@317atc.co.uk"]) == 1
    assert len(to(pushes, "author")) == 1 and to(pushes, "other") == []


def test_an_empty_email_list_sends_to_nobody(db, auth, pushes):
    add_device(db, "author", "nco@317atc.co.uk", ["my_session_plans"])
    assert push.notify("my_session_plans", "t", "b", emails=[]) == 0


def test_an_unknown_topic_sends_nothing(db, auth, pushes):
    add_device(db, "phone", "staff@317atc.co.uk", ["orders"])
    assert push.notify("gossip", "t", "b") == 0 and pushes.sent == []


@pytest.mark.parametrize("status", [404, 410])
def test_a_device_the_push_service_says_is_gone_is_removed(db, auth, pushes, status):
    add_device(db, "gone", "staff@317atc.co.uk", ["orders"])
    add_device(db, "fine", "staff2@317atc.co.uk", ["orders"])
    pushes.fail["https://push.example/gone"] = status
    assert push.notify("orders", "t", "b") == 1
    db.expire_all()
    assert [s.endpoint for s in db.query(PushSubscription).all()] == ["https://push.example/fine"]


@pytest.mark.parametrize("failure", [500, 429, TimeoutError("slow"), ValueError("bad key")])
def test_any_other_failure_keeps_the_device_and_carries_on_to_the_next(db, auth, pushes, failure):
    add_device(db, "flaky", "staff@317atc.co.uk", ["orders"])
    add_device(db, "fine", "staff2@317atc.co.uk", ["orders"])
    pushes.fail["https://push.example/flaky"] = failure
    assert push.notify("orders", "t", "b") == 1
    db.expire_all()
    assert db.query(PushSubscription).count() == 2


def test_notify_never_raises_even_if_the_database_does(auth, pushes, monkeypatch):
    class Broken:
        def query(self, *a):
            raise RuntimeError("db gone")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(push, "SessionLocal", Broken)
    assert push.notify("orders", "t", "b") == 0


def test_generated_keys_sign_a_real_push_request(monkeypatch):
    """The key format the script prints is the one pywebpush and browsers take:
    run the real webpush (encryption, VAPID signature) into a fake HTTP session."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from pywebpush import webpush

    from scripts.gen_vapid_keys import b64url

    public, private = generate()
    assert len(public) == 87 and len(private) == 43  # 65 and 32 bytes, base64url
    assert Vapid02.from_string(private).public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint) == \
        urlsafe_b64decode(public + "=")

    browser = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    posted = {}

    class Session:
        def post(self, url, data=None, headers=None, timeout=None):
            posted.update(url=url, data=data, headers=headers)
            return type("R", (), {"status_code": 201, "text": "", "headers": {}})()

    webpush({"endpoint": "https://push.example/real", "keys": {"p256dh": b64url(browser), "auth": b64url(b"0" * 16)}},
            data='{"title": "x"}', vapid_private_key=private, vapid_claims={"sub": "mailto:a@b.c"},
            requests_session=Session())
    assert posted["url"] == "https://push.example/real"
    assert posted["headers"]["authorization"].startswith("vapid t=") and public in posted["headers"]["authorization"]
    assert b'"title"' not in posted["data"]  # encrypted, not sent in the clear


def test_quali_expiry_alert_pushes_alongside_the_email(db, auth, pushes, outbox):
    now = datetime.now()
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able"))
    db.add_all([CadetQualification(cadet_id=1, qual_type=q, status="t", date_expires=now + timedelta(days=d))
                for q, d in (("First Aid", 10), ("Radio", 20), ("Swim", 30), ("Hike", 40))])
    db.commit()
    add_device(db, "staff", "staff@317atc.co.uk", ["qualifications"])

    jobs.quali_expiry_alert()
    assert len(outbox) == 1
    [msg] = to(pushes, "staff")
    assert msg["title"] == "4 qualifications expiring" and msg["url"] == "/stats"
    assert msg["body"] == "Amy Able (First Aid), Amy Able (Radio), Amy Able (Swim) and more"

    jobs.quali_expiry_alert()
    assert len(to(pushes, "staff")) == 1  # once per qualification, like the email


def test_topics_for_each_role():
    assert push.topics_for_role("staff") == list(push.TOPICS)
    assert push.topics_for_role("nco") == push.topics_for_role("snco") == ["my_session_plans"]
    assert push.topics_for_role(None) == [] and push.topics_for_role("cadet") == []
