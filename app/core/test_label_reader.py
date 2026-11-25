"""Reading a label photo with Gemini: the request it sends, how it copes with
the reply, and falling back through the models when one fails."""

import json

import httpx
import pytest

from core import label_reader


class FakeGemini:
    """Stands in for httpx.post; each model answers from ``replies``."""

    def __init__(self):
        self.calls = []
        self.replies = {}

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        model = next(m for m in label_reader.GEMINI_MODELS if f"/{m}:" in url)
        reply = self.replies.get(model, self.ok({"nsn": "8405-99-845-6634", "size": "110/40(16)",
                                                 "description": "SHIRT, MAN'S, Mid Blue"}))
        if isinstance(reply, Exception):
            raise reply
        return reply

    @staticmethod
    def ok(payload, raw=None):
        text = raw if raw is not None else json.dumps(payload)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


@pytest.fixture
def gemini(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(label_reader, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(httpx, "post", fake)
    return fake


def test_a_label_is_read(gemini):
    read, model = label_reader.read_label(b"jpeg", "image/jpeg")
    assert read == {"nsn": "8405-99-845-6634", "size": "110/40(16)", "description": "SHIRT, MAN'S, Mid Blue"}
    assert model == label_reader.GEMINI_MODELS[0]
    url, kwargs = gemini.calls[0]
    assert url.endswith("?key=key")
    image, prompt = kwargs["json"]["contents"][0]["parts"]
    assert image == {"inline_data": {"mime_type": "image/jpeg", "data": "anBlZw=="}}
    assert "never guess a digit" in prompt["text"] and "Ignore anything handwritten" in prompt["text"]
    assert kwargs["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_the_content_type_is_sent_without_its_parameters(gemini):
    label_reader.read_label(b"x", "image/HEIC; charset=binary")
    assert gemini.calls[0][1]["json"]["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/heic"


def test_only_the_three_fields_come_back_even_if_the_model_reads_the_name(gemini):
    # Labels have Name and No boxes; whatever a cadet wrote there goes no further.
    gemini.replies[label_reader.GEMINI_MODELS[0]] = gemini.ok(
        {"nsn": "8410-99-869-6671", "size": "35/96", "description": None, "name": "Cdt Ó Súilleabháin"})
    read, _ = label_reader.read_label(b"x", "image/jpeg")
    assert read == {"nsn": "8410-99-869-6671", "size": "35/96", "description": None}


def test_a_fenced_reply_is_still_read(gemini):
    gemini.replies[label_reader.GEMINI_MODELS[0]] = gemini.ok(
        None, raw='```json\n{"nsn": null, "size": "56", "description": "Beret"}\n```')
    read, _ = label_reader.read_label(b"x", "image/jpeg")
    assert read == {"nsn": None, "size": "56", "description": "Beret"}


@pytest.mark.parametrize("raw", ['{"nsn": 8405998456634}', '{"nsn": "   "}', '{"nsn": ["x"]}'])
def test_anything_but_text_is_treated_as_unread(raw):
    assert label_reader.parse_reply(raw)["nsn"] is None


@pytest.mark.parametrize("bad", [
    httpx.Response(429, json={"error": {"message": "quota"}}),
    httpx.Response(500, text="boom"),
    FakeGemini.ok(None, raw="I can see a shirt label."),
    FakeGemini.ok(None, raw="[1, 2]"),
    httpx.ConnectTimeout("slow"),
])
def test_a_failing_model_falls_back_to_the_next(gemini, bad):
    gemini.replies[label_reader.GEMINI_MODELS[0]] = bad
    read, model = label_reader.read_label(b"x", "image/jpeg")
    assert model == label_reader.GEMINI_MODELS[1]
    assert read["nsn"] == "8405-99-845-6634"


def test_every_model_failing_is_an_error_for_the_caller(gemini):
    for model in label_reader.GEMINI_MODELS:
        gemini.replies[model] = httpx.Response(500, text="down")
    with pytest.raises(RuntimeError, match="every model failed"):
        label_reader.read_label(b"x", "image/jpeg")


def test_without_a_key_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(label_reader, "GEMINI_API_KEY", None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("sent without a key"))
    assert label_reader.available() is False
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY not configured"):
        label_reader.read_label(b"x", "image/jpeg")
