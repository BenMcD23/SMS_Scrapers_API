import httpx
from fastapi.testclient import TestClient

from api import app
from core.security import require_staff
from routers import chat

client = TestClient(app)


def test_chat_requires_a_token():
    assert client.post("/chat/ask", json={"question": "hi"}).status_code == 401


def test_chat_forwards_to_the_chatbot(monkeypatch):
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, json=json)
        return httpx.Response(200, json={"answer": "Yes [1].", "sources": []})

    monkeypatch.setattr(chat.httpx, "post", fake_post)
    app.dependency_overrides[require_staff] = lambda: {"email": "staff@317atc.co.uk"}
    try:
        res = client.post("/chat/ask", json={"question": "  Can cadets sleep at the centre? "})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    assert res.json()["answer"] == "Yes [1]."
    assert sent["url"].endswith("/ask")
    assert sent["json"] == {"question": "Can cadets sleep at the centre?"}


def test_chat_unreachable_is_503(monkeypatch):
    def fake_post(*a, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(chat.httpx, "post", fake_post)
    app.dependency_overrides[require_staff] = lambda: {"email": "staff@317atc.co.uk"}
    try:
        res = client.post("/chat/ask", json={"question": "anything"})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 503
