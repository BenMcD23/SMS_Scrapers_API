"""Parade-night text generation — the batch, the stream and the single-night calls.

Nothing here touches the network: the programme parser and the LLM call are both
stubbed, so what's under test is the concurrency, the ordering the stream relies
on, and which fields each entry point is allowed to overwrite.
"""
import asyncio
import json
import threading
import time
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.database import Base
from database.models import ParadeNightMessage
import routers.texts as tx


STAFF = {"sub": "staff", "email": "staff@317atc.co.uk"}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _night(day: int) -> dict:
    return {
        "date": datetime(2026, 9, day),
        "uniform": "No.3 SD",
        "dnco": f"Cdt {day}",
        "c_flight": f"c {day}",
        "main_body": f"main {day}",
    }


def _stub_programme(monkeypatch, nights: list[dict]):
    monkeypatch.setattr(tx, "parse_programme", lambda month, year: nights)


def _stub_ai(monkeypatch, fn):
    monkeypatch.setattr(tx, "generate_message", fn)


def _writer(delays: dict[str, float] | None = None, failures: set[str] = frozenset()):
    """A stand-in for the LLM call that answers from the prompt it was handed, so a
    test can tell which night each result belongs to, and records how many calls
    were ever in flight at once."""
    state = {"inflight": 0, "peak": 0}
    lock = threading.Lock()

    def generate(main_body: str, c_flight: str):
        with lock:
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
        try:
            time.sleep((delays or {}).get(main_body, 0.05))
            if main_body in failures:
                raise RuntimeError(f"model refused {main_body}")
            return f"MAIN {main_body}", f"C {c_flight}", "nvidia/nemotron-3-ultra-550b-a55b"
        finally:
            with lock:
                state["inflight"] -= 1

    return generate, state


def _events(response) -> list[dict]:
    """Drain an SSE StreamingResponse into the decoded events it carried."""
    async def collect():
        return "".join([chunk async for chunk in response.body_iterator])

    body = asyncio.run(collect())
    return [json.loads(frame[len("data: "):]) for frame in body.split("\n\n") if frame.strip()]


# ─── The batch ────────────────────────────────────────────────────────────────

def test_batch_generates_every_night_concurrently(db, monkeypatch):
    nights = [_night(d) for d in (2, 4, 9, 11, 16, 18)]
    _stub_programme(monkeypatch, nights)
    generate, state = _writer()
    _stub_ai(monkeypatch, generate)

    result = asyncio.run(tx.generate_messages(month=9, year=2026, db=db, idinfo=STAFF))

    assert result["generated"] == 6
    assert result["failed"] == 0
    assert result["models_used"] == [
        {"model": "nvidia/nemotron-3-ultra-550b-a55b", "label": "Nemotron 3 Ultra",
         "count": 6, "fallback": False}
    ]
    # The point of the rewrite: nights are written several at a time, capped at
    # AI_CONCURRENCY so the free tier's per-minute limit still has headroom.
    assert 1 < state["peak"] <= tx.AI_CONCURRENCY

    rows = db.query(ParadeNightMessage).order_by(ParadeNightMessage.parade_date).all()
    assert [r.main_message for r in rows] == [f"MAIN main {d}" for d in (2, 4, 9, 11, 16, 18)]
    assert [r.status for r in rows] == ["draft"] * 6
    # The uniform line is derived from the programme, not written by the model.
    assert rows[0].uniform == "No.3 SD (MTP/DPM)"


def test_sent_nights_are_left_alone(db, monkeypatch):
    db.add(ParadeNightMessage(parade_date=datetime(2026, 9, 2), main_message="already gone",
                              status="sent", generated_at=datetime.now()))
    db.commit()
    _stub_programme(monkeypatch, [_night(2), _night(4)])
    generate, _ = _writer()
    _stub_ai(monkeypatch, generate)

    result = asyncio.run(tx.generate_messages(month=9, year=2026, db=db, idinfo=STAFF))

    assert (result["generated"], result["skipped_sent"]) == (1, 1)
    sent = db.query(ParadeNightMessage).filter_by(parade_date=datetime(2026, 9, 2)).one()
    assert sent.main_message == "already gone"


def test_one_bad_night_does_not_sink_the_batch(db, monkeypatch):
    _stub_programme(monkeypatch, [_night(2), _night(4), _night(9)])
    generate, _ = _writer(failures={"main 4"})
    _stub_ai(monkeypatch, generate)

    result = asyncio.run(tx.generate_messages(month=9, year=2026, db=db, idinfo=STAFF))

    assert (result["generated"], result["failed"]) == (2, 1)
    assert db.query(ParadeNightMessage).count() == 2


def test_every_night_failing_is_an_error_not_an_empty_success(db, monkeypatch):
    _stub_programme(monkeypatch, [_night(2), _night(4)])
    generate, _ = _writer(failures={"main 2", "main 4"})
    _stub_ai(monkeypatch, generate)

    with pytest.raises(tx.HTTPException) as exc:
        asyncio.run(tx.generate_messages(month=9, year=2026, db=db, idinfo=STAFF))
    assert exc.value.status_code == 502


# ─── The stream ───────────────────────────────────────────────────────────────

def test_stream_sends_each_night_as_it_lands(monkeypatch):
    nights = [_night(2), _night(4), _night(9)]
    _stub_programme(monkeypatch, nights)
    # The last night answers first, so completion order and date order differ —
    # the stream must follow completion order, which is what puts a finished
    # night on screen instead of waiting for the slowest one.
    generate, _ = _writer(delays={"main 2": 0.30, "main 4": 0.20, "main 9": 0.01})
    _stub_ai(monkeypatch, generate)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(tx, "SessionLocal", sessionmaker(bind=engine))

    response = asyncio.run(tx.generate_messages_stream(month=9, year=2026, idinfo=STAFF))
    assert response.media_type == "text/event-stream"
    events = _events(response)

    assert events[0]["type"] == "start"
    assert events[0]["parade_dates"] == [n["date"].isoformat() for n in nights]
    assert [e["message"]["parade_date"][:10] for e in events if e["type"] == "message"] == [
        "2026-09-09", "2026-09-04", "2026-09-02",
    ]
    assert events[-1]["type"] == "done"
    assert events[-1]["generated"] == 3


def test_stream_reports_a_failed_night_without_dropping_the_rest(monkeypatch):
    _stub_programme(monkeypatch, [_night(2), _night(4)])
    generate, _ = _writer(failures={"main 2"})
    _stub_ai(monkeypatch, generate)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(tx, "SessionLocal", sessionmaker(bind=engine))

    events = _events(asyncio.run(tx.generate_messages_stream(month=9, year=2026, idinfo=STAFF)))

    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["parade_date"] == "2026-09-02T00:00:00"
    assert events[-1] == {"type": "done", "status": "success", "generated": 1,
                          "skipped_sent": 0, "failed": 1,
                          "models_used": events[-1]["models_used"]}


# ─── One night at a time ──────────────────────────────────────────────────────

def test_regenerate_can_rewrite_one_message_and_keep_the_other(db, monkeypatch):
    db.add(ParadeNightMessage(parade_date=datetime(2026, 9, 2), main_body_raw="main 2",
                              c_flight_raw="c 2", uniform_raw="No.3 SD", uniform="hand edited",
                              main_message="hand edited main", c_flight_message="old c",
                              generated_at=datetime.now()))
    db.commit()
    generate, _ = _writer()
    _stub_ai(monkeypatch, generate)
    message_id = db.query(ParadeNightMessage).one().id

    result = asyncio.run(tx.regenerate_message(
        message_id, tx.RegenerateBody(part="c_flight"), db=db, idinfo=STAFF))

    assert result["c_flight_message"] == "C c 2"
    # Only the requested half moves — the edited main message and uniform stand.
    assert result["main_message"] == "hand edited main"
    assert result["uniform"] == "hand edited"

    # No body at all is the whole message, uniform included.
    result = asyncio.run(tx.regenerate_message(message_id, None, db=db, idinfo=STAFF))
    assert result["main_message"] == "MAIN main 2"
    assert result["uniform"] == "No.3 SD (MTP/DPM)"


def test_generate_night_rereads_the_programme_for_one_date(db, monkeypatch):
    # The programme has been edited since the batch ran.
    changed = _night(2) | {"main_body": "main 2 revised", "dnco": "Cdt New"}
    _stub_programme(monkeypatch, [changed, _night(4)])
    generate, state = _writer()
    _stub_ai(monkeypatch, generate)

    body = tx.GenerateNightBody(parade_date="2026-09-02")
    result = asyncio.run(tx.generate_night(body, db=db, idinfo=STAFF))

    # Created from nothing, with the revised programme text, and only that night.
    assert result["main_message"] == "MAIN main 2 revised"
    assert result["dnco"] == "Cdt New"
    assert state["peak"] == 1
    assert db.query(ParadeNightMessage).count() == 1


def test_generate_night_rejects_a_date_that_is_not_a_parade_night(db, monkeypatch):
    _stub_programme(monkeypatch, [_night(2)])
    generate, _ = _writer()
    _stub_ai(monkeypatch, generate)

    with pytest.raises(tx.HTTPException) as exc:
        asyncio.run(tx.generate_night(tx.GenerateNightBody(parade_date="2026-09-03"),
                                      db=db, idinfo=STAFF))
    assert exc.value.status_code == 404


def test_a_sent_night_cannot_be_regenerated(db, monkeypatch):
    db.add(ParadeNightMessage(parade_date=datetime(2026, 9, 2), status="sent",
                              main_body_raw="main 2", generated_at=datetime.now()))
    db.commit()
    _stub_programme(monkeypatch, [_night(2)])
    generate, _ = _writer()
    _stub_ai(monkeypatch, generate)
    message_id = db.query(ParadeNightMessage).one().id

    for call in (
        lambda: tx.regenerate_message(message_id, None, db=db, idinfo=STAFF),
        lambda: tx.generate_night(tx.GenerateNightBody(parade_date="2026-09-02"),
                                  db=db, idinfo=STAFF),
    ):
        with pytest.raises(tx.HTTPException) as exc:
            asyncio.run(call())
        assert exc.value.status_code == 400
