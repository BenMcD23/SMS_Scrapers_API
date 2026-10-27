"""Committee purchase requests: the full submit → committee → decision →
receipts → payment → paid workflow, who may do each step, and every
out-of-order transition."""

import io

import pytest
from pypdf import PdfReader

import routers.committee as committee
from core.crypto import encrypt_password
from database.models import AuditEvent, CommitteeRequest, User, UserProfile

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
    # An old client sends only a cost: it reads as one item at that price.
    assert body["items"] == [
        {"description": "Tent", "quantity": 1, "unit_cost": 120.56, "cost": 120.56},
        {"description": "Pegs", "quantity": 1, "unit_cost": 4.5, "cost": 4.5},
    ]
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


def test_only_the_first_oc_is_emailed(api, outbox, monkeypatch):
    monkeypatch.setattr(committee, "OC_EMAIL", "oc@317atc.co.uk, extra@317atc.co.uk")
    assert _create(api).status_code == 200
    assert [m["to"] for m in outbox] == ["oc@317atc.co.uk"]


@pytest.mark.parametrize("body,detail", [
    ({"title": "   "}, "title"),
    ({"items": []}, "at least one item"),
    ({"items": [{"description": " ", "cost": 3}]}, "at least one item"),
    ({"items": [{"description": "Refund", "cost": -1}]}, "negative"),
    ({"items": [{"description": "Refund", "quantity": 2, "unit_cost": -1}]}, "negative"),
    ({"items": [{"description": "Tent", "quantity": 0, "unit_cost": 5}]}, "quantity of at least 1"),
    ({"items": [{"description": "Tent", "quantity": 2}]}, "unit cost or a total"),
])
def test_create_rejections(api, body, detail):
    res = _create(api, **body)
    assert res.status_code == 400 and detail in res.json()["detail"]


def test_quantity_and_unit_cost_fill_the_line_total(api):
    body = _create(api, items=[
        {"description": "Radio", "quantity": 3, "unit_cost": 24.99},
        # A multi-buy: the requester typed their own total over 4 × £2.50.
        {"description": "Batteries", "quantity": 4, "unit_cost": 2.5, "cost": 8},
        # Only a total and a quantity: the unit cost is worked back from it.
        {"description": "Antenna", "quantity": 2, "cost": 15},
    ]).json()
    assert body["items"] == [
        {"description": "Radio", "quantity": 3, "unit_cost": 24.99, "cost": 74.97},
        {"description": "Batteries", "quantity": 4, "unit_cost": 2.5, "cost": 8},
        {"description": "Antenna", "quantity": 2, "unit_cost": 7.5, "cost": 15},
    ]
    assert body["total"] == 97.97


def test_requests_stored_before_quantities_read_as_one_each(api, db):
    rid = _create(api).json()["id"]
    db.get(CommitteeRequest, rid).items = [{"description": "Old tent", "cost": 80}]
    db.commit()
    assert api.get(f"/committee-requests/{rid}", headers=STAFF).json()["items"] == [
        {"description": "Old tent", "quantity": 1, "unit_cost": 80.0, "cost": 80.0},
    ]


def _pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


def test_pdf_puts_the_justification_above_an_items_table(api):
    rid = _create(api, justification="Our tents are worn out & leak", items=[
        {"description": "Tent <large>", "quantity": 2, "unit_cost": 60},
    ]).json()["id"]
    text = _pdf_text(api.get(f"/committee-requests/{rid}/pdf", headers=STAFF).content)
    assert text.index("Our tents are worn out & leak") < text.index("Items")
    for cell in ("Qty", "Unit cost", "Tent <large>", "£60.00", "£120.00"):
        assert cell in text


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


# ── the OC's edit ─────────────────────────────────────────────────────────────

def test_the_oc_edits_the_text_and_items_and_the_pdf_follows(api, db):
    rid = _create(api).json()["id"]
    res = api.patch(f"/committee-requests/{rid}", headers=OC, json={
        "title": "  Camping kit (revised)  ",
        "justification": "Two patrols share one tent",
        "items": [{"description": "Tent", "quantity": 2, "unit_cost": 100, "cost": 190}],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Camping kit (revised)"
    assert body["justification"] == "Two patrols share one tent"
    assert body["items"] == [{"description": "Tent", "quantity": 2, "unit_cost": 100, "cost": 190}]
    assert body["total"] == 190
    # The requester and status are untouched by an edit.
    assert body["status"] == "submitted" and body["requester_name"] == "Sam Staff"

    text = _pdf_text(api.get(f"/committee-requests/{rid}/pdf", headers=STAFF).content)
    assert "Camping kit (revised)" in text and "Two patrols share one tent" in text
    assert "£190.00" in text and "Annual camp" not in text

    audit = db.query(AuditEvent).filter_by(resource="committee_request", resource_id=str(rid)).one()
    assert audit.action == "update" and audit.actor_email == "oc@317atc.co.uk"
    assert audit.detail == {"fields": ["title", "justification", "items"]}


def test_an_edit_of_one_field_leaves_the_rest(api):
    created = _create(api).json()
    body = api.patch(f"/committee-requests/{created['id']}", headers=OC,
                     json={"justification": ""}).json()
    assert body["justification"] == ""
    assert body["title"] == created["title"] and body["items"] == created["items"]


def test_an_empty_edit_changes_nothing(api, db):
    rid = _create(api).json()["id"]
    pdf = db.get(CommitteeRequest, rid).pdf_data
    assert api.patch(f"/committee-requests/{rid}", headers=OC, json={}).status_code == 200
    db.expire_all()
    assert db.get(CommitteeRequest, rid).pdf_data == pdf
    assert db.query(AuditEvent).filter_by(resource="committee_request").count() == 0


@pytest.mark.parametrize("headers", [STAFF, OTHER])
def test_only_the_oc_edits_not_even_the_requester(api, headers):
    rid = _create(api).json()["id"]
    assert api.patch(f"/committee-requests/{rid}", headers=headers, json={"title": "Mine"}).status_code == 403


@pytest.mark.parametrize("persona", ["snco", "nco", "cadet"])
def test_non_staff_cannot_edit(api, persona):
    rid = _create(api).json()["id"]
    assert api.patch(f"/committee-requests/{rid}", headers=api.as_(persona), json={"title": "x"}).status_code == 403


def test_editing_needs_a_token(api):
    rid = _create(api).json()["id"]
    assert api.patch(f"/committee-requests/{rid}", json={"title": "x"}).status_code == 401


def test_editing_a_missing_request_404s(api):
    assert api.patch("/committee-requests/9", headers=OC, json={"title": "x"}).status_code == 404


@pytest.mark.parametrize("body,detail", [
    ({"title": " "}, "title"),
    ({"items": []}, "at least one item"),
    ({"items": [{"description": "Tent", "quantity": 1, "unit_cost": -5}]}, "negative"),
])
def test_edit_rejections_leave_the_request_as_it_was(api, body, detail):
    created = _create(api).json()
    res = api.patch(f"/committee-requests/{created['id']}", headers=OC, json=body)
    assert res.status_code == 400 and detail in res.json()["detail"]
    after = api.get(f"/committee-requests/{created['id']}", headers=STAFF).json()
    assert after["title"] == created["title"] and after["items"] == created["items"]


def test_the_oc_can_edit_at_every_open_stage(api, db):
    rid = _create(api).json()["id"]
    _to_approved(api, rid)
    assert api.patch(f"/committee-requests/{rid}", headers=OC, json={"title": "Approved kit"}).status_code == 200


def test_paid_and_withdrawn_requests_are_closed_records(api, db):
    withdrawn = _create(api).json()["id"]
    api.post(f"/committee-requests/{withdrawn}/withdraw", headers=STAFF)
    res = api.patch(f"/committee-requests/{withdrawn}", headers=OC, json={"title": "x"})
    assert res.status_code == 409

    paid = _create(api).json()["id"]
    db.get(CommitteeRequest, paid).status = "paid"
    db.commit()
    assert api.patch(f"/committee-requests/{paid}", headers=OC, json={"title": "x"}).status_code == 409


def test_rejection_reason_is_editable_only_once_rejected(api):
    rid = _create(api).json()["id"]
    res = api.patch(f"/committee-requests/{rid}", headers=OC, json={"rejection_reason": "No"})
    assert res.status_code == 409

    api.post(f"/committee-requests/{rid}/reject", json={"reason": "Too dear"}, headers=OC)
    body = api.patch(f"/committee-requests/{rid}", headers=OC,
                     json={"rejection_reason": " Over budget this year "}).json()
    assert body["rejection_reason"] == "Over budget this year"
    cleared = api.patch(f"/committee-requests/{rid}", headers=OC, json={"rejection_reason": ""}).json()
    assert cleared["rejection_reason"] is None
