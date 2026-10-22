"""Push notification sign-up over HTTP, and the events that send them."""

import pytest

import routers.session_plans as sp
import routers.stores as stores
from database.models import Cadet, PushSubscription

KEYS = {"p256dh": "BPubKey", "auth": "authsecret"}


def endpoint(name: str) -> str:
    return f"https://push.example/{name}"


def subscribe(api, persona, name, topics):
    res = api.put("/push/subscriptions", headers=api.as_(persona),
                  json={"endpoint": endpoint(name), "keys": KEYS, "topics": topics})
    assert res.status_code == 200, res.text
    return res.json()


# ── who may use it ────────────────────────────────────────────────────────────

ENDPOINTS = [
    ("get", "/push/config", None),
    ("post", "/push/subscriptions/lookup", {"endpoint": endpoint("x")}),
    ("put", "/push/subscriptions", {"endpoint": endpoint("x"), "keys": KEYS, "topics": []}),
    ("post", "/push/subscriptions/delete", {"endpoint": endpoint("x")}),
    ("post", "/push/test", None),
]


def _call(api, method, path, body, headers=None):
    kwargs = {"headers": headers or {}}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)(path, **kwargs)


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_every_push_endpoint_needs_a_token(api, pushes, method, path, body):
    assert _call(api, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_cadets_cannot_sign_up_for_notifications(api, pushes, method, path, body):
    assert _call(api, method, path, body, api.as_("cadet")).status_code == 403


@pytest.mark.parametrize("persona,topics", [
    ("staff", ["orders", "session_plans", "qualifications", "my_session_plans"]),
    ("owner", ["orders", "session_plans", "qualifications", "my_session_plans"]),
    ("snco", ["my_session_plans"]),
    ("nco", ["my_session_plans"]),
])
def test_config_offers_each_role_only_its_own_topics(api, pushes, persona, topics):
    res = api.get("/push/config", headers=api.as_(persona))
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True and body["publicKey"]
    assert [t["key"] for t in body["topics"]] == topics
    assert all(t["label"] and t["description"] for t in body["topics"])


def test_config_says_push_is_off_when_the_server_has_no_keys(api):
    body = api.get("/push/config", headers=api.as_("staff")).json()
    assert body["enabled"] is False and body["publicKey"] is None


@pytest.mark.parametrize("method,path", [("put", "/push/subscriptions"), ("post", "/push/test")])
def test_signing_up_without_server_keys_is_a_clear_503(api, method, path):
    body = {"endpoint": endpoint("x"), "keys": KEYS, "topics": []} if method == "put" else None
    res = _call(api, method, path, body, api.as_("staff"))
    assert res.status_code == 503 and "set up" in res.json()["detail"]


# ── signing up ────────────────────────────────────────────────────────────────

def test_a_device_signs_up_and_changes_its_topics(api, db, pushes):
    assert subscribe(api, "staff", "phone", ["qualifications", "orders", "orders"]) == {
        "subscribed": True, "topics": ["orders", "qualifications"],  # de-duplicated, in a fixed order
    }
    subscribe(api, "staff", "phone", ["session_plans"])

    rows = db.query(PushSubscription).all()
    assert len(rows) == 1  # same endpoint updates, never duplicates
    assert rows[0].email == "staff@317atc.co.uk" and rows[0].topics == ["session_plans"]
    assert rows[0].p256dh == "BPubKey" and rows[0].auth == "authsecret"

    lookup = api.post("/push/subscriptions/lookup", json={"endpoint": endpoint("phone")}, headers=api.as_("staff"))
    assert lookup.json() == {"subscribed": True, "topics": ["session_plans"]}


def test_a_device_with_no_topics_is_still_signed_up_for_tests(api, pushes):
    assert subscribe(api, "nco", "phone", []) == {"subscribed": True, "topics": []}
    assert api.post("/push/test", headers=api.as_("nco")).json() == {"sent": 1}


def test_an_nco_cannot_sign_up_for_staff_alerts(api, db, pushes):
    res = api.put("/push/subscriptions", headers=api.as_("nco"),
                  json={"endpoint": endpoint("p"), "keys": KEYS, "topics": ["my_session_plans", "orders"]})
    assert res.status_code == 403 and "orders" in res.json()["detail"]
    assert db.query(PushSubscription).count() == 0


def test_an_unknown_topic_is_a_400(api, pushes):
    res = api.put("/push/subscriptions", headers=api.as_("staff"),
                  json={"endpoint": endpoint("p"), "keys": KEYS, "topics": ["everything"]})
    assert res.status_code == 400 and "everything" in res.json()["detail"]


@pytest.mark.parametrize("body", [
    {"endpoint": "http://push.example/plain", "keys": KEYS, "topics": []},  # must be https
    {"endpoint": "file:///etc/passwd", "keys": KEYS, "topics": []},
    {"endpoint": endpoint("p"), "topics": []},  # no keys
    {"endpoint": endpoint("p"), "keys": {"p256dh": "", "auth": "a"}, "topics": []},
    {"endpoint": endpoint("p") + "x" * 2000, "keys": KEYS, "topics": []},
    {"keys": KEYS, "topics": []},
])
def test_a_malformed_subscription_is_a_422(api, pushes, body):
    assert api.put("/push/subscriptions", json=body, headers=api.as_("staff")).status_code == 422


def test_a_device_that_changes_hands_stops_notifying_the_last_person(api, db, pushes):
    # A shared laptop: Sam turned notifications on, then Sue signs in on it.
    subscribe(api, "staff", "laptop", ["orders"])
    subscribe(api, "staff2", "laptop", ["orders"])
    row = db.query(PushSubscription).one()
    assert row.email == "staff2@317atc.co.uk"
    lookup = api.post("/push/subscriptions/lookup", json={"endpoint": endpoint("laptop")}, headers=api.as_("staff"))
    assert lookup.json() == {"subscribed": False, "topics": []}


def test_lookup_of_an_unknown_device_says_not_subscribed(api, pushes):
    res = api.post("/push/subscriptions/lookup", json={"endpoint": endpoint("new")}, headers=api.as_("nco"))
    assert res.json() == {"subscribed": False, "topics": []}


def test_turning_off_deletes_only_your_own_device(api, db, pushes):
    subscribe(api, "staff", "mine", ["orders"])
    subscribe(api, "staff2", "theirs", ["orders"])

    # Someone else's endpoint is left alone, and nothing says whether it exists.
    res = api.post("/push/subscriptions/delete", json={"endpoint": endpoint("theirs")}, headers=api.as_("staff"))
    assert res.status_code == 204
    res = api.post("/push/subscriptions/delete", json={"endpoint": endpoint("mine")}, headers=api.as_("staff"))
    assert res.status_code == 204
    assert [s.endpoint for s in db.query(PushSubscription).all()] == [endpoint("theirs")]

    # Already gone (the browser dropped it first) is fine too.
    res = api.post("/push/subscriptions/delete", json={"endpoint": endpoint("mine")}, headers=api.as_("staff"))
    assert res.status_code == 204


# ── test notification ─────────────────────────────────────────────────────────

def test_test_notification_reaches_each_of_your_devices_only(api, pushes):
    subscribe(api, "staff", "phone", [])
    subscribe(api, "staff", "laptop", ["orders"])
    subscribe(api, "staff2", "other", ["orders"])
    assert api.post("/push/test", headers=api.as_("staff")).json() == {"sent": 2}
    assert {m["endpoint"] for m in pushes.sent} == {endpoint("phone"), endpoint("laptop")}
    assert pushes.sent[0]["payload"]["url"] == "/settings"


def test_test_notification_with_no_device_is_a_409(api, pushes):
    res = api.post("/push/test", headers=api.as_("snco"))
    assert res.status_code == 409 and "Turn notifications on" in res.json()["detail"]


def test_test_notification_the_push_service_refuses_is_a_502(api, pushes):
    subscribe(api, "staff", "phone", [])
    pushes.fail[endpoint("phone")] = 500
    assert api.post("/push/test", headers=api.as_("staff")).status_code == 502


# ── events that notify ────────────────────────────────────────────────────────

@pytest.fixture
def cadet(db):
    db.add(Cadet(cin=1, first_name="Cara", last_name="Ünal", email="cadet@317atc.co.uk", rank="Cdt"))
    db.commit()


def test_a_portal_uniform_order_notifies_staff_who_want_orders(api, pushes, cadet):
    subscribe(api, "staff", "stores", ["orders"])
    subscribe(api, "staff2", "quiet", ["qualifications"])
    subscribe(api, "nco", "nco", ["my_session_plans"])

    res = api.post("/cadets/me/orders", headers=api.as_("cadet"),
                   json={"items": [{"itemType": "Beret", "size": "55"}, {"itemType": "Tie"}]})
    assert res.status_code == 201
    assert pushes.to(endpoint("stores")) == [{
        "title": "New uniform order", "body": "Cdt Cara Ünal ordered 2 items",
        "url": "/stores/uniform/orders", "tag": "orders",
    }]
    assert pushes.to(endpoint("quiet")) == [] and pushes.to(endpoint("nco")) == []


def test_a_portal_badge_order_notifies_staff(api, pushes, cadet):
    subscribe(api, "staff", "stores", ["orders"])
    res = api.post("/cadets/me/badge-orders", headers=api.as_("cadet"), json={"items": [{"badgeName": "First Aid"}]})
    assert res.status_code == 201
    [msg] = pushes.to(endpoint("stores"))
    assert msg["title"] == "New badge order" and msg["body"].endswith("ordered 1 badge")
    assert msg["url"] == "/stores/badges/orders"


def test_a_staff_members_own_order_notifies_stores(api, pushes):
    subscribe(api, "staff", "stores", ["orders"])
    res = api.post("/users/me/orders", headers=api.as_("staff2"), json={"items": [{"itemType": "Beret"}]})
    assert res.status_code == 201
    assert pushes.to(endpoint("stores"))[0]["body"] == "Sue Second ordered 1 item"


def test_a_rejected_order_notifies_nobody(api, pushes, cadet):
    subscribe(api, "staff", "stores", ["orders"])
    assert api.post("/cadets/me/orders", headers=api.as_("cadet"), json={"items": []}).status_code == 400
    assert pushes.sent == []


def test_an_order_still_saves_when_the_push_service_is_down(api, db, pushes, cadet):
    subscribe(api, "staff", "stores", ["orders"])
    pushes.fail[endpoint("stores")] = TimeoutError("push service timed out")
    res = api.post("/cadets/me/orders", headers=api.as_("cadet"), json={"items": [{"itemType": "Tie"}]})
    assert res.status_code == 201
    assert db.query(PushSubscription).count() == 1  # a timeout isn't "gone"


def test_form_import_sends_one_summary_not_one_per_order(api, db, pushes, monkeypatch):
    monkeypatch.setattr(stores, "UNIFORM_FORM_API_KEY", "secret")
    db.add(Cadet(cin=5, first_name="Amy", last_name="Able", email="amy@317atc.co.uk"))
    db.commit()
    subscribe(api, "staff", "stores", ["orders"])
    rows = [{"email": "amy@317atc.co.uk", "items": [{"itemType": "Tie"}]}] * 2 + [{"email": "ghost@x"}]
    res = api.post("/stores/orders/form-import", json={"rows": rows}, headers={"X-Import-Key": "secret"})
    assert res.status_code == 201
    assert [m["body"] for m in pushes.to(endpoint("stores"))] == ["2 orders came in from the order form"]


def test_form_import_with_nothing_created_sends_nothing(api, pushes, monkeypatch):
    monkeypatch.setattr(stores, "UNIFORM_FORM_API_KEY", "secret")
    subscribe(api, "staff", "stores", ["orders"])
    api.post("/stores/orders/form-import", json={"rows": [{"email": "ghost@x"}]}, headers={"X-Import-Key": "secret"})
    assert pushes.sent == []


PLAN = {"session_name": "Map reading", "aim": "a", "situation": "s", "mission": "m", "execution": "e"}


def test_session_plan_review_round_trip_notifies_the_right_people(api, pushes, monkeypatch):
    monkeypatch.setattr(sp, "SESSION_PLAN_ALERT_EMAIL", "notify@317atc.co.uk")
    subscribe(api, "staff", "reviewer", ["session_plans"])
    subscribe(api, "nco", "author", ["my_session_plans"])
    subscribe(api, "nco2", "bystander", ["my_session_plans"])

    plan = api.post("/session-plans", json=PLAN, headers=api.as_("nco")).json()
    assert pushes.sent == []  # a draft is nobody's business yet
    api.post(f"/session-plans/{plan['id']}/submit", headers=api.as_("nco"))
    assert pushes.to(endpoint("reviewer")) == [{
        "title": "Session plan to review", "body": "Map reading by Nina Corporal",
        "url": f"/session-plans/{plan['id']}", "tag": "session_plans",
    }]

    api.post(f"/session-plans/{plan['id']}/request-amendments", json={"note": "More detail"},
             headers=api.as_("staff"))
    api.post(f"/session-plans/{plan['id']}/submit", headers=api.as_("nco"))
    api.post(f"/session-plans/{plan['id']}/approve", json={}, headers=api.as_("staff"))

    assert [m["title"] for m in pushes.to(endpoint("author"))] == ["Amendments needed", "Session plan approved"]
    assert [m["title"] for m in pushes.to(endpoint("reviewer"))] == ["Session plan to review",
                                                                    "Session plan resubmitted"]
    assert pushes.to(endpoint("bystander")) == []  # another NCO's plan is none of theirs
