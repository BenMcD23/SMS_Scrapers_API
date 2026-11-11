"""Speech to text for staff voice notes.

Staff hold a button on their phone, talk, and let go; the browser records the
clip and this turns it into text. Recording in the browser and transcribing
here, rather than using the browser's own speech recognition, is deliberate:
that API is missing from Firefox, unreliable on iPhones, stops at the first
pause, and copes badly with a drill hall's noise. Whisper handles all of that,
and can be handed the NCOs' names so it spells them the way the roster does.

Groq's Whisper (the same Groq key the LLM chain already falls back on) is tried
first; Gemini, which takes audio directly, is the backup. The clip is never
stored — once it's text, the recording of a child has served its purpose.
"""

import base64
import logging

import httpx

from core.config import GEMINI_API_KEY, GROQ_API_KEY

logger = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"

GEMINI_AUDIO_MODEL = "gemini-2.5-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

MODEL_LABELS = {
    GROQ_WHISPER_MODEL: "Whisper (Groq)",
    GEMINI_AUDIO_MODEL: "Gemini 2.5 Flash",
}

# Whisper reads the file extension to pick a decoder, and browsers record in
# different containers: Chrome and Firefox give WebM/Opus, Safari gives MP4/AAC.
EXTENSIONS = {
    "audio/webm": "webm", "video/webm": "webm",
    "audio/mp4": "m4a", "video/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a",
    "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/flac": "flac",
}


def base_type(content_type: str | None) -> str:
    """"audio/webm;codecs=opus" -> "audio/webm"."""
    return (content_type or "").split(";")[0].strip().lower()


def is_supported(content_type: str | None) -> bool:
    return base_type(content_type) in EXTENSIONS


def available() -> bool:
    """Whether any transcriber is configured — the page hides the mic if not,
    rather than offering a button that can only fail."""
    return bool(GROQ_API_KEY or GEMINI_API_KEY)


def _groq(audio: bytes, content_type: str, hint: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")
    ext = EXTENSIONS[base_type(content_type)]
    resp = httpx.post(
        GROQ_TRANSCRIBE_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (f"note.{ext}", audio, base_type(content_type))},
        data={
            "model": GROQ_WHISPER_MODEL,
            "language": "en",
            "temperature": "0",
            "response_format": "json",
            # Whisper's prompt is "the text so far": a list of names primes it
            # to spell them as the roster does instead of guessing.
            "prompt": hint,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq transcription error {resp.status_code}: {resp.text[:200]}")
    return (resp.json().get("text") or "").strip()


def _gemini(audio: bytes, content_type: str, hint: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured")
    resp = httpx.post(
        GEMINI_URL.format(model=GEMINI_AUDIO_MODEL) + f"?key={GEMINI_API_KEY}",
        json={
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": base_type(content_type),
                                 "data": base64.b64encode(audio).decode()}},
                {"text": "Transcribe this voice note word for word, in British English. "
                         "Reply with the transcript only, nothing else. "
                         f"Names that may come up: {hint}"},
            ]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 4000},
        },
        timeout=60,
    )
    data = resp.json()
    if "candidates" not in data:
        raise RuntimeError(f"Gemini transcription error {resp.status_code}: {resp.text[:200]}")
    parts = data["candidates"][0]["content"].get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


TRANSCRIBERS = [(GROQ_WHISPER_MODEL, _groq), (GEMINI_AUDIO_MODEL, _gemini)]


def transcribe(audio: bytes, content_type: str, names: list[str]) -> tuple[str, str]:
    """(transcript, model id). Raises RuntimeError when every transcriber fails,
    so the caller can tell staff to type it instead."""
    hint = ", ".join(names)
    last_error: Exception | None = None
    for model, call in TRANSCRIBERS:
        try:
            return call(audio, content_type, hint), model
        except Exception as e:
            last_error = e
            logger.error(f"{model} transcription failed, trying next: {e}")
    raise RuntimeError(f"every transcriber failed; last error: {last_error}")
