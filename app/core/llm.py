"""The squadron's LLM client — model preference chain and the calls behind it.

Models are tried in order until one answers. GLM 5.2 on NVIDIA's free endpoint
leads: it benchmarks level with Gemini 3.5 Flash and its free tier caps requests
per minute (~40, shared across the whole API key) rather than per day, so a
month of texts never runs it dry — bursts just wait. Gemini 3.5 Flash writes
comparable prose but allows only 20 requests/day, so it and Gemini 2.5 Flash
(250/day) sit behind it, with Groq's gpt-oss-120b as the final fallback. All are
thinking models — keep token budgets high enough that reasoning doesn't starve
the actual answer.

Shared by every AI feature (parade-night texts, NCO appraisals) so the chain,
the quota fallback and the model labels are defined once. Callers get back the
model id that actually answered, so the UI can say a fallback was used.
"""

import time

import httpx

from core.config import GEMINI_API_KEY, GROQ_API_KEY, NVIDIA_API_KEY

GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# NVIDIA and Groq both speak the OpenAI chat-completions API, so one caller does
# both — only the base URL, key and payload extras differ.
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "z-ai/glm-5.2"
# GLM 5.2 thinks hard; NVIDIA's own example uses 16k, and anything less risks
# the reasoning eating the whole budget before the answer starts.
NVIDIA_MAX_TOKENS = 16384

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

# Preference order, best first. Anything other than the first entry means we fell
# back — GLM's per-minute limit is waited out rather than fallen back on, so in
# practice that means NVIDIA errored or the key is missing.
MODEL_PREFERENCE = [NVIDIA_MODEL, "gemini-3.5-flash", "gemini-2.5-flash", GROQ_MODEL]
PRIMARY_MODEL = MODEL_PREFERENCE[0]

MODEL_LABELS = {
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "z-ai/glm-5.2": "GLM 5.2",
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


def _call_openai_compatible(url: str, api_key: str | None, key_name: str, model: str,
                            prompt: str, system_prompt: str, temperature: float,
                            max_tokens: int, **extras) -> str:
    if not api_key:
        raise RuntimeError(f"{key_name} not configured")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        **extras,
    }

    # Generating a whole month of texts trips the free tiers' per-minute limits
    # (Groq counts tokens, NVIDIA counts requests), so wait out 429s instead of
    # failing the batch.
    for _ in range(5):
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=120,
        )
        if resp.status_code != 429:
            break
        time.sleep(min(float(resp.headers.get("retry-after", 10)) + 1, 60))

    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"{model} API error: {resp.text[:200]}")
    return (data["choices"][0]["message"].get("content") or "").strip()


def generate(prompt: str, system_prompt: str, *, temperature: float = 0.6,
             max_tokens: int = 8000, groq_max_tokens: int | None = None) -> tuple[str, str]:
    """Try each model in preference order; return (output_text, model_id used).

    ``groq_max_tokens`` caps the fallback separately — Groq bills a much smaller
    free-tier budget, and its answers don't need Gemini's thinking headroom.
    """
    last_error: Exception | None = None
    for model in MODEL_PREFERENCE:
        try:
            return _call_model(model, prompt, system_prompt, temperature,
                               max_tokens, groq_max_tokens), model
        except Exception as e:
            last_error = e
            print(f"[llm.generate] {model} failed, trying next: {e}")
    raise RuntimeError(f"every model in the chain failed; last error: {last_error}")


def _call_model(model: str, prompt: str, system_prompt: str, temperature: float,
                max_tokens: int, groq_max_tokens: int | None) -> str:
    if model in GEMINI_MODELS:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")
        return _call_gemini(model, prompt, system_prompt, temperature, max_tokens)
    if model == NVIDIA_MODEL:
        return _call_openai_compatible(
            NVIDIA_URL, NVIDIA_API_KEY, "NVIDIA_API_KEY", model,
            prompt, system_prompt, temperature, NVIDIA_MAX_TOKENS,
        )
    return _call_openai_compatible(
        GROQ_URL, GROQ_API_KEY, "GROQ_API_KEY", model,
        prompt, system_prompt, temperature, groq_max_tokens or max_tokens,
        reasoning_effort="low",
    )
