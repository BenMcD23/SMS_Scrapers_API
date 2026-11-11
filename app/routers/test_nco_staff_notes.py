"""Staff notes about NCOs: access, writing, profiles, voice, the AI split, and
how they feed the appraisals."""

import json
from datetime import datetime

import httpx
import pytest

from core import transcribe as speech
from database.models import Cadet, NcoStaffNote

AMY, BEN, CARA, NIAMH = 1, 2, 3, 4


@pytest.fixture
def team(db):
    db.add_all([
        Cadet(cin=AMY, first_name="Amy", last_name="Able", rank="Corporal", flight="A"),
        Cadet(cin=BEN, first_name="Ben", last_name="Baker", rank="Sgt", flight="B"),
        Cadet(cin=CARA, first_name="Cara", last_name="Cadet", rank="Cadet", flight="A"),
        Cadet(cin=NIAMH, first_name="Niamh", last_name="Ó Súilleabháin", rank="Flight Sergeant", flight="C"),
    ])
    db.commit()


def _note(api, persona="staff", **body):
    return api.post("/nco-notes", headers=api.as_(persona),
                    json={"cadet_cin": AMY, "body": "Sharp on drill", **body})


# ── access ───────────────────────────────────────────────────────────────────

ENDPOINTS = [
    ("get", "/nco-notes"), ("get", f"/nco-notes/ncos/{AMY}"), ("post", "/nco-notes"),
    ("post", "/nco-notes/batch"), ("put", "/nco-notes/1"), ("delete", "/nco-notes/1"),
    ("post", "/nco-notes/transcribe"), ("post", "/nco-notes/split"),
]


@pytest.mark.parametrize("method,path", ENDPOINTS)
@pytest.mark.parametrize("persona,code", [(None, 401), ("cadet", 403), ("nco", 403), ("snco", 403)])
def test_only_staff_get_in(api, team, method, path, persona, code):
    # These are staff's views of the NCOs themselves: no NCO or SNCO, ever.
    headers = api.as_(persona) if persona else {}
    call = getattr(api, method)
    res = call(path, headers=headers) if method in ("get", "delete") else call(path, json={}, headers=headers)
    assert res.status_code == code


@pytest.mark.parametrize("persona", ["staff", "owner", "oc"])
def test_every_staff_member_can_read_and_write(api, team, persona):
    assert _note(api, persona).status_code == 201
    assert api.get("/nco-notes", headers=api.as_(persona)).status_code == 200
    assert api.get(f"/nco-notes/ncos/{AMY}", headers=api.as_(persona)).json()["open_notes"] >= 1


# ── writing ──────────────────────────────────────────────────────────────────

def test_a_note_is_filed_against_the_night_it_is_about(api, team):
    res = _note(api, body="  Took the flight for drill  ", note_date="2026-03-04T19:45:00Z", source="voice")
    assert res.status_code == 201
    n = res.json()
    assert (n["body"], n["note_date"], n["source"]) == ("Took the flight for drill", "2026-03-04T00:00:00", "voice")
    assert (n["cadet_cin"], n["cadet_name"], n["author_name"]) == (AMY, "Corporal Amy Able", "Sam Staff")
    assert n["is_mine"] and n["can_edit"] and n["can_delete"] and n["appraisal_id"] is None
    # Today, at midnight, by default; typed unless it says otherwise.
    n = _note(api).json()
    assert n["note_date"] == datetime.now().strftime("%Y-%m-%dT00:00:00") and n["source"] == "typed"


@pytest.mark.parametrize("body,code", [
    ({"body": "   "}, 400),
    ({"source": "telepathy"}, 400),
    ({"note_date": "next week"}, 400),
    ({"cadet_cin": 99}, 404),
    ({"cadet_cin": CARA}, 400),  # a cadet, not an NCO
    ({"cadet_cin": "abc"}, 422),
])
def test_bad_notes_are_turned_away(api, team, db, body, code):
    assert _note(api, **body).status_code == code
    assert db.query(NcoStaffNote).count() == 0


def test_a_long_note_is_cut_short_not_rejected(api, team):
    assert len(_note(api, body="b" * 5000).json()["body"]) == 2000


def test_non_ascii_names_come_through_intact(api, team):
    n = _note(api, cadet_cin=NIAMH, body="Ran the brief — très bien").json()
    assert n["cadet_name"] == "Flight Sergeant Niamh Ó Súilleabháin" and "très" in n["body"]


def test_a_batch_saves_a_note_per_nco(api, team):
    res = api.post("/nco-notes/batch", headers=api.as_("staff"), json={
        "note_date": "2026-05-01",
        "notes": [{"cadet_cin": AMY, "body": "Great drill"}, {"cadet_cin": BEN, "body": "Late again"}],
    })
    assert res.status_code == 201
    notes = res.json()["notes"]
    assert [(n["cadet_cin"], n["body"], n["source"]) for n in notes] == [
        (AMY, "Great drill", "voice"), (BEN, "Late again", "voice")]
    assert {n["note_date"] for n in notes} == {"2026-05-01T00:00:00"}


@pytest.mark.parametrize("notes,code", [
    ([{"cadet_cin": AMY, "body": "Fine"}, {"cadet_cin": CARA, "body": "Not an NCO"}], 400),
    ([{"cadet_cin": AMY, "body": "Fine"}, {"cadet_cin": BEN, "body": " "}], 400),
    ([{"cadet_cin": AMY, "body": "Fine"}, {"cadet_cin": 99, "body": "Who?"}], 404),
    ([], 422),
    ([{"cadet_cin": AMY, "body": "x"}] * 31, 422),
])
def test_a_batch_with_one_bad_row_saves_nothing(api, team, db, notes, code):
    # All or nothing, so staff never have to work out which half of the night saved.
    res = api.post("/nco-notes/batch", headers=api.as_("staff"), json={"notes": notes})
    assert res.status_code == code
    assert db.query(NcoStaffNote).count() == 0


# ── reading ──────────────────────────────────────────────────────────────────

def test_the_overview_counts_notes_waiting_for_each_ncos_appraisal(api, team):
    _note(api, note_date="2026-01-01")
    _note(api, note_date="2026-02-01")
    _note(api, cadet_cin=BEN)
    body = api.get("/nco-notes", headers=api.as_("staff")).json()
    rows = {r["cin"]: r for r in body["ncos"]}
    # The cadet isn't on the NCO team, so isn't listed.
    assert set(rows) == {AMY, BEN, NIAMH}
    assert (rows[AMY]["open_notes"], rows[BEN]["open_notes"], rows[NIAMH]["open_notes"]) == (2, 1, 0)
    assert rows[AMY]["last_note_date"] == "2026-02-01T00:00:00" and rows[NIAMH]["last_note_date"] is None
    assert len(body["recent"]) == 3


def test_the_overview_with_no_notes_is_empty_not_an_error(api, team):
    body = api.get("/nco-notes", headers=api.as_("staff")).json()
    assert body["recent"] == [] and {r["open_notes"] for r in body["ncos"]} == {0}


def test_the_overview_says_whether_voice_notes_can_be_transcribed(api, monkeypatch):
    for groq, gemini, expected in [(None, None, False), ("k", None, True), (None, "k", True)]:
        monkeypatch.setattr(speech, "GROQ_API_KEY", groq)
        monkeypatch.setattr(speech, "GEMINI_API_KEY", gemini)
        assert api.get("/nco-notes", headers=api.as_("staff")).json()["voice_available"] is expected


def test_a_profile_lists_the_ncos_notes_newest_first(api, team):
    _note(api, body="Old", note_date="2026-01-01")
    _note(api, body="New", note_date="2026-03-01")
    _note(api, cadet_cin=BEN, body="Someone else")
    body = api.get(f"/nco-notes/ncos/{AMY}", headers=api.as_("staff")).json()
    assert body["nco"]["name"] == "Corporal Amy Able"
    assert [n["body"] for n in body["notes"]] == ["New", "Old"]
    assert body["open_notes"] == 2 and body["appraisals"] == [] and body["next_review_date"] is None


@pytest.mark.parametrize("cin,code", [(99, 404), (CARA, 400)])
def test_a_profile_is_only_for_an_nco(api, team, cin, code):
    assert api.get(f"/nco-notes/ncos/{cin}", headers=api.as_("staff")).status_code == code


def test_a_note_about_a_cadet_who_has_left_keeps_their_name(api, team, db):
    _note(api)
    db.query(NcoStaffNote).update({NcoStaffNote.cadet_id: None})  # what the FK does on delete
    db.commit()
    [n] = api.get("/nco-notes", headers=api.as_("staff")).json()["recent"]
    assert (n["cadet_cin"], n["cadet_name"]) == (None, "Corporal Amy Able")


# ── editing and deleting ─────────────────────────────────────────────────────

def test_the_author_can_fix_a_note_and_move_it_to_the_right_nco(api, team):
    note = _note(api).json()
    res = api.put(f"/nco-notes/{note['id']}", headers=api.as_("staff"),
                  json={"body": " Ben, not Amy, was sharp ", "cadet_cin": BEN})
    assert res.status_code == 200
    n = res.json()
    assert (n["body"], n["cadet_cin"], n["cadet_name"]) == ("Ben, not Amy, was sharp", BEN, "Sgt Ben Baker")
    assert n["updated_at"]


@pytest.mark.parametrize("body,code", [({"body": ""}, 400), ({"body": "x", "cadet_cin": CARA}, 400),
                                       ({"body": "x", "cadet_cin": 99}, 404), ({}, 422)])
def test_a_bad_edit_is_turned_away(api, team, body, code):
    note = _note(api).json()
    assert api.put(f"/nco-notes/{note['id']}", headers=api.as_("staff"), json=body).status_code == code


def test_another_staff_members_note_is_read_only(api, team):
    note = _note(api).json()
    other = api.as_("staff2")
    seen = api.get("/nco-notes", headers=other).json()["recent"][0]
    assert not seen["is_mine"] and not seen["can_edit"] and not seen["can_delete"]
    assert api.put(f"/nco-notes/{note['id']}", headers=other, json={"body": "x"}).status_code == 403
    assert api.delete(f"/nco-notes/{note['id']}", headers=other).status_code == 403


def test_a_note_whose_author_has_gone_can_be_tidied_by_any_staff(api, team, db):
    note = _note(api).json()
    db.query(NcoStaffNote).update({NcoStaffNote.author_id: None})
    db.commit()
    other = api.as_("staff2")
    seen = api.get("/nco-notes", headers=other).json()["recent"][0]
    assert seen["author_name"] == "Former staff" and seen["can_delete"]
    assert api.delete(f"/nco-notes/{note['id']}", headers=other).status_code == 200


def test_missing_notes_are_404(api, team):
    staff = api.as_("staff")
    assert api.put("/nco-notes/99", headers=staff, json={"body": "x"}).status_code == 404
    assert api.delete("/nco-notes/99", headers=staff).status_code == 404


# ── feeding the appraisals ───────────────────────────────────────────────────

def _appraise(api, cin=AMY, note_ids=()):
    return api.post("/nco-appraisals", headers=api.as_("staff"),
                    json={"cadet_id": cin, "strengths": "Drill", "note_ids": list(note_ids)})


def test_an_appraisal_uses_up_the_notes_it_was_written_from(api, team):
    used = [_note(api, body=f"Note {i}").json()["id"] for i in range(2)]
    later = _note(api, body="After").json()["id"]
    appraisal = _appraise(api, note_ids=used)
    assert appraisal.status_code == 200

    profile = api.get(f"/nco-notes/ncos/{AMY}", headers=api.as_("staff")).json()
    by_id = {n["id"]: n for n in profile["notes"]}
    assert {by_id[i]["appraisal_id"] for i in used} == {appraisal.json()["id"]}
    assert by_id[used[0]]["appraisal_date"] and by_id[later]["appraisal_id"] is None
    assert profile["open_notes"] == 1 and len(profile["appraisals"]) == 1 and profile["next_review_date"]
    # Its text is the appraisal's evidence now, so it's fixed; removing it is still fine.
    assert not by_id[used[0]]["can_edit"] and by_id[used[0]]["can_delete"]
    assert api.put(f"/nco-notes/{used[0]}", headers=api.as_("staff"), json={"body": "x"}).status_code == 409
    assert api.delete(f"/nco-notes/{used[0]}", headers=api.as_("staff")).status_code == 200


def test_the_appraisals_page_shows_how_many_notes_are_waiting(api, team):
    _note(api)
    _note(api)
    rows = {r["cin"]: r for r in api.get("/nco-appraisals", headers=api.as_("staff")).json()["ncos"]}
    assert (rows[AMY]["open_notes"], rows[BEN]["open_notes"]) == (2, 0)


def test_an_appraisal_cant_claim_another_ncos_notes(api, team, db):
    bens = _note(api, cadet_cin=BEN).json()["id"]
    assert _appraise(api, note_ids=[bens]).status_code == 400
    assert _appraise(api, note_ids=[999]).status_code == 400
    # Nothing was saved and the note is still waiting.
    assert db.get(NcoStaffNote, bens).appraisal_id is None
    assert api.get("/nco-appraisals", headers=api.as_("staff")).json()["appraisals"] == []


def test_notes_already_used_by_another_appraisal_are_a_conflict(api, team):
    # Two staff writing the same NCO's appraisal at once from the same notes.
    note = _note(api).json()["id"]
    assert _appraise(api, note_ids=[note]).status_code == 200
    assert _appraise(api, note_ids=[note]).status_code == 409


def test_deleting_an_appraisal_puts_its_notes_back_in_the_queue(api, team):
    note = _note(api).json()["id"]
    appraisal = _appraise(api, note_ids=[note]).json()["id"]
    assert api.delete(f"/nco-appraisals/{appraisal}", headers=api.as_("staff")).status_code == 200
    assert api.get(f"/nco-notes/ncos/{AMY}", headers=api.as_("staff")).json()["open_notes"] == 1


def test_an_appraisal_without_notes_still_saves(api, team):
    assert _appraise(api).status_code == 200


# ── voice ────────────────────────────────────────────────────────────────────

class FakeTranscribers:
    """Answers httpx.post for Groq and Gemini, recording what was sent."""

    def __init__(self):
        self.calls = []
        self.groq = lambda: httpx.Response(200, json={"text": " Amy was sharp on drill. "})
        self.gemini = lambda: httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "From Gemini"}]}}]})

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = (self.groq if "groq" in url else self.gemini)()
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def stt(monkeypatch):
    fake = FakeTranscribers()
    monkeypatch.setattr(speech, "GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(speech, "GEMINI_API_KEY", "gemini-key")
    monkeypatch.setattr(httpx, "post", fake)
    return fake


def _upload(api, data=b"\x1aE\xdf\xa3audio", content_type="audio/webm;codecs=opus", persona="staff"):
    return api.post("/nco-notes/transcribe", headers=api.as_(persona),
                    files={"audio": ("note", data, content_type)})


def test_a_recording_comes_back_as_text(api, team, stt):
    res = _upload(api)
    assert res.status_code == 200
    assert res.json() == {"text": "Amy was sharp on drill.", "model": "whisper-large-v3-turbo",
                          "model_label": "Whisper (Groq)"}
    [(url, sent)] = stt.calls
    assert "groq" in url
    # The NCOs' names prime the spelling; the cadet isn't one of them.
    assert "Corporal Amy Able" in sent["data"]["prompt"] and "Cara" not in sent["data"]["prompt"]
    assert sent["files"]["file"][0] == "note.webm"


def test_safaris_mp4_recordings_are_sent_with_an_extension_whisper_can_read(api, team, stt):
    assert _upload(api, content_type="audio/mp4").status_code == 200
    assert stt.calls[0][1]["files"]["file"][0] == "note.m4a"


@pytest.mark.parametrize("failure", [
    lambda: httpx.Response(500, text="down"),
    lambda: httpx.Response(429, text="rate limited"),
    lambda: httpx.ConnectTimeout("timed out"),
])
def test_gemini_takes_over_when_groq_fails(api, team, stt, failure):
    stt.groq = failure
    res = _upload(api)
    assert res.status_code == 200 and res.json()["text"] == "From Gemini"
    assert res.json()["model_label"] == "Gemini 2.5 Flash"


def test_when_every_transcriber_fails_staff_are_told_to_type_it(api, team, stt):
    stt.groq = lambda: httpx.Response(500, text="down")
    stt.gemini = lambda: httpx.Response(400, json={"error": {"message": "bad audio"}})
    res = _upload(api)
    assert res.status_code == 502 and "type the note" in res.json()["detail"]


def test_with_no_keys_at_all_the_failure_is_clean(api, team, stt, monkeypatch):
    monkeypatch.setattr(speech, "GROQ_API_KEY", None)
    monkeypatch.setattr(speech, "GEMINI_API_KEY", None)
    assert _upload(api).status_code == 502 and stt.calls == []


def test_silence_is_reported_rather_than_saved_as_an_empty_note(api, team, stt):
    stt.groq = lambda: httpx.Response(200, json={"text": "  "})
    assert _upload(api).status_code == 422


@pytest.mark.parametrize("kwargs,code", [
    ({"content_type": "image/png"}, 415),
    ({"content_type": "application/octet-stream"}, 415),
    ({"data": b""}, 400),
])
def test_bad_uploads_never_reach_a_transcriber(api, team, stt, kwargs, code):
    assert _upload(api, **kwargs).status_code == code
    assert stt.calls == []


def test_an_overlong_recording_is_refused(api, team, stt, monkeypatch):
    from routers import nco_staff_notes
    monkeypatch.setattr(nco_staff_notes, "MAX_AUDIO_BYTES", 10)
    assert _upload(api, data=b"x" * 11).status_code == 413
    assert stt.calls == []


def test_a_missing_file_is_a_validation_error(api, team, stt):
    assert api.post("/nco-notes/transcribe", headers=api.as_("staff")).status_code == 422


# ── splitting a several-NCO debrief ──────────────────────────────────────────

@pytest.fixture
def llm(monkeypatch):
    """The model's raw reply, settable per test; prompts are kept."""
    from scripts import nco_notes_ai

    state = {"reply": "{}", "prompts": []}

    def fake_generate(prompt, system_prompt, **kwargs):
        state["prompts"].append(prompt)
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"], "gemini-3.8-flash"

    monkeypatch.setattr(nco_notes_ai, "generate", fake_generate)
    return state


def _split(api, text="Amy great on drill, Ben late again", persona="staff"):
    return api.post("/nco-notes/split", headers=api.as_(persona), json={"text": text})


def test_a_debrief_is_split_into_a_note_per_nco(api, team, llm):
    llm["reply"] = json.dumps({"notes": [
        {"id": BEN, "note": "Late again."},
        {"id": AMY, "note": "Great on drill."},
        {"id": AMY, "note": "Helped a recruit."},
    ], "unassigned": ["The tea urn is broken."]})
    res = _split(api)
    assert res.status_code == 200
    body = res.json()
    # Roster (surname) order, and two parts about Amy become one note.
    assert body["notes"] == [
        {"cin": AMY, "body": "Great on drill. Helped a recruit.", "cadet_name": "Corporal Amy Able"},
        {"cin": BEN, "body": "Late again.", "cadet_name": "Sgt Ben Baker"},
    ]
    assert body["unassigned"] == ["The tea urn is broken."]
    assert body["model_label"] == "Gemini 3.8 Flash"
    # The model only gets the NCO team to choose from.
    assert "Corporal Amy Able" in llm["prompts"][0] and "Cara" not in llm["prompts"][0]
    # Nothing is saved until staff confirm.
    assert api.get("/nco-notes", headers=api.as_("staff")).json()["recent"] == []


def test_a_note_for_someone_not_on_the_nco_team_is_left_for_staff_to_place(api, team, llm):
    # A model can invent an id, or pick a cadet: neither is trusted.
    llm["reply"] = json.dumps({"notes": [
        {"id": CARA, "note": "About a cadet."}, {"id": 12345, "note": "About nobody."},
        {"id": "Amy", "note": "Not an id."}, {"id": AMY, "note": "  "},
    ]})
    body = _split(api).json()
    assert body["notes"] == []
    assert body["unassigned"] == ["About a cadet.", "About nobody.", "Not an id."]


def test_a_fenced_reply_is_still_read(api, team, llm):
    llm["reply"] = 'Here you go:\n```json\n{"notes": [{"id": 1, "note": "Fine."}]}\n```'
    assert _split(api).json()["notes"][0]["body"] == "Fine."


@pytest.mark.parametrize("reply", ["not json at all", "[1, 2]", RuntimeError("every model failed")])
def test_when_the_ai_cant_split_it_staff_are_told_to_add_them_one_by_one(api, team, llm, reply):
    llm["reply"] = reply
    res = _split(api)
    assert res.status_code == 502 and "one NCO at a time" in res.json()["detail"]


def test_an_empty_debrief_is_refused_before_the_ai_is_asked(api, team, llm):
    assert _split(api, text="   ").status_code == 400
    assert llm["prompts"] == []


def test_with_no_ncos_on_the_roster_there_is_nothing_to_split_against(api, llm):
    assert _split(api).status_code == 409
