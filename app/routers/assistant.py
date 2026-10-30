"""Squadron assistant: staff chat about cadets, attendance, qualifications,
stores and events in plain English (core/assistant.py does the work), with
charts and tables in the answers and chats saved to carry on later.

Staff only. Chats belong to the account that started them: nobody else can
list, read or delete one, and a chat id someone else owns is a 404 so ids
can't be probed. The answers draw on cadet records (dates of birth and emails
included) and go through a third-party model, so every question is written to
the audit trail with the tools it used.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core import assistant
from core.audit import record_audit
from core.db import get_db
from core.llm import PRIMARY_MODEL, model_label
from core.ratelimit import limiter
from core.security import require_staff
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


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # None starts a new chat.
    chat_id: int | None = None
    # The site's "Hide personal data" switch: keeps dates of birth and emails
    # out of this answer.
    hide_personal: bool = False


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
            calls=extra.get("calls", []),
            model=model,
            model_label=model_label(model) if model else "",
            fallback=bool(model) and model != PRIMARY_MODEL,
        )
    return out


def _chat_json(chat: AssistantChat) -> dict:
    return {"id": chat.id, "title": chat.title, "created_at": chat.created_at.isoformat(),
            "updated_at": chat.updated_at.isoformat()}


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


@router.get("/chats")
def list_chats(db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> list[dict]:
    """The caller's own chats, most recently used first."""
    chats = (db.query(AssistantChat).filter_by(email=_owner(idinfo))
             .order_by(AssistantChat.updated_at.desc(), AssistantChat.id.desc())
             .limit(CHAT_LIST_LIMIT).all())
    return [_chat_json(c) for c in chats]


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> dict:
    chat = _own_chat(db, chat_id, idinfo)
    return {**_chat_json(chat), "messages": [_message_json(m) for m in chat.messages]}


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: int, db: Session = Depends(get_db), idinfo: dict = Depends(require_staff)) -> None:
    db.delete(_own_chat(db, chat_id, idinfo))
    db.commit()


@router.post("/ask")
# Each question is up to five model calls on free tiers shared with the texts
# and appraisals, so a runaway client mustn't starve those.
@limiter.limit("15/minute")
def ask(request: Request, body: Question, db: Session = Depends(get_db),
        idinfo: dict = Depends(require_staff)) -> dict:
    """{chat: {id, title, ...}, question: message, answer: message}. A failed
    answer saves nothing, so the question can simply be asked again."""
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Ask a question")
    chat = _own_chat(db, body.chat_id, idinfo) if body.chat_id is not None else None

    try:
        result = assistant.ask(db, question, _history(chat), personal=not body.hide_personal)
    except RuntimeError as e:
        logger.error(f"assistant failed for {idinfo['email']}: {e}")
        raise HTTPException(status_code=503, detail="The assistant is unavailable. Try again in a minute.")

    now = datetime.now()
    if chat is None:
        title = question if len(question) <= TITLE_CHARS else question[: TITLE_CHARS - 1].rstrip() + "…"
        chat = AssistantChat(email=_owner(idinfo), title=title, created_at=now, updated_at=now)
        db.add(chat)
    chat.updated_at = now
    asked = AssistantMessage(role="user", content=question, created_at=now)
    answered = AssistantMessage(role="assistant", content=result.answer, created_at=now, extra={
        "charts": result.charts, "tables": result.tables, "follow_ups": result.follow_ups,
        "calls": result.calls, "model": result.model,
    })
    chat.messages.extend([asked, answered])
    db.commit()

    record_audit(db, idinfo, action="ask", resource="assistant", resource_id=chat.id,
                 detail={"question": question[:500], "tools": [c["tool"] for c in result.calls],
                         "personal": not body.hide_personal})
    return {"chat": _chat_json(chat), "question": _message_json(asked), "answer": _message_json(answered)}
