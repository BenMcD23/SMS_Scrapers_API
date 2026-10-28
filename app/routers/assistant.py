"""Squadron assistant: staff ask about cadets, attendance, qualifications,
stores and events in plain English (core/assistant.py does the work).

Staff only. The answers draw on cadet records and go through a third-party
model, so every question is written to the audit trail with the tools it used.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core import assistant
from core.audit import record_audit
from core.db import get_db
from core.llm import PRIMARY_MODEL, model_label
from core.ratelimit import limiter
from core.security import require_staff

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["assistant"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Earlier turns of this chat, oldest first, so "and in B flight?" works.
    # Capped so a long chat can't grow the prompt without limit.
    history: list[Turn] = Field(default_factory=list, max_length=12)


@router.post("/ask")
# Each question is up to four model calls on free tiers shared with the texts
# and appraisals, so a runaway client mustn't starve those.
@limiter.limit("15/minute")
def ask(request: Request, body: Question, db: Session = Depends(get_db),
        idinfo: dict = Depends(require_staff)) -> dict:
    """{answer (Markdown), calls: [{tool, args, ok}], model, model_label, fallback}."""
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Ask a question")
    try:
        result = assistant.ask(db, question, [t.model_dump() for t in body.history])
    except RuntimeError as e:
        logger.error(f"assistant failed for {idinfo['email']}: {e}")
        raise HTTPException(status_code=503, detail="The assistant is unavailable. Try again in a minute.")
    record_audit(db, idinfo, action="ask", resource="assistant",
                 detail={"question": question[:500], "tools": [c["tool"] for c in result.calls]})
    return {
        "answer": result.answer,
        "calls": result.calls,
        "model": result.model,
        "model_label": model_label(result.model),
        "fallback": result.model != PRIMARY_MODEL,
    }
