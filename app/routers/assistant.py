"""Squadron assistant: staff chat about cadets, attendance, qualifications,
stores, events and the rest of the records in plain English (core/assistant.py
does the work), with charts and tables in the answers and chats saved to carry
on later.

Staff only. Chats belong to the account that started them: nobody else can
list, read, rename, rate or delete one, and a chat id someone else owns is a
404 so ids can't be probed. The answers draw on cadet records (dates of birth
and emails included) and go through a third-party model, so every question is
written to the audit trail with the tools it used. The owner's usage view
(GET /assistant/usage) is the one place anyone sees other people's questions,
and only the thumbs-down ones, which is how wrong answers get found.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import database.database as database
from core import assistant
from core.audit import record_audit
from core.db import get_db
from core.llm import PRIMARY_MODEL, model_label
from core.ratelimit import limiter
from core.security import require_owner, require_staff
from database.models import AssistantChat, AssistantMessage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["assistant"])

# Earlier messages sent to the model with each question: enough for "and B
# flight?" to make sense, without a long chat growing the prompt forever.
HISTORY_MESSAGES = 12
# Longest an earlier answer is quoted back to the model.
HISTORY_CHARS = 4000
TITLE_CHARS = 80
CHAT_LIST_LIMIT = 100
UNAVAILABLE = "The assistant is unavailable. Try again in a minute."


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # None starts a new chat.
    chat_id: int | None = None
    # The site's "Hide personal data" switch: keeps dates of birth and emails
    # out of this answer.
    hide_personal: bool = False


class ChatUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=TITLE_CHARS)
    pinned: bool | None = None


class Feedback(BaseModel):
    # 1 up, -1 down, None to take a rating back.
    value: int | None = Field(None, ge=-1, le=1)


def _owner(idinfo: dict) -> str:
    return idinfo["email"].strip().lower()


def _own_chat(db: Session, chat_id: int, idinfo: dict) -> AssistantChat:
    chat = db.get(AssistantChat, chat_id)
    if not chat or chat.email != _owner(idinfo):
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _message_json(m: AssistantMessage) -> dict:
    extra = m.extra or {}
    out = {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
    if m.role == "assistant":
        model = extra.get("model") or ""
        out.update(
            charts=extra.get("charts", []),
            tables=extra.get("tables", []),
            follow_ups=extra.get("follow_ups", []),
            cadets=extra.get("cadets", []),
            calls=extra.get("calls", []),
            model=model,
            model_label=model_label(model) if model else "",
            fallback=bool(model) and model != PRIMARY_MODEL,
            feedback=m.feedback,
        )
    return out


def _chat_json(chat: AssistantChat) -> dict:
    return {"id": chat.id, "title": chat.title, "pinned": bool(chat.pinned),
            "created_at": chat.created_at.isoformat(), "updated_at": chat.updated_at.isoformat()}


def _history(chat: AssistantChat | None) -> list[dict]:
    """The chat so far as the model reads it. An answer's charts and tables
    are named, so "make that a pie chart" knows what "that" was."""
    if not chat:
        return []
    out = []
    for m in chat.messages[-HISTORY_MESSAGES:]:
        content = m.content[:HISTORY_CHARS]
        extra = m.extra or {}
        shown = [f"chart \"{c['title']}\"" for c in extra.get("charts", [])] + \
                [f"table \"{t['title']}\"" for t in extra.get("tables", [])]
        if shown:
            content += f"\n[Shown with this answer: {', '.join(shown)}]"
        out.append({"role": m.role, "content": content})
    return out


def _save(db: Session, idinfo: dict, chat: AssistantChat | None, question: str,
          result: assistant.Result, personal: bool) -> dict:
    """Save a question and its answer (starting the chat if it's new), audit
    it, and return what both ask endpoints send back."""
    now = datetime.now()
    if chat is None:
        title = question if len(question) <= TITLE_CHARS else question[: TITLE_CHARS - 1].rstrip() + "…"
        chat = AssistantChat(email=_owner(idinfo), title=title, created_at=now, updated_at=now)
        db.add(chat)
    chat.updated_at = now
    asked = AssistantMessage(role="user", content=question, created_at=now)
    answered = AssistantMessage(role="assistant", content=result.answer, created_at=now, extra={
        "charts": result.charts, "tables": result.tables, "follow_ups": result.follow_ups,
        "cadets": result.cadets, "calls": result.calls, "model": result.model,
    })
    chat.messages.extend([asked, answered])
    db.commit()
    record_audit(db, idinfo, action="ask", resource="assistant", resource_id=chat.id,
                 detail={"question": question[:500], "tools": [c["tool"] for c in result.calls],
                         "personal": personal})
    return {"chat": _chat_json(chat), "question": _message_json(asked), "answer": _message_json(answered)}


def _question(body: Question) -> str:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Ask a question")
    return question


# ─── Chats ───────────────────────────────────────────────────────────────────

@router.get("/chats")
def list_chats(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> list[dict]:
    """The caller's own chats: pinned first, then most recently used."""
    chats = (db.query(AssistantChat).filter_by(email=_owner(idinfo))
             .order_by(AssistantChat.pinned.desc(), AssistantChat.updated_at.desc(), AssistantChat.id.desc())
             .limit(CHAT_LIST_LIMIT).all())
    return [_chat_json(c) for c in chats]


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    chat = _own_chat(db, chat_id, idinfo)
    return {**_chat_json(chat), "messages": [_message_json(m) for m in chat.messages]}


@router.patch("/chats/{chat_id}")
def update_chat(chat_id: int, body: ChatUpdate, db: Session = Depends(get_db),
                idinfo: dict = Depends(require_staff)) -> dict:
    """Rename or pin a chat. Neither counts as using it, so the list order by
    last use doesn't jump."""
    chat = _own_chat(db, chat_id, idinfo)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="Give the chat a name")
        chat.title = title
    if body.pinned is not None:
        chat.pinned = body.pinned
    db.commit()
    return _chat_json(chat)


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> None:
    db.delete(_own_chat(db, chat_id, idinfo))
    db.commit()


@router.put("/messages/{message_id}/feedback")
def rate_answer(message_id: int, body: Feedback, db: Session = Depends(get_db),
                idinfo: dict = Depends(require_staff)) -> dict:
    """Thumbs up or down on one of your own answers."""
    message = db.get(AssistantMessage, message_id)
    if not message or message.chat.email != _owner(idinfo):
        raise HTTPException(status_code=404, detail="Answer not found")
    if message.role != "assistant":
        raise HTTPException(status_code=400, detail="Only answers can be rated")
    if body.value == 0:
        raise HTTPException(status_code=422, detail="Rate 1 or -1, or clear it")
    message.feedback = body.value
    db.commit()
    return {"id": message.id, "feedback": message.feedback}


# ─── Asking ──────────────────────────────────────────────────────────────────

@router.post("/ask")
# Each question is up to five model calls on free tiers shared with the texts
# and appraisals, so a runaway client mustn't starve those.
@limiter.limit("15/minute")
def ask(request: Request, body: Question, db: Session = Depends(get_db),
        idinfo: dict = Depends(require_staff)) -> dict:
    """{chat, question, answer} in one go. A failed answer saves nothing, so
    the question can simply be asked again."""
    question = _question(body)
    chat = _own_chat(db, body.chat_id, idinfo) if body.chat_id is not None else None
    try:
        result = assistant.ask(db, question, _history(chat), personal=not body.hide_personal)
    except RuntimeError as e:
        logger.error(f"assistant failed for {idinfo['email']}: {e}")
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    return _save(db, idinfo, chat, question, result, not body.hide_personal)


@router.post("/ask/stream")
@limiter.limit("15/minute")
def ask_stream(request: Request, body: Question, db: Session = Depends(get_db),
               idinfo: dict = Depends(require_staff)) -> StreamingResponse:
    """The same as /ask, but as it works: newline-delimited JSON, one
    {"type": "step", "label"} per thing it does, then {"type": "done", chat,
    question, answer} or {"type": "error", "detail"}.

    Who's asking and whose chat it is are checked before the stream starts,
    so those are still ordinary 401/403/404/422s. The stream does its own
    reading and saving on its own session: the request's is closed once the
    response starts."""
    question = _question(body)
    chat_id = _own_chat(db, body.chat_id, idinfo).id if body.chat_id is not None else None
    personal = not body.hide_personal

    def events():
        def line(event: dict) -> str:
            return json.dumps(event, default=str) + "\n"

        with database.SessionLocal() as session:
            chat = session.get(AssistantChat, chat_id) if chat_id is not None else None
            try:
                for event in assistant.ask_steps(session, question, _history(chat), personal=personal):
                    if isinstance(event, assistant.Step):
                        yield line({"type": "step", "label": event.label})
                    else:
                        yield line({"type": "done", **_save(session, idinfo, chat, question, event, personal)})
            except RuntimeError as e:
                logger.error(f"assistant failed for {idinfo['email']}: {e}")
                yield line({"type": "error", "detail": UNAVAILABLE})
            except Exception:
                logger.exception("assistant stream failed")
                yield line({"type": "error", "detail": "The assistant hit a problem. Try asking again."})

    # no-transform/X-Accel-Buffering: a proxy that buffers would hold every
    # step back until the answer, which is the one thing streaming is for.
    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# ─── Usage (owner) ───────────────────────────────────────────────────────────

@router.get("/usage")
def usage(days: int = 30, db: Session = Depends(get_db), idinfo: dict = Depends(require_owner)) -> dict:
    """How the assistant is being used: questions a day, who asks, which tools
    it reaches for, how often the backup model answered, and the ratings, with
    the thumbs-down answers listed so wrong ones can be looked at."""
    days = max(1, min(days, 365))
    since = datetime.now() - timedelta(days=days)
    messages = (db.query(AssistantMessage).join(AssistantChat)
                .filter(AssistantMessage.created_at >= since).order_by(AssistantMessage.id).all())
    questions = [m for m in messages if m.role == "user"]
    answers = [m for m in messages if m.role == "assistant"]
    per_day = Counter(m.created_at.date().isoformat() for m in questions)
    tools = Counter(c.get("tool") for a in answers for c in (a.extra or {}).get("calls", []))
    down = [a for a in answers if a.feedback == -1]

    def asked(answer: AssistantMessage) -> str:
        # The question is the user message just before it in the same chat.
        before = [m for m in answer.chat.messages if m.id < answer.id and m.role == "user"]
        return before[-1].content if before else ""

    return {
        "days": days,
        "questions": len(questions),
        "people": len({m.chat.email for m in questions}),
        "per_day": [{"date": d, "questions": n} for d, n in sorted(per_day.items())],
        "by_person": [{"email": e, "questions": n} for e, n in
                      Counter(m.chat.email for m in questions).most_common()],
        "tools": [{"tool": t, "calls": n} for t, n in tools.most_common()],
        "fallback": sum(1 for a in answers if (a.extra or {}).get("model") not in (None, "", PRIMARY_MODEL)),
        "thumbs_up": sum(1 for a in answers if a.feedback == 1),
        "thumbs_down": len(down),
        "rated_down": [{
            "date": a.created_at.isoformat(), "email": a.chat.email,
            "question": asked(a),
            "answer": a.content[:500],
        } for a in reversed(down[-20:])],
    }
