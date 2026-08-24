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
    GROQ_MODEL, MODEL_LABELS, MODEL_PREFERENCE, NVIDIA_MODEL, PRIMARY_MODEL,
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


def test_primary_is_nemotron():
    # The UI names the primary model to the user, so copy depends on this.
    assert PRIMARY_MODEL == NVIDIA_MODEL == "nvidia/nemotron-3-ultra-550b-a55b"


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
    assert model_label(PRIMARY_MODEL) == "Nemotron 3 Ultra"
    assert model_label(None) == "Unknown"
