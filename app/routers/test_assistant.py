"""The assistant over HTTP: who can ask, saved chats and who can see them,
what's rejected, what's audited, and how a failed model chain is reported.
The loop itself is tested in core/test_assistant.py; here the model is a
scripted fake."""

import json

import pytest

import core.assistant as assistant
from database.models import AssistantChat, AssistantMessage, AuditEvent, Cadet

CHART = {"type": "bar", "title": "Cadets by flight", "labels": ["A"], "series": [{"name": "Cadets", "values": [1]}]}


@pytest.fixture
def model(monkeypatch):
    state = {"replies": [], "prompts": [], "model": "z-ai/glm-5.3"}

    def fake_generate(prompt, system, **_kw):
        state["prompts"].append(prompt)
        reply = state["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply), state["model"]

    monkeypatch.setattr(assistant, "generate", fake_generate)
    return state


def ask(api, persona="staff", **body):
    return api.post("/assistant/ask", json={"question": "q", **body}, headers=api.as_(persona))


# ─── Who can ask ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method, path", [
    ("post", "/assistant/ask"), ("get", "/assistant/chats"), ("get", "/assistant/chats/1"),
    ("delete", "/assistant/chats/1"),
])
def test_no_token_is_401(api, method, path):
    kw = {"json": {"question": "hi"}} if method == "post" else {}
    assert getattr(api, method)(path, **kw).status_code == 401


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
@pytest.mark.parametrize("method, path", [
    ("post", "/assistant/ask"), ("get", "/assistant/chats"), ("get", "/assistant/chats/1"),
    ("delete", "/assistant/chats/1"),
])
def test_only_staff_get_in(api, model, persona, method, path):
    kw = {"json": {"question": "hi"}} if method == "post" else {}
    assert getattr(api, method)(path, headers=api.as_(persona), **kw).status_code == 403
    assert model["prompts"] == []  # refused before any model call


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_staff_get_an_answer_in_a_new_saved_chat(api, db, model, persona):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Ashworth", flight="A"))
    db.commit()
    model["replies"] = [{"calls": [{"tool": "find_cadets", "args": {"flight": "A"}}]},
                        {"answer": "**Amy Ashworth** is in A flight.", "charts": [CHART],
                         "follow_ups": ["And B?"]}]
    res = ask(api, persona, question="  Who's in A flight?  ")
    assert res.status_code == 200
    body = res.json()
    assert body["chat"]["title"] == "Who's in A flight?"
    assert body["question"]["content"] == "Who's in A flight?"
    answer = body["answer"]
    assert answer["content"] == "**Amy Ashworth** is in A flight."
    assert answer["charts"][0]["title"] == "Cadets by flight" and answer["tables"] == []
    assert answer["follow_ups"] == ["And B?"]
    assert answer["calls"] == [{"tool": "find_cadets", "args": {"flight": "A"}, "ok": True}]
    assert (answer["model_label"], answer["fallback"]) == ("GLM 5.3", False)
    # Saved: the chat reopens with both messages.
    chat = api.get(f"/assistant/chats/{body['chat']['id']}", headers=api.as_(persona)).json()
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["charts"] == answer["charts"]


def test_a_fallback_model_is_flagged(api, model):
    model["model"] = "openai/gpt-oss-120b"
    model["replies"] = [{"answer": "ok"}]
    answer = ask(api).json()["answer"]
    assert answer["fallback"] is True and answer["model_label"] == "Groq gpt-oss-120b"


# ─── Carrying on a chat ──────────────────────────────────────────────────────

def test_a_follow_up_carries_on_the_same_chat_with_its_history(api, db, model):
    model["replies"] = [{"answer": "Four.", "charts": [CHART]}, {"answer": "Six."}]
    first = ask(api, question="How many in A?").json()
    chat_id = first["chat"]["id"]
    second = ask(api, question="And B?", chat_id=chat_id).json()

    assert second["chat"]["id"] == chat_id
    # The model sees the earlier turn, and that a chart came with it.
    assert ("Staff: How many in A?\nAssistant: Four.\n[Shown with this answer: chart \"Cadets by flight\"]"
            "\nStaff: And B?") in model["prompts"][1]
    messages = api.get(f"/assistant/chats/{chat_id}", headers=api.as_("staff")).json()["messages"]
    assert [m["content"] for m in messages] == ["How many in A?", "Four.", "And B?", "Six."]
    assert db.query(AssistantChat).count() == 1


def test_only_the_most_recent_messages_go_back_to_the_model(api, db, model):
    chat = AssistantChat(email="staff@317atc.co.uk", title="t", created_at=_t(0), updated_at=_t(0))
    for i in range(20):
        chat.messages.append(AssistantMessage(role="user" if i % 2 == 0 else "assistant",
                                              content=f"m{i}", created_at=_t(0)))
    db.add(chat)
    db.commit()
    model["replies"] = [{"answer": "ok"}]
    ask(api, question="next", chat_id=chat.id)
    prompt = model["prompts"][0]
    assert "m19" in prompt and "m8" in prompt and "m7" not in prompt


def test_chats_list_newest_first_and_only_your_own(api, db, model):
    model["replies"] = [{"answer": "1"}, {"answer": "2"}, {"answer": "3"}, {"answer": "4"}]
    a = ask(api, question="first").json()["chat"]["id"]
    ask(api, question="second")
    ask(api, "staff2", question="someone else's")
    ask(api, question="back to first", chat_id=a)  # using a chat moves it to the top
    titles = [c["title"] for c in api.get("/assistant/chats", headers=api.as_("staff")).json()]
    assert titles == ["first", "second"]


def test_a_long_first_question_is_cut_for_the_title(api, model):
    model["replies"] = [{"answer": "ok"}]
    title = ask(api, question="Which cadets " + "x" * 200).json()["chat"]["title"]
    assert len(title) == 80 and title.endswith("…")


def test_an_empty_list_for_someone_with_no_chats(api):
    assert api.get("/assistant/chats", headers=api.as_("staff")).json() == []


# ─── Someone else's chat ─────────────────────────────────────────────────────

@pytest.fixture
def others_chat(api, model):
    model["replies"] = [{"answer": "secret"}]
    return ask(api, "staff2", question="mine").json()["chat"]["id"]


def test_someone_elses_chat_cant_be_read(api, others_chat):
    assert api.get(f"/assistant/chats/{others_chat}", headers=api.as_("staff")).status_code == 404


def test_someone_elses_chat_cant_be_carried_on(api, model, others_chat):
    assert ask(api, question="more", chat_id=others_chat).status_code == 404
    assert len(model["prompts"]) == 1  # only staff2's own question reached the model


def test_someone_elses_chat_cant_be_deleted(api, db, others_chat):
    assert api.delete(f"/assistant/chats/{others_chat}", headers=api.as_("staff")).status_code == 404
    assert db.get(AssistantChat, others_chat) is not None


@pytest.mark.parametrize("method", ["get", "delete"])
def test_a_missing_chat_is_404(api, method):
    assert getattr(api, method)("/assistant/chats/999", headers=api.as_("staff")).status_code == 404


def test_asking_in_a_missing_chat_is_404(api, model):
    assert ask(api, chat_id=999).status_code == 404
    assert model["prompts"] == []


def test_deleting_a_chat_removes_its_messages(api, db, model):
    model["replies"] = [{"answer": "ok"}]
    chat_id = ask(api).json()["chat"]["id"]
    assert api.delete(f"/assistant/chats/{chat_id}", headers=api.as_("staff")).status_code == 204
    assert db.query(AssistantChat).count() == 0 and db.query(AssistantMessage).count() == 0


# ─── Rejections and failures ─────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {},
    {"question": ""},
    {"question": "x" * 2001},
    {"question": "q", "chat_id": "abc"},
    {"question": "q", "hide_personal": "maybe"},
])
def test_bad_requests_are_422(api, model, body):
    assert api.post("/assistant/ask", json=body, headers=api.as_("staff")).status_code == 422
    assert model["prompts"] == []


def test_a_whitespace_question_is_422(api, model):
    assert ask(api, question="   ").status_code == 422


def test_every_model_failing_is_a_clean_503_that_saves_nothing(api, db, model):
    model["replies"] = [RuntimeError("every model in the chain failed; last error: quota")]
    res = ask(api)
    assert res.status_code == 503
    assert res.json()["detail"] == "The assistant is unavailable. Try again in a minute."
    # Nothing was answered: no half-chat to come back to, nothing audited as asked.
    assert db.query(AssistantChat).count() == 0
    assert db.query(AuditEvent).count() == 0


def test_a_failure_in_an_existing_chat_leaves_it_as_it_was(api, db, model):
    model["replies"] = [{"answer": "ok"}, RuntimeError("down")]
    chat_id = ask(api).json()["chat"]["id"]
    assert ask(api, question="again", chat_id=chat_id).status_code == 503
    assert db.query(AssistantMessage).count() == 2


# ─── Personal data and audit ─────────────────────────────────────────────────

def test_hide_personal_keeps_dates_of_birth_and_emails_from_the_model(api, db, model):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Ashworth", email="amy@example.com"))
    db.commit()
    call = {"calls": [{"tool": "find_cadets", "args": {"name": "amy"}}]}
    model["replies"] = [call, {"answer": "ok"}, call, {"answer": "ok"}]
    ask(api, question="Amy's email?", hide_personal=True)
    assert "amy@example.com" not in model["prompts"][1]
    ask(api, question="Amy's email?")
    assert "amy@example.com" in model["prompts"][3]


def test_each_question_is_audited_with_the_chat_and_tools_it_used(api, db, model):
    model["replies"] = [{"calls": [{"tool": "squadron_overview", "args": {}}]}, {"answer": "ok"}]
    chat_id = ask(api, question="  How many cadets?  ", hide_personal=True).json()["chat"]["id"]
    [event] = db.query(AuditEvent).all()
    assert (event.actor_email, event.action, event.resource, event.resource_id) == (
        "staff@317atc.co.uk", "ask", "assistant", str(chat_id))
    assert event.detail == {"question": "How many cadets?", "tools": ["squadron_overview"], "personal": False}


def test_asking_is_rate_limited(api, model):
    model["replies"] = [{"answer": "ok"}] * 20
    codes = [ask(api).status_code for _ in range(16)]
    assert codes[:15] == [200] * 15 and codes[15] == 429


def _t(minutes):
    from datetime import datetime, timedelta
    return datetime(2026, 10, 1) + timedelta(minutes=minutes)
