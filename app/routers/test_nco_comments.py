"""NCO quick comments and their reply chains."""

import pytest

from database.models import Cadet, NcoComment

NCO = {"Authorization": "Bearer nco"}
NCO2 = {"Authorization": "Bearer nco2"}
STAFF = {"Authorization": "Bearer staff"}


@pytest.fixture
def cadet(db):
    db.add(Cadet(cin=1, first_name="Amy", last_name="Able", rank="Cpl", flight="A"))
    db.commit()


def _post(api, headers=NCO, **body):
    return api.post("/nco-comments", json={"subject": "Drill", "body": "Sharp tonight", **body}, headers=headers)


@pytest.mark.parametrize("method,path", [
    ("get", "/nco-comments"), ("post", "/nco-comments"), ("delete", "/nco-comments/1"),
    ("post", "/nco-comments/1/replies"), ("delete", "/nco-comments/1/replies/1"),
])
def test_cadets_are_kept_out(api, method, path):
    call = getattr(api, method)
    for headers, code in (({}, 401), (api.as_("cadet"), 403)):
        res = call(path, headers=headers) if method in ("get", "delete") else call(path, json={}, headers=headers)
        assert res.status_code == code


def test_create_general_and_cadet_comments(api, cadet):
    general = _post(api, subject="  Kit  ", body="  Store tidy  ", comment_date="2026-03-04T19:45:00Z")
    assert general.status_code == 201
    g = general.json()
    assert (g["subject"], g["body"], g["comment_date"]) == ("Kit", "Store tidy", "2026-03-04T00:00:00")
    assert g["cadet_cin"] is None and g["cadet_name"] == "" and g["is_mine"] is True

    c = _post(api, cadet_cin=1).json()
    assert (c["cadet_name"], c["cadet_flight"]) == ("Cpl Amy Able", "A")
    # Today's date by default, at midnight.
    assert c["comment_date"].endswith("T00:00:00")


@pytest.mark.parametrize("body,code", [
    ({"subject": "   "}, 400),
    ({"body": ""}, 400),
    ({"comment_date": "next week"}, 400),
    ({"cadet_cin": 99}, 404),
    ({"cadet_cin": "abc"}, 422),
])
def test_create_validation(api, cadet, body, code):
    assert _post(api, **body).status_code == code


def test_long_text_is_truncated_not_rejected(api):
    res = _post(api, subject="s" * 500, body="b" * 9000).json()
    assert len(res["subject"]) == 200 and len(res["body"]) == 5000


def test_list_order_and_permissions(api, cadet):
    old = _post(api, comment_date="2026-01-01").json()
    new = _post(api, headers=NCO2, comment_date="2026-02-01").json()
    as_nco = api.get("/nco-comments", headers=NCO).json()
    assert as_nco["is_staff"] is False
    assert [c["id"] for c in as_nco["comments"]] == [new["id"], old["id"]]
    mine = {c["id"]: c for c in as_nco["comments"]}
    assert mine[old["id"]]["can_delete"] is True and mine[new["id"]]["can_delete"] is False

    as_staff = api.get("/nco-comments", headers=STAFF).json()
    assert as_staff["is_staff"] is True and all(c["can_delete"] for c in as_staff["comments"])


def test_delete_own_or_as_staff(api):
    a = _post(api).json()["id"]
    b = _post(api).json()["id"]
    assert api.delete(f"/nco-comments/{a}", headers=NCO2).status_code == 403
    assert api.delete(f"/nco-comments/{a}", headers=NCO).json() == {"ok": True}
    assert api.delete(f"/nco-comments/{b}", headers=STAFF).json() == {"ok": True}
    assert api.delete(f"/nco-comments/{b}", headers=STAFF).status_code == 404


def test_replies(api):
    cid = _post(api).json()["id"]
    res = api.post(f"/nco-comments/{cid}/replies", json={"body": "  Agreed "}, headers=NCO2)
    assert res.status_code == 201
    reply = res.json()["replies"][0]
    assert reply["body"] == "Agreed" and reply["author_name"] == "Ned Other"

    assert api.post(f"/nco-comments/{cid}/replies", json={"body": "  "}, headers=NCO).status_code == 400
    assert api.post("/nco-comments/999/replies", json={"body": "x"}, headers=NCO).status_code == 404

    # Only the reply's author (or staff) may remove it — not the comment's author.
    assert api.delete(f"/nco-comments/{cid}/replies/{reply['id']}", headers=NCO).status_code == 403
    assert api.delete(f"/nco-comments/{cid}/replies/{reply['id']}", headers=NCO2).json()["replies"] == []
    assert api.delete(f"/nco-comments/{cid}/replies/{reply['id']}", headers=NCO2).status_code == 404
    assert api.delete(f"/nco-comments/999/replies/{reply['id']}", headers=NCO2).status_code == 404


def test_reply_under_the_wrong_comment_is_not_found(api):
    a = _post(api).json()["id"]
    b = _post(api).json()["id"]
    reply = api.post(f"/nco-comments/{a}/replies", json={"body": "x"}, headers=NCO).json()["replies"][0]
    assert api.delete(f"/nco-comments/{b}/replies/{reply['id']}", headers=NCO).status_code == 404


def test_deleting_a_comment_takes_its_replies(api, db):
    from database.models import NcoCommentReply

    cid = _post(api).json()["id"]
    api.post(f"/nco-comments/{cid}/replies", json={"body": "x"}, headers=NCO)
    api.delete(f"/nco-comments/{cid}", headers=NCO)
    assert db.query(NcoCommentReply).count() == 0


def test_note_keeps_the_cadet_name_after_they_leave(api, db, cadet):
    cid = _post(api, cadet_cin=1).json()["id"]
    db.query(NcoComment).filter(NcoComment.id == cid).update({"cadet_id": None})
    db.query(Cadet).delete()
    db.commit()
    [comment] = api.get("/nco-comments", headers=NCO).json()["comments"]
    assert comment["cadet_name"] == "Cpl Amy Able" and comment["cadet_flight"] is None


def test_a_renamed_cadet_shows_their_current_name(api, db, cadet):
    _post(api, cadet_cin=1)
    db.query(Cadet).update({"rank": "Sgt"})
    db.commit()
    assert api.get("/nco-comments", headers=NCO).json()["comments"][0]["cadet_name"] == "Sgt Amy Able"
