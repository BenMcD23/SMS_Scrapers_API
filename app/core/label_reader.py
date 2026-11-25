"""Read the stock number and size off a photo of a uniform label.

Kit in its bag has the stock number as a barcode, which the phone reads by
itself. Once it's out of the bag only the sewn-in label is left, printed text
on fabric that's usually creased and photographed at an angle, so this asks a
vision model to read it. Only Gemini in the LLM chain takes images, so its two
models are the whole chain here.

The model only reads; core/nsn.py decides what the number means, so a misread
digit comes back as a number nobody knows rather than as the wrong item. The
photo is never stored. Labels have Name and No boxes that a cadet may have
written on, so the prompt asks for neither and the reply is cut down to the
three fields below.
"""

import base64
import json
import logging
import re

import httpx

from core.config import GEMINI_API_KEY
from core.llm import GEMINI_MODELS, GEMINI_URL, MODEL_LABELS

logger = logging.getLogger(__name__)

# Gemini's own list of image types, plus HEIC for photos straight off an iPhone.
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"})

PROMPT = (
    "This is a photo of a UK military uniform label or the label on its packet. "
    "Read what is printed on it. Reply with JSON only, in this shape: "
    '{"nsn": "the 13-digit NATO stock number exactly as printed, with its dashes", '
    '"size": "the size as printed, without the word Size", '
    '"description": "the item description as printed"}. '
    "Use null for anything you can't read with confidence; never guess a digit. "
    "Ignore anything handwritten, and any name or service number."
)


def base_type(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def available() -> bool:
    """Whether label reading can work at all, so the page can hide the button
    rather than offer one that can only fail."""
    return bool(GEMINI_API_KEY)


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:200] or None


def parse_reply(text: str) -> dict:
    """The model's JSON, tolerating a ```json fence, cut to the fields we use."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(cleaned)
    except ValueError as e:
        raise RuntimeError(f"not JSON: {text[:100]!r}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"not an object: {text[:100]!r}")
    return {key: _text(data.get(key)) for key in ("nsn", "size", "description")}


def _gemini(model: str, image: bytes, content_type: str) -> dict:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured")
    resp = httpx.post(
        GEMINI_URL.format(model=model) + f"?key={GEMINI_API_KEY}",
        json={
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": base_type(content_type),
                                 "data": base64.b64encode(image).decode()}},
                {"text": PROMPT},
            ]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 2000,
                                 "responseMimeType": "application/json"},
        },
        timeout=60,
    )
    data = resp.json()
    if "candidates" not in data:
        if resp.status_code == 429:
            raise RuntimeError("rate limited (free tier quota)")
        raise RuntimeError(f"Gemini label read error {resp.status_code}: {resp.text[:200]}")
    parts = data["candidates"][0]["content"].get("parts", [])
    return parse_reply("".join(p.get("text", "") for p in parts if not p.get("thought")))


def read_label(image: bytes, content_type: str) -> tuple[dict, str]:
    """({nsn, size, description}, model id), each field None if unreadable.
    Raises RuntimeError when every model fails, so the caller can offer typing
    the number or picking the item instead."""
    last_error: Exception | None = None
    for model in GEMINI_MODELS:
        try:
            return _gemini(model, image, content_type), model
        except Exception as e:
            last_error = e
            logger.error(f"{model} label read failed, trying next: {e}")
    raise RuntimeError(f"every model failed; last error: {last_error}")


def model_label(model: str) -> str:
    return MODEL_LABELS.get(model, model)
