"""Docs assistant: questions answered from the RAFAC controlled documents.

The chatbot service (github.com/BenMcD23/chatbot) only listens inside the
cluster and has no auth of its own, so this route is its front door: it does
the staff check, then forwards the question as-is.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.config import CHATBOT_URL
from core.security import require_staff

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@router.post("/ask")
def ask(body: Question, idinfo: dict = Depends(require_staff)) -> dict:
    """Answer plus numbered sources ({n, filename, location, url, snippet, score}).

    The timeout covers the chatbot's own retry/backoff when the LLM provider
    rate-limits it (~15s) on top of a normal answer.
    """
    logger.info(f"docs assistant question from {idinfo['email']}")
    try:
        res = httpx.post(f"{CHATBOT_URL}/ask", json={"question": body.question.strip()}, timeout=90.0)
    except httpx.HTTPError as e:
        logger.error(f"chatbot unreachable: {e}")
        raise HTTPException(status_code=503, detail="Docs assistant is unavailable")
    if res.status_code != 200:
        logger.error(f"chatbot returned {res.status_code}: {res.text[:500]}")
        raise HTTPException(status_code=502, detail="Docs assistant failed to answer")
    return res.json()
