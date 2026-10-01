"""Shared pytest fixtures for the API suite.

Every HTTP-level test runs against a fresh in-memory SQLite database and a fake
Google: tokens are mapped to identities here and roles come from a fixed group
snapshot, so the real `require_*` dependencies still run and the role gates are
exercised rather than overridden away.

Use them like:

    def test_x(api, db):
        db.add(Cadet(cin=1, first_name="A", last_name="B")); db.commit()
        res = api.get("/cadets", headers=api.as_("staff"))
        assert res.status_code == 200
"""

import os
import sys

# Importing the app reads these at module level (crypto refuses to import
# without a key). setdefault so CI's own values win; locally a bare `pytest`
# just works.
os.environ.setdefault("ENCRYPTION_KEY", "5-6ZgtVJdBRAJfCPIhLdyUFqGgb0ChZfBGB4fL0jTOo=")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# The scheduler would start real background jobs under TestClient's lifespan.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import core.security as security  # noqa: E402
import database.database as database  # noqa: E402
from core.db import get_db  # noqa: E402
from database.database import Base  # noqa: E402

DOMAIN = "317atc.co.uk"
OWNER = f"ci.mcdonald@{DOMAIN}"
OC = f"oc@{DOMAIN}"

# token -> identity. The token *is* the persona name, so a test reads as
# `headers=api.as_("nco")`. "owner" and "oc" are staff too, as in real life.
PERSONAS = {
    "staff":  {"email": f"staff@{DOMAIN}",  "given_name": "Sam",   "family_name": "Staff"},
    "staff2": {"email": f"staff2@{DOMAIN}", "given_name": "Sue",   "family_name": "Second"},
    "snco":   {"email": f"snco@{DOMAIN}",   "given_name": "Sid",   "family_name": "Senior"},
    "nco":    {"email": f"nco@{DOMAIN}",    "given_name": "Nina",  "family_name": "Corporal"},
    "nco2":   {"email": f"nco2@{DOMAIN}",   "given_name": "Ned",   "family_name": "Other"},
    "cadet":  {"email": f"cadet@{DOMAIN}",  "given_name": "Cara",  "family_name": "Cadet"},
    "cadet2": {"email": f"cadet2@{DOMAIN}", "given_name": "Carl",  "family_name": "Cadet"},
    "owner":  {"email": OWNER,              "given_name": "Ben",   "family_name": "Owner"},
    "oc":     {"email": OC,                 "given_name": "Olive", "family_name": "Commanding"},
}
GROUPS = {
    "staff": {PERSONAS[p]["email"] for p in ("staff", "staff2", "owner", "oc")},
    "snco": {PERSONAS["snco"]["email"]},
    "nco": {PERSONAS[p]["email"] for p in ("nco", "nco2")},
}


def idinfo(persona: str) -> dict:
    p = PERSONAS[persona]
    return {**p, "sub": f"sub-{persona}", "email_verified": True, "hd": DOMAIN}


def _fake_verify_token(authorization):
    # Mirrors the real function's contract: no/garbled header is 401, an
    # unknown token is 401 — so "no auth" tests mean the same thing here.
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    persona = authorization.split(" ", 1)[1]
    if persona not in PERSONAS:
        raise HTTPException(status_code=401, detail="Invalid Token")
    return idinfo(persona)


@pytest.fixture
def engine():
    # StaticPool: one connection shared by every session, otherwise each new
    # connection to sqlite:// would see its own empty database.
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine, monkeypatch):
    factory = sessionmaker(bind=engine)
    # Background tasks and jobs open their own SessionLocal() rather than using
    # the request's session — point every module's copy at the test database.
    original = database.SessionLocal
    for mod in list(sys.modules.values()):
        if getattr(mod, "SessionLocal", None) is original:
            monkeypatch.setattr(mod, "SessionLocal", factory)
        # Scraper runs open Session(engine) directly.
        if getattr(mod, "engine", None) is database.engine and mod is not database:
            monkeypatch.setattr(mod, "engine", engine)
    return factory


@pytest.fixture
def db(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def auth(monkeypatch):
    """Fake Google: token verification and group membership."""
    monkeypatch.setattr(security, "verify_token", _fake_verify_token)
    monkeypatch.setattr(security, "_groups", lambda unknown=False: GROUPS)
    monkeypatch.setattr(security, "OWNER_EMAIL", OWNER)
    monkeypatch.setattr(security, "OC_EMAIL", OC)
    monkeypatch.delenv("DEV_FAKE_AUTH", raising=False)


@pytest.fixture
def api(auth, session_factory):
    from api import app

    def _get_db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    client = TestClient(app)
    client.as_ = lambda persona: {"Authorization": f"Bearer {persona}"}
    yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """Every email the code under test tries to send, instead of sending it.

    Autouse so no test can ever reach Gmail. Routers import send_email by name,
    so each module's copy is swapped, not just core.emailer's."""
    import core.emailer as emailer

    sent: list[dict] = []

    def fake_send_email(to, subject, html_body, attachment=None, attachment_filename="attachment.pdf",
                        attachments=None, reply_to=None):
        sent.append({"to": to, "subject": subject, "html": html_body, "attachment": attachment,
                     "attachment_filename": attachment_filename, "attachments": attachments,
                     "reply_to": reply_to})

    original = emailer.send_email
    for mod in list(sys.modules.values()):
        if getattr(mod, "send_email", None) is original:
            monkeypatch.setattr(mod, "send_email", fake_send_email)
    return sent


@pytest.fixture(autouse=True)
def _fresh_cache():
    """core.cache is process-global; a list cached by one test must not leak
    into the next one's fresh database."""
    from core import cache

    cache._store.clear()
    yield
    cache._store.clear()


class FakeHttp:
    """Answers outbound httpx.AsyncClient calls from a handler instead of the
    network. ``handler(request) -> httpx.Response``; every request is kept in
    ``calls`` so a test can assert on what was sent."""

    def __init__(self):
        self.calls = []
        self.handler = lambda request: httpx.Response(500, text="no handler set")

    def __call__(self, request):
        self.calls.append(request)
        return self.handler(request)


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(*args, transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return fake


class FakeGitHub:
    """Just enough of the GitHub contents + git-data API for the newsletter and
    programme updaters: files per repo, and a log of every commit made."""

    def __init__(self):
        self.files: dict[str, dict[str, bytes]] = {}
        self.commits: list[dict] = []
        self._blobs: dict[str, bytes] = {}
        self._trees: dict[str, list] = {}
        self._pending: dict[str, dict] = {}
        self.fail: dict[str, int] = {}  # step name -> status code to return

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import base64
        import json as _json

        parts = request.url.path.split("/")
        repo = "/".join(parts[2:4])
        rest = "/".join(parts[4:])
        files = self.files.setdefault(repo, {})

        def step(name, ok):
            code = self.fail.get(name)
            return httpx.Response(code, text=f"{name} failed") if code else ok()

        if rest.startswith("contents/"):
            path = rest[len("contents/"):]
            if path in files:
                if "raw" in request.headers.get("accept", ""):
                    return httpx.Response(200, content=files[path])
                return httpx.Response(200, json={"content": base64.b64encode(files[path]).decode()})
            children = sorted({p[len(path) + 1:].split("/")[0] for p in files if p.startswith(path + "/")})
            if children:
                return httpx.Response(200, json=[{"name": c} for c in children])
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and rest.startswith("git/ref/heads/"):
            return step("ref", lambda: httpx.Response(200, json={"object": {"sha": f"head-{len(self.commits)}"}}))
        if request.method == "GET" and rest.startswith("git/commits/"):
            return step("base", lambda: httpx.Response(200, json={"tree": {"sha": "base-tree"}}))
        body = _json.loads(request.content or b"{}")
        if rest == "git/blobs":
            sha = f"blob-{len(self._blobs)}"
            self._blobs[sha] = base64.b64decode(body["content"])
            return step("blob", lambda: httpx.Response(201, json={"sha": sha}))
        if rest == "git/trees":
            sha = f"tree-{len(self._trees)}"
            self._trees[sha] = body["tree"]
            return step("tree", lambda: httpx.Response(201, json={"sha": sha}))
        if rest == "git/commits":
            sha = f"commit-{len(self._pending)}"
            self._pending[sha] = {"repo": repo, "message": body["message"], "tree": self._trees[body["tree"]]}
            return step("commit", lambda: httpx.Response(201, json={"sha": sha}))
        if request.method == "PATCH" and rest.startswith("git/refs/heads/"):
            def apply():
                commit = self._pending[_json.loads(request.content)["sha"]]
                for entry in commit["tree"]:
                    if entry["sha"] is None:
                        files.pop(entry["path"], None)
                    else:
                        files[entry["path"]] = self._blobs[entry["sha"]]
                self.commits.append({"repo": repo, "message": commit["message"],
                                     "paths": [e["path"] for e in commit["tree"] if e["sha"]],
                                     "deleted": [e["path"] for e in commit["tree"] if e["sha"] is None]})
                return httpx.Response(200, json={})
            return step("update_ref", apply)
        return httpx.Response(404, json={"message": f"unhandled {request.method} {rest}"})


@pytest.fixture
def github(http):
    fake = FakeGitHub()
    http.handler = fake
    return fake
