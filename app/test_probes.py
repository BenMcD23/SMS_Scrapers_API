from fastapi.testclient import TestClient

from api import app

client = TestClient(app)


def test_liveness_probes_need_no_auth():
    assert client.get("/ping").json() == {"ok": True}
    assert client.get("/healthz").json() == {"ok": True}


def test_readiness_checks_the_database():
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "database": "ok"}


def test_health_requires_a_token():
    assert client.get("/health").status_code == 401
