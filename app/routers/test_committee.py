"""Committee purchase requests: the full submit → committee → decision →
receipts → payment → paid workflow, who may do each step, and every
out-of-order transition."""

import pytest

import routers.committee as committee
from core.crypto import encrypt_password
from database.models import CommitteeRequest, User, UserProfile

STAFF = {"Authorization": "Bearer staff"}
OTHER = {"Authorization": "Bearer staff2"}
OC = {"Authorization": "Bearer oc"}

ITEMS = [{"description": "Tent", "cost": 120.555}, {"description": "  ", "cost": 99}, {"description": "Pegs", "cost": 4.5}]


@pytest.fixture(autouse=True)
def mailboxes(monkeypatch):
    monkeypatch.setattr(committee, "OC_EMAIL", "oc@317atc.co.uk")
    monkeypatch.setattr(committee, "COMMITTEE_EMAIL", "committee@317atc.co.uk")


def _create(api, headers=STAFF, **body):
    payload = {"title": "Camping kit", "justification": "Annual camp", "items": ITEMS, **body}
    return api.post("/committee-requests", json=payload, headers=headers)


def _bank_details(db, email="staff@317atc.co.uk"):
    user = db.query(User).filter(User.email == email).one()
    db.add(UserProfile(user_id=user.id, bank_account_name=encrypt_password("S Staff"),
                       bank_sort_code=encrypt_password("12-34-56"),
                       bank_account_number=encrypt_password("12345678")))
    db.commit()


def _to_approved(api, rid):
    assert api.post(f"/committee-requests/{rid}/send-to-committee", headers=OC).status_code == 200
    assert api.post(f"/committee-requests/{rid}/approve", headers=OC).status_code == 200


def _upload(api, rid, files=None, headers=STAFF):
    files = files or [("files", ("receipt.pdf", b"%PDF-1.4 receipt", "application/pdf"))]
    return api.post(f"/committee-requests/{rid}/receipts", files=files, headers=headers)


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_non_staff_cannot_see_or_create(api, persona):
    h = api.as_(persona)
    assert api.get("/committee-requests", headers=h).status_code == 403
    assert _create(api, headers=h).status_code == 403


@pytest.mark.parametrize("action", ["send-to-committee", "approve", "reject", "mark-paid"])
def test_oc_only_actions_refuse_other_staff(api, action):
    rid = _create(api).json()["id"]
    res = api.post(f"/committee-requests/{rid}/{action}", json={}, headers=STAFF)
    assert res.status_code == 403


# ── create ────────────────────────────────────────────────────────────────────

def test_create_cleans_items_totals_and_emails_the_oc(api, outbox):
    res = _create(api)
    assert res.status_code == 200
    body = res.json()
    assert body["reference"].startswith("CR-") and body["reference"].endswith("-001")
    assert body["items"] == [{"description": "Tent", "cost": 120.56}, {"description": "Pegs", "cost": 4.5}]
    assert body["total"] == 125.06
    assert body["status"] == "submitted"
    assert body["is_requester"] is True and body["can_approve"] is False
    assert body["requester_name"] == "Sam Staff"

    assert len(outbox) == 1
    mail = outbox[0]
    assert mail["to"] == "oc@317atc.co.uk" and body["reference"] in mail["subject"]
    assert mail["attachment"].startswith(b"%PDF")


def test_references_count_up_per_year(api):
    refs = [_create(api).json()["reference"] for _ in range(3)]
    assert [r[-3:] for r in refs] == ["001", "002", "003"]


def test_no_oc_configured_means_no_email(api, outbox, monkeypatch):
    monkeypatch.setattr(committee, "OC_EMAIL", "")
    assert _create(api).status_code == 200
    assert outbox == []


@pytest.mark.parametrize("body,detail", [
    ({"title": "   "}, "title"),
    ({"items": []}, "at least one item"),
    ({"items": [{"description": " ", "cost": 3}]}, "at least one item"),
    ({"items": [{"description": "Refund", "cost": -1}]}, "negative"),
])
def test_create_rejections(api, body, detail):
    res = _create(api, **body)
    assert res.status_code == 400 and detail in res.json()["detail"]


def test_create_schema_validation(api):
    assert api.post("/committee-requests", json={"title": "x"}, headers=STAFF).status_code == 422
    bad = {"title": "x", "items": [{"description": "a", "cost": "lots"}]}
    assert api.post("/committee-requests", json=bad, headers=STAFF).status_code == 422


# ── read ──────────────────────────────────────────────────────────────────────

def test_list_detail_and_pdf(api):
    rid = _create(api).json()["id"]
    second = _create(api, title="Second").json()["id"]
    listing = api.get("/committee-requests", headers=OC).json()
    assert listing["is_oc"] is True
    assert [r["id"] for r in listing["requests"]] == [second, rid]
    assert api.get("/committee-requests", headers=STAFF).json()["is_oc"] is False

    as_oc = api.get(f"/committee-requests/{rid}", headers=OC).json()
    assert as_oc["can_approve"] is True and as_oc["is_requester"] is False

    pdf = api.get(f"/committee-requests/{rid}/pdf", headers=OTHER)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_missing_request_404s(api):
    for path in ("/committee-requests/9", "/committee-requests/9/pdf"):
        assert api.get(path, headers=STAFF).status_code == 404
    for action in ("send-to-committee", "approve", "reject", "mark-paid"):
        assert api.post(f"/committee-requests/9/{action}", json={}, headers=OC).status_code == 404
    for action in ("send-for-payment", "withdraw"):
        assert api.post(f"/committee-requests/9/{action}", headers=STAFF).status_code == 404


def test_pdf_missing_is_404(api, db):
    rid = _create(api).json()["id"]
    db.get(CommitteeRequest, rid).pdf_data = None
    db.commit()
    assert api.get(f"/committee-requests/{rid}/pdf", headers=STAFF).status_code == 404


# ── the happy path end to end ─────────────────────────────────────────────────

def test_full_workflow(api, db, outbox):
    rid = _create(api).json()["id"]
    outbox.clear()

    sent = api.post(f"/committee-requests/{rid}/send-to-committee", headers=OC).json()
    assert sent["status"] == "sent_to_committee" and sent["sent_to_committee_by"] == "oc@317atc.co.uk"
    assert outbox[-1]["to"] == "committee@317atc.co.uk"

    approved = api.post(f"/committee-requests/{rid}/approve", headers=OC).json()
    assert approved["status"] == "approved" and approved["decided_by"] == "oc@317atc.co.uk"
    assert outbox[-1]["to"] == "staff@317atc.co.uk" and "approved" in outbox[-1]["subject"]

    up = _upload(api, rid, files=[
        ("files", ("a.png", b"\x89PNG", "image/png")),
        ("files", ("b.jpg", b"\xff\xd8", "image/jpeg")),
    ]).json()
    assert [r["filename"] for r in up["receipts"]] == ["a.png", "b.jpg"]
    receipt_id = up["receipts"][0]["id"]
    got = api.get(f"/committee-requests/{rid}/receipts/{receipt_id}", headers=OTHER)
    assert got.content == b"\x89PNG" and got.headers["content-type"] == "image/png"

    _bank_details(db)
    outbox.clear()
    paid_req = api.post(f"/committee-requests/{rid}/send-for-payment", headers=STAFF).json()
    assert paid_req["status"] == "sent_for_payment"
    mail = outbox[0]
    assert mail["to"] == "committee@317atc.co.uk"
    # Decrypted bank details go into the email body, plus every receipt and the PDF.
    assert "12-34-56" in mail["html"] and "12345678" in mail["html"]
    assert [a[0] for a in mail["attachments"]] == ["a.png", "b.jpg", f"{paid_req['reference']}.pdf"]

    paid = api.post(f"/committee-requests/{rid}/mark-paid", headers=OC).json()
    assert paid["status"] == "paid" and paid["paid_marked_by"] == "oc@317atc.co.uk"

    # Terminal: nothing else is possible.
    assert api.post(f"/committee-requests/{rid}/withdraw", headers=STAFF).status_code == 409
    assert api.post(f"/committee-requests/{rid}/mark-paid", headers=OC).status_code == 409


# ── out of order ──────────────────────────────────────────────────────────────

def test_state_machine_rejects_out_of_order_steps(api):
    rid = _create(api).json()["id"]
    assert api.post(f"/committee-requests/{rid}/approve", headers=OC).status_code == 409
    assert api.post(f"/committee-requests/{rid}/mark-paid", headers=OC).status_code == 409
    assert _upload(api, rid).status_code == 409
    assert api.post(f"/committee-requests/{rid}/send-for-payment", headers=STAFF).status_code == 409

    api.post(f"/committee-requests/{rid}/send-to-committee", headers=OC)
    assert api.post(f"/committee-requests/{rid}/send-to-committee", headers=OC).status_code == 409


def test_send_to_committee_needs_a_committee_address(api, monkeypatch):
    rid = _create(api).json()["id"]
    monkeypatch.setattr(committee, "COMMITTEE_EMAIL", "")
    res = api.post(f"/committee-requests/{rid}/send-to-committee", headers=OC)
    assert res.status_code == 400
    assert api.get(f"/committee-requests/{rid}", headers=STAFF).json()["status"] == "submitted"


@pytest.mark.parametrize("stage", ["submitted", "sent_to_committee"])
def test_reject_from_either_waiting_state(api, outbox, stage):
    rid = _create(api).json()["id"]
    if stage == "sent_to_committee":
        api.post(f"/committee-requests/{rid}/send-to-committee", headers=OC)
    res = api.post(f"/committee-requests/{rid}/reject", json={"reason": "  Too dear  "}, headers=OC).json()
    assert res["status"] == "rejected" and res["rejection_reason"] == "Too dear"
    assert "Too dear" in outbox[-1]["html"]
    assert api.post(f"/committee-requests/{rid}/reject", json={}, headers=OC).status_code == 409


def test_reject_without_reason_stores_none(api):
    rid = _create(api).json()["id"]
    res = api.post(f"/committee-requests/{rid}/reject", json={"reason": "   "}, headers=OC).json()
    assert res["rejection_reason"] is None


def test_withdraw(api, outbox):
    rid = _create(api).json()["id"]
    assert api.post(f"/committee-requests/{rid}/withdraw", headers=OTHER).status_code == 403
    res = api.post(f"/committee-requests/{rid}/withdraw", headers=STAFF).json()
    assert res["status"] == "withdrawn" and res["withdrawn_by"] == "staff@317atc.co.uk"
    assert "withdrawn" in outbox[-1]["subject"]
    assert api.post(f"/committee-requests/{rid}/withdraw", headers=STAFF).status_code == 409


# ── receipts ──────────────────────────────────────────────────────────────────

def test_only_the_requester_manages_receipts(api):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    assert _upload(api, rid, headers=OTHER).status_code == 403
    rec = _upload(api, rid).json()["receipts"][0]["id"]
    assert api.delete(f"/committee-requests/{rid}/receipts/{rec}", headers=OTHER).status_code == 403
    assert api.post(f"/committee-requests/{rid}/send-for-payment", headers=OTHER).status_code == 403


@pytest.mark.parametrize("file,detail", [
    (("r.gif", b"GIF89a", "image/gif"), "only PNG, JPEG or PDF"),
    (("r.exe", b"MZ", "application/octet-stream"), "only PNG, JPEG or PDF"),
    (("big.pdf", b"0" * (5 * 1024 * 1024 + 1), "application/pdf"), "under 5 MB"),
])
def test_receipt_type_and_size_limits(api, file, detail):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    res = _upload(api, rid, files=[("files", file)])
    assert res.status_code == 400 and detail in res.json()["detail"]


def test_a_bad_file_in_a_batch_saves_none_of_it(api):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    res = _upload(api, rid, files=[("files", ("ok.pdf", b"%PDF", "application/pdf")),
                                   ("files", ("bad.gif", b"GIF", "image/gif"))])
    assert res.status_code == 400
    assert api.get(f"/committee-requests/{rid}", headers=STAFF).json()["receipts"] == []


def test_receipt_exactly_5mb_is_allowed(api):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    files = [("files", ("edge.pdf", b"0" * (5 * 1024 * 1024), "application/pdf"))]
    assert _upload(api, rid, files=files).status_code == 200


def test_receipt_with_an_awkward_filename_still_downloads(api):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    up = _upload(api, rid, files=[("files", ("Café – receipt.pdf", b"%PDF", "application/pdf"))]).json()
    res = api.get(f"/committee-requests/{rid}/receipts/{up['receipts'][0]['id']}", headers=STAFF)
    assert res.status_code == 200
    assert res.headers["content-disposition"].startswith('inline; filename="Cafe  receipt.pdf"')


def test_delete_receipt_and_404s(api):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    rec = _upload(api, rid).json()["receipts"][0]["id"]
    res = api.delete(f"/committee-requests/{rid}/receipts/{rec}", headers=STAFF).json()
    assert res["receipts"] == []
    assert api.delete(f"/committee-requests/{rid}/receipts/{rec}", headers=STAFF).status_code == 404
    assert api.get(f"/committee-requests/{rid}/receipts/{rec}", headers=STAFF).status_code == 404
    # A receipt id under the wrong request is not found.
    other = _create(api).json()["id"]
    _to_approved(api, other)
    theirs = _upload(api, other).json()["receipts"][0]["id"]
    assert api.get(f"/committee-requests/{rid}/receipts/{theirs}", headers=STAFF).status_code == 404


def test_receipts_are_frozen_after_sending_for_payment(api, db):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    rec = _upload(api, rid).json()["receipts"][0]["id"]
    _bank_details(db)
    api.post(f"/committee-requests/{rid}/send-for-payment", headers=STAFF)
    assert api.delete(f"/committee-requests/{rid}/receipts/{rec}", headers=STAFF).status_code == 409
    assert _upload(api, rid).status_code == 409


# ── send for payment preconditions ────────────────────────────────────────────

def test_send_for_payment_needs_receipts_bank_details_and_an_address(api, db, monkeypatch):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    res = api.post(f"/committee-requests/{rid}/send-for-payment", headers=STAFF)
    assert res.status_code == 400 and "receipt" in res.json()["detail"]

    _upload(api, rid)
    res = api.post(f"/committee-requests/{rid}/send-for-payment", headers=STAFF)
    assert res.status_code == 400 and "bank details" in res.json()["detail"]

    # Bank details that can't be decrypted (key rotated) count as missing.
    user = db.query(User).filter(User.email == "staff@317atc.co.uk").one()
    db.add(UserProfile(user_id=user.id, bank_account_name="garbage", bank_sort_code="garbage",
                       bank_account_number="garbage"))
    db.commit()
    res = api.post(f"/committee-requests/{rid}/send-for-payment", headers=STAFF)
    assert res.status_code == 400 and "bank details" in res.json()["detail"]

    monkeypatch.setattr(committee, "COMMITTEE_EMAIL", "")
    res = api.post(f"/committee-requests/{rid}/send-for-payment", headers=STAFF)
    assert res.status_code == 400 and "COMMITTEE_EMAIL" in res.json()["detail"]
