"""Session plans over HTTP: drafting, privacy of drafts, submit/review
round-trips with emails, comments, attachments and the PDF export."""

import pytest

import routers.session_plans as sp

NCO = {"Authorization": "Bearer nco"}
NCO2 = {"Authorization": "Bearer nco2"}
STAFF = {"Authorization": "Bearer staff"}

FULL = {"session_name": "Map reading", "aim": "Read a grid ref", "situation": "s", "mission": "m",
        "execution": "e", "session_date": "2026-05-06",
        "timetable": [{"timing": "1900", "event": "Brief", "location": "Hall"}, {"timing": " ", "event": ""}]}


@pytest.fixture(autouse=True)
def alerts(monkeypatch):
    monkeypatch.setattr(sp, "SESSION_PLAN_ALERT_EMAIL", "notify@317atc.co.uk")


def _create(api, headers=NCO, **body):
    res = api.post("/session-plans", json={**FULL, **body}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _submitted(api, **body):
    plan = _create(api, **body)
    assert api.post(f"/session-plans/{plan['id']}/submit", headers=NCO).status_code == 200
    return plan


@pytest.mark.parametrize("method,path", [
    ("get", "/session-plans"), ("post", "/session-plans"), ("get", "/session-plans/1"),
    ("put", "/session-plans/1"), ("delete", "/session-plans/1"), ("get", "/session-plans/1/pdf"),
])
def test_cadets_are_kept_out(api, method, path):
    call = getattr(api, method)
    res = call(path, headers=api.as_("cadet")) if method in ("get", "delete") else call(path, json={},
                                                                                       headers=api.as_("cadet"))
    assert res.status_code == 403


@pytest.mark.parametrize("action", ["approve", "request-amendments"])
def test_reviews_are_staff_only(api, action):
    plan = _submitted(api)
    assert api.post(f"/session-plans/{plan['id']}/{action}", json={"note": "x"}, headers=NCO2).status_code == 403


def test_create_trims_defaults_ic_and_drops_empty_rows(api):
    plan = _create(api, session_name="  Map reading  ", session_ic="")
    assert plan["session_name"] == "Map reading" and plan["session_ic"] == "Nina Corporal"
    assert plan["timetable"] == [{"timing": "1900", "event": "Brief", "location": "Hall"}]
    assert plan["session_date"] == "2026-05-06T00:00:00"
    assert (plan["status"], plan["is_author"], plan["can_edit"], plan["can_review"]) == ("draft", True, True, False)


@pytest.mark.parametrize("body,code", [
    ({"session_date": "next week"}, 400),
    ({"timetable": [{"timing": "1"}] * 41}, 400),
    ({"timetable": [{"timing": "1"}] * 40}, 200),
    ({"session_date": ""}, 200),
    ({"session_date": "2026-05-06T18:00:00.000Z"}, 200),
])
def test_create_validation(api, body, code):
    assert api.post("/session-plans", json={**FULL, **body}, headers=NCO).status_code == code


def test_drafts_are_private_until_submitted(api):
    plan = _create(api)
    pid = plan["id"]
    for path in (f"/session-plans/{pid}", f"/session-plans/{pid}/pdf"):
        assert api.get(path, headers=NCO2).status_code == 404
        assert api.get(path, headers=STAFF).status_code == 404
    assert api.get("/session-plans", headers=STAFF).json()["plans"] == []
    assert [p["id"] for p in api.get("/session-plans", headers=NCO).json()["plans"]] == [pid]

    api.post(f"/session-plans/{pid}/submit", headers=NCO)
    assert api.get(f"/session-plans/{pid}", headers=NCO2).status_code == 200
    assert [p["id"] for p in api.get("/session-plans", headers=STAFF).json()["plans"]] == [pid]


def test_submit_requires_the_core_sections(api, outbox):
    plan = _create(api, aim=" ", mission="")
    res = api.post(f"/session-plans/{plan['id']}/submit", headers=NCO)
    assert res.status_code == 400
    assert "Aim/goal" in res.json()["detail"] and "Mission" in res.json()["detail"]
    assert outbox == []


def test_full_review_round_trip(api, outbox):
    plan = _submitted(api)
    pid = plan["id"]
    assert outbox[-1]["to"] == "notify@317atc.co.uk" and "submitted" in outbox[-1]["subject"]

    # Locked while with staff.
    assert api.put(f"/session-plans/{pid}", json=FULL, headers=NCO).status_code == 409
    assert api.post(f"/session-plans/{pid}/submit", headers=NCO).status_code == 409
    assert api.get(f"/session-plans/{pid}", headers=STAFF).json()["can_review"] is True

    # Sending back needs something to say; unknown sections are dropped.
    assert api.post(f"/session-plans/{pid}/request-amendments", json={"feedback": {"bogus": "x"}},
                    headers=STAFF).status_code == 400
    back = api.post(f"/session-plans/{pid}/request-amendments", headers=STAFF,
                    json={"note": "Nearly", "feedback": {"mission": " Be specific ", "aim": "ignored"}}).json()
    assert back["status"] == "amendments_requested"
    assert back["feedback"] == {"mission": "Be specific"} and back["amendment_note"] == "Nearly"
    assert outbox[-1]["to"] == "nco@317atc.co.uk" and "Be specific" in outbox[-1]["html"]

    # Author fixes it and resubmits; feedback is kept for context.
    edited = api.put(f"/session-plans/{pid}", json={**FULL, "mission": "Better"}, headers=NCO).json()
    assert edited["mission"] == "Better" and edited["feedback"] == {"mission": "Be specific"}
    api.post(f"/session-plans/{pid}/submit", headers=NCO)
    assert "resubmitted" in outbox[-1]["subject"]

    approved = api.post(f"/session-plans/{pid}/approve", json={"note": "  "}, headers=STAFF).json()
    assert approved["status"] == "approved" and approved["amendment_note"] is None
    assert approved["reviewed_by"] == "staff@317atc.co.uk"
    assert "approved" in outbox[-1]["subject"]

    # Approved plans are locked for good.
    assert api.put(f"/session-plans/{pid}", json=FULL, headers=NCO).status_code == 409
    assert api.post(f"/session-plans/{pid}/approve", json={}, headers=STAFF).status_code == 409
    assert api.delete(f"/session-plans/{pid}", headers=NCO).status_code == 409
    pdf = api.get(f"/session-plans/{pid}/pdf", headers=NCO2)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_reviewing_a_draft_is_refused(api):
    # Staff can't even see someone else's draft.
    plan = _create(api)
    assert api.post(f"/session-plans/{plan['id']}/approve", json={}, headers=STAFF).status_code == 404
    own = _create(api, headers=STAFF)
    assert api.post(f"/session-plans/{own['id']}/approve", json={}, headers=STAFF).status_code == 409
    assert api.post(f"/session-plans/{own['id']}/request-amendments", json={"note": "x"},
                    headers=STAFF).status_code == 409


def test_no_alert_address_sends_nothing(api, outbox, monkeypatch):
    monkeypatch.setattr(sp, "SESSION_PLAN_ALERT_EMAIL", "")
    _submitted(api)
    assert outbox == []


def test_only_the_author_edits_submits_deletes(api):
    plan = _submitted(api)
    pid = plan["id"]
    api.post(f"/session-plans/{pid}/request-amendments", json={"note": "x"}, headers=STAFF)
    for method, path in (("put", f"/session-plans/{pid}"), ("post", f"/session-plans/{pid}/submit"),
                         ("delete", f"/session-plans/{pid}")):
        call = getattr(api, method)
        res = call(path, headers=STAFF) if method == "delete" else call(path, json=FULL, headers=STAFF)
        assert res.status_code == 403
    assert api.delete(f"/session-plans/{pid}", headers=NCO).json() == {"ok": True}
    assert api.get(f"/session-plans/{pid}", headers=NCO).status_code == 404


def test_comments(api):
    plan = _submitted(api)
    pid = plan["id"]
    res = api.post(f"/session-plans/{pid}/comments", json={"body": "  Nice  "}, headers=NCO2).json()
    comment = res["comments"][0]
    assert comment["body"] == "Nice" and comment["can_delete"] is True
    assert res["status"] == "submitted"  # commenting never changes the status
    assert api.post(f"/session-plans/{pid}/comments", json={"body": " "}, headers=NCO2).status_code == 400

    assert api.delete(f"/session-plans/{pid}/comments/{comment['id']}", headers=NCO).status_code == 403
    assert api.delete(f"/session-plans/{pid}/comments/{comment['id']}", headers=STAFF).json()["comments"] == []
    assert api.delete(f"/session-plans/{pid}/comments/{comment['id']}", headers=STAFF).status_code == 404

    long = api.post(f"/session-plans/{pid}/comments", json={"body": "x" * 6000}, headers=NCO).json()
    assert len(long["comments"][0]["body"]) == 5000


def test_cannot_comment_on_someone_elses_draft(api):
    plan = _create(api)
    assert api.post(f"/session-plans/{plan['id']}/comments", json={"body": "x"}, headers=NCO2).status_code == 404


def test_attachments(api):
    plan = _create(api)
    pid = plan["id"]
    res = api.post(f"/session-plans/{pid}/attachments", headers=NCO, files=[
        ("files", ("map.png", b"\x89PNG", "image/png")),
        ("files", ("Café route.pdf", b"%PDF", "application/pdf")),
    ])
    assert res.status_code == 200
    atts = res.json()["attachments"]
    assert [a["filename"] for a in atts] == ["map.png", "Café route.pdf"]

    got = api.get(f"/session-plans/{pid}/attachments/{atts[1]['id']}", headers=NCO)
    assert got.status_code == 200 and got.content == b"%PDF"
    assert "filename*=UTF-8''Caf%C3%A9%20route.pdf" in got.headers["content-disposition"]

    # Another NCO can't fetch a draft's attachment.
    assert api.get(f"/session-plans/{pid}/attachments/{atts[0]['id']}", headers=NCO2).status_code == 404
    assert api.delete(f"/session-plans/{pid}/attachments/{atts[0]['id']}", headers=NCO).json()["attachments"][0][
        "filename"] == "Café route.pdf"
    assert api.delete(f"/session-plans/{pid}/attachments/{atts[0]['id']}", headers=NCO).status_code == 404
    assert api.get(f"/session-plans/{pid}/attachments/999", headers=NCO).status_code == 404


@pytest.mark.parametrize("file,detail", [
    (("x.gif", b"GIF", "image/gif"), "only PNG, JPEG, WebP or PDF"),
    (("big.png", b"0" * (5 * 1024 * 1024 + 1), "image/png"), "under 5 MB"),
])
def test_attachment_rejections(api, file, detail):
    plan = _create(api)
    res = api.post(f"/session-plans/{plan['id']}/attachments", headers=NCO, files=[("files", file)])
    assert res.status_code == 400 and detail in res.json()["detail"]


def test_attachments_locked_once_submitted_and_author_only(api):
    plan = _create(api)
    pid = plan["id"]
    att = api.post(f"/session-plans/{pid}/attachments", headers=NCO,
                   files=[("files", ("m.png", b"x", "image/png"))]).json()["attachments"][0]
    api.post(f"/session-plans/{pid}/submit", headers=NCO)
    assert api.post(f"/session-plans/{pid}/attachments", headers=NCO,
                    files=[("files", ("m.png", b"x", "image/png"))]).status_code == 409
    assert api.delete(f"/session-plans/{pid}/attachments/{att['id']}", headers=NCO).status_code == 409
    assert api.post(f"/session-plans/{pid}/attachments", headers=NCO2,
                    files=[("files", ("m.png", b"x", "image/png"))]).status_code == 403
    assert api.delete(f"/session-plans/{pid}/attachments/{att['id']}", headers=NCO2).status_code == 403


@pytest.mark.parametrize("name,expected", [
    ("Map reading", 'filename="Map reading.pdf"'),
    ("../../etc/passwd", 'filename="etcpasswd.pdf"'),
    ("!!!", 'filename="session-plan.pdf"'),
])
def test_pdf_filename_is_sanitised(api, name, expected):
    plan = _create(api, session_name=name)
    res = api.get(f"/session-plans/{plan['id']}/pdf", headers=NCO)
    assert res.status_code == 200 and expected in res.headers["content-disposition"]
