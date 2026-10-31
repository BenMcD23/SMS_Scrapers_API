"""The model preference chain and its fallback behaviour.

Was a `__main__` self-check inside llm.py, which nothing ran — so it went stale
(still asserting Gemini led the chain long after NVIDIA took over) and had
stopped being runnable at all, since executing llm.py as a script makes
core/calendar.py shadow the stdlib `calendar` module.

No network: `_call_model` is stubbed, so nothing here needs an API key.
"""

import pytest

import core.llm as llm
from core.llm import (
    GROQ_MODEL,
    MODEL_LABELS,
    MODEL_PREFERENCE,
    NVIDIA_MODEL,
    PRIMARY_MODEL,
    model_label,
)


@pytest.fixture
def chain(monkeypatch):
    """Stub the per-model call. Returns the list that records what was tried,
    and takes the set of models that should 'work'."""
    def install(working: set[str]):
        tried: list[str] = []

        def fake_call(model, *_args, **_kwargs):
            tried.append(model)
            if model not in working:
                raise RuntimeError("quota")
            return "ok"

        monkeypatch.setattr(llm, "_call_model", fake_call)
        return tried

    return install


def test_primary_is_glm():
    # The UI names the primary model to the user, so copy depends on this.
    assert PRIMARY_MODEL == NVIDIA_MODEL == "z-ai/glm-5.3"


def test_first_working_model_wins(chain):
    tried = chain(set(MODEL_PREFERENCE))
    assert llm.generate("p", "s") == ("ok", PRIMARY_MODEL)
    # Nothing after the first is even attempted.
    assert tried == [PRIMARY_MODEL]


def test_falls_through_in_order(chain):
    tried = chain({GROQ_MODEL})
    assert llm.generate("p", "s") == ("ok", GROQ_MODEL)
    assert tried == MODEL_PREFERENCE


def test_raises_when_every_model_fails(chain):
    tried = chain(set())
    with pytest.raises(RuntimeError, match="every model in the chain failed"):
        llm.generate("p", "s")
    assert tried == MODEL_PREFERENCE


def test_every_model_in_the_chain_has_a_label():
    # A missing label falls back to the raw id, which would surface something
    # like "nvidia/nemotron-3-ultra-550b-a55b" to squadron staff in the texts UI.
    for model in MODEL_PREFERENCE:
        assert model in MODEL_LABELS, f"{model} has no display label"
    assert model_label(PRIMARY_MODEL) == "GLM 5.3"
    assert model_label(None) == "Unknown"


# ─── Tool calling (chat) ─────────────────────────────────────────────────────

import httpx  # noqa: E402

from core.llm import GROQ_MODEL as GROQ  # noqa: E402
from core.llm import chat  # noqa: E402


@pytest.fixture
def api(monkeypatch):
    """Fake OpenAI-compatible endpoints: what each was sent, and a reply per URL."""
    sent, replies = [], {}

    def fake_post(url, headers, json, timeout):
        sent.append((url, json))
        reply = replies.get("nvidia" if "nvidia" in url else "groq")
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    monkeypatch.setattr(llm, "NVIDIA_API_KEY", "nv-key")
    monkeypatch.setattr(llm, "GROQ_API_KEY", "groq-key")
    return sent, replies


TOOLS = [{"type": "function", "function": {"name": "find", "parameters": {"type": "object", "properties": {}}}}]
CALL = {"choices": [{"message": {"tool_calls": [{"id": "c1", "type": "function",
                                                 "function": {"name": "find", "arguments": "{}"}}]}}]}


def test_chat_sends_the_tools_and_returns_the_message(api):
    sent, replies = api
    replies["nvidia"] = httpx.Response(200, json=CALL)
    message, model = chat([{"role": "user", "content": "q"}], TOOLS, models=[NVIDIA_MODEL, GROQ])
    assert model == NVIDIA_MODEL and message["tool_calls"][0]["function"]["name"] == "find"
    [(url, payload)] = sent
    assert payload["tools"] == TOOLS and payload["tool_choice"] == "auto"
    assert payload["messages"] == [{"role": "user", "content": "q"}]


def test_chat_falls_back_when_the_first_model_fails(api):
    sent, replies = api
    replies["nvidia"] = httpx.ReadTimeout("slow")
    replies["groq"] = httpx.Response(200, json=CALL)
    _, model = chat([], TOOLS, models=[NVIDIA_MODEL, GROQ], groq_max_tokens=8000)
    assert model == GROQ
    groq_payload = sent[-1][1]
    assert groq_payload["reasoning_effort"] == "medium" and groq_payload["max_tokens"] == 8000


@pytest.mark.parametrize("reply", [httpx.Response(400, json={"error": {"message": "bad"}}), httpx.ConnectError("x")])
def test_chat_raises_when_every_model_fails(api, reply):
    _, replies = api
    replies["nvidia"] = replies["groq"] = reply
    with pytest.raises(RuntimeError, match="every model in the chain failed"):
        chat([], TOOLS, models=[NVIDIA_MODEL, GROQ])


def test_chat_wont_offer_tools_to_a_model_it_cant_call_them_on(api, monkeypatch):
    with pytest.raises(RuntimeError, match="can't call tools here"):
        chat([], TOOLS, models=["gemini-2.5-flash"])
    monkeypatch.setattr(llm, "NVIDIA_API_KEY", None)
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY not configured"):
        chat([], TOOLS, models=[NVIDIA_MODEL])
