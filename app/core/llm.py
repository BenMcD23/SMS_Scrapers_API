"""The squadron's LLM client — model preference chain and the calls behind it.

Models are tried in order until one answers. Gemini 3.5 Flash writes the best
prose but its free tier only allows 20 requests/day (per model), so 2.5 Flash
(250/day) catches the overflow and Groq's gpt-oss-120b is the final fallback.
All are thinking models — keep token budgets high enough that reasoning doesn't
starve the actual answer.

Shared by every AI feature (parade-night texts, NCO appraisals) so the chain,
the quota fallback and the model labels are defined once. Callers get back the
model id that actually answered, so the UI can say a fallback was used.
"""

import time

import httpx

from core.config import GEMINI_API_KEY, GROQ_API_KEY

GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

# Preference order, best first. Anything other than the first entry means we
# fell back — usually because the best model's daily free-tier quota ran out.
MODEL_PREFERENCE = GEMINI_MODELS + [GROQ_MODEL]
PRIMARY_MODEL = MODEL_PREFERENCE[0]

MODEL_LABELS = {
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "openai/gpt-oss-120b": "Groq gpt-oss-120b",
}


def model_label(model_id: str | None) -> str:
    if not model_id:
        return "Unknown"
    return MODEL_LABELS.get(model_id, model_id)


def _call_gemini(model: str, prompt: str, system_prompt: str,
                 temperature: float, max_tokens: int) -> str:
    resp = httpx.post(
        GEMINI_URL.format(model=model) + f"?key={GEMINI_API_KEY}",
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        },
        timeout=120,
    )
    data = resp.json()
    if "candidates" not in data:
        if resp.status_code == 429:
            raise RuntimeError("rate limited (free tier quota)")
        raise RuntimeError(f"Gemini API error: {resp.text[:200]}")

    parts = data["candidates"][0]["content"].get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def _call_groq(prompt: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_effort": "low",
    }

    # Generating a whole month of texts trips the free tier's tokens-per-minute
    # limit, so wait out 429s instead of failing the batch.
    for _ in range(5):
        resp = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json=payload,
            timeout=60,
        )
        if resp.status_code != 429:
            break
        time.sleep(min(float(resp.headers.get("retry-after", 10)) + 1, 60))

    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"Groq API error: {resp.text}")
    return data["choices"][0]["message"]["content"].strip()


def generate(prompt: str, system_prompt: str, *, temperature: float = 0.6,
             max_tokens: int = 8000, groq_max_tokens: int | None = None) -> tuple[str, str]:
    """Try each model in preference order; return (output_text, model_id used).

    ``groq_max_tokens`` caps the fallback separately — Groq bills a much smaller
    free-tier budget, and its answers don't need Gemini's thinking headroom.
    """
    if GEMINI_API_KEY:
        for model in GEMINI_MODELS:
            try:
                return _call_gemini(model, prompt, system_prompt, temperature, max_tokens), model
            except Exception as e:
                print(f"[llm.generate] {model} failed, trying next: {e}")
    return (
        _call_groq(prompt, system_prompt, temperature, groq_max_tokens or max_tokens),
        GROQ_MODEL,
    )
