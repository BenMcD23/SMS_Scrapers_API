"""The contact audit's rules, on plain cadets (no database needed)."""

from datetime import datetime
from types import SimpleNamespace

from core.contact_audit import BAD, CHECKS, GOOD, NOT_APPLICABLE, NOT_CHECKED, audit_cadet, export_table, method_kind

READ_AT = datetime(2026, 11, 20, 19, 30)


def m(method, detail):
    return {"method": method, "detail": detail}


def contact(name, *methods, relation="Mother"):
    return SimpleNamespace(name=name, title="Mrs", relation=relation, next_of_kin=True, contact_methods=list(methods))


MUM = contact("Sarah Able", m("Mobile Phone", "07700 900111"), m("Primary Email", "sarah@example.com"))
DAD = contact("David Able", m("Home Phone", "0161 496 0000"), m("Secondary Email", "dave@example.com"),
              relation="Father")
OWN = [m("Mobile Phone", "07700900222"), m("Personal Account (Bader)", "amy.able100@rafac.mod.gov.uk"),
       m("Primary Email", "amy@317atc.co.uk")]


def cadet(methods=OWN, contacts=(MUM, DAD), checked=READ_AT):
    return SimpleNamespace(cin=1001, first_name="Amy", last_name="Able", rank="Cdt", flight="A",
                           contact_methods=methods, contacts=list(contacts), contacts_checked_at=checked)


def test_a_cadet_with_their_own_details_and_two_complete_parents_is_good():
    row = audit_cadet(cadet())
    assert row["checks"] == {key: GOOD for key, _ in CHECKS}
    assert row["result"] == GOOD and row["problems"] == []
    assert row["phones"] == ["07700900222"] and row["emails"] == ["amy@317atc.co.uk"]
    assert row["contacts"][1] == {"name": "David Able", "title": "Mrs", "relationship": "Father",
                                  "next_of_kin": True, "emails": ["dave@example.com"], "phones": ["0161 496 0000"]}


def test_the_bader_account_is_not_an_email_the_cadet_set():
    # Every cadet has one, so counting it would pass everybody.
    row = audit_cadet(cadet(methods=[m("Personal Account (Bader)", "amy@rafac.mod.gov.uk"),
                                     m("Mobile Phone", "07700900222")]))
    assert row["checks"]["cadet_email"] == BAD
    assert row["checks"]["email_differs"] == NOT_APPLICABLE
    assert "No email for the cadet" in row["problems"]
    assert row["result"] == BAD


def test_no_phone_is_bad():
    row = audit_cadet(cadet(methods=[m("Primary Email", "amy@317atc.co.uk")]))
    assert row["checks"]["cadet_phone"] == BAD
    assert row["problems"] == ["No phone for the cadet"]


def test_a_cadets_phone_matching_a_parents_is_bad_however_its_written():
    row = audit_cadet(cadet(methods=[m("Mobile Phone", "+44 7700 900111"), m("Primary Email", "amy@x.com")]))
    assert row["checks"]["phone_differs"] == BAD
    assert row["checks"]["email_differs"] == GOOD
    assert row["problems"] == ["Cadet phone is the same as Sarah Able's"]


def test_a_cadets_email_matching_a_parents_is_bad_whatever_the_case():
    row = audit_cadet(cadet(methods=[m("Mobile Phone", "07700900222"), m("Secondary Email", "Dave@Example.com ")]))
    assert row["checks"]["email_differs"] == BAD
    assert row["problems"] == ["Cadet email is the same as David Able's"]


def test_no_parent_contact_is_bad():
    row = audit_cadet(cadet(contacts=()))
    assert row["checks"]["has_contact"] == BAD
    assert row["checks"]["contacts_complete"] == NOT_APPLICABLE
    assert row["checks"]["phone_differs"] == NOT_APPLICABLE
    assert row["problems"] == ["No parent contact"]


def test_every_parent_needs_both_an_email_and_a_phone():
    gran = contact("Joan Able", m("Home Phone", "0161 496 0001"), relation="Grandparent")
    nothing = contact("Bob Able", relation="Father")
    row = audit_cadet(cadet(contacts=(MUM, gran, nothing)))
    assert row["checks"]["has_contact"] == GOOD
    assert row["checks"]["contacts_complete"] == BAD
    assert row["problems"] == ["Joan Able has no email", "Bob Able has no email or phone"]


def test_a_single_parent_without_a_phone_is_bad_too():
    row = audit_cadet(cadet(contacts=(contact("Sarah Able", m("Primary Email", "s@example.com")),)))
    assert row["checks"]["contacts_complete"] == BAD
    assert row["problems"] == ["Sarah Able has no phone"]


def test_never_scraped_is_not_checked_rather_than_bad():
    row = audit_cadet(cadet(methods=None, contacts=(), checked=None))
    assert set(row["checks"].values()) == {NOT_CHECKED}
    assert row["result"] == NOT_CHECKED and row["problems"] == []
    assert row["contacts_checked_at"] is None


def test_contacts_not_yet_read_dont_hide_a_missing_phone():
    row = audit_cadet(cadet(methods=[m("Primary Email", "amy@x.com")], checked=None))
    assert row["checks"]["has_contact"] == NOT_CHECKED
    assert row["checks"]["cadet_phone"] == BAD
    assert row["result"] == BAD  # a known problem outranks an unknown


def test_malformed_methods_are_ignored_not_a_crash():
    row = audit_cadet(cadet(methods=["junk", {"method": None}, m("Mobile Phone", ""), m("Fax", "123")]))
    assert row["phones"] == [] and row["emails"] == []


def test_method_names():
    assert method_kind("Mobile Phone") == "phone"
    assert method_kind("Home Phone") == "phone"
    assert method_kind("Primary Email") == "email"
    assert method_kind("Secondary Email") == "email"
    assert method_kind("Personal Account (Bader)") is None
    assert method_kind(None) is None


def test_the_sheet_has_a_block_per_parent_up_to_the_most_any_cadet_has():
    rows = [audit_cadet(cadet()), audit_cadet(cadet(contacts=()))]
    headers, table = export_table(rows)
    assert headers[:7] == ["Surname", "First name", "CIN", "Rank", "Flight", "Cadet email", "Cadet phone"]
    assert headers[7:15] == ["Parent 1", "Parent 1 relationship", "Parent 1 email", "Parent 1 phone",
                             "Parent 2", "Parent 2 relationship", "Parent 2 email", "Parent 2 phone"]
    assert headers[15:] == [label for _, label in CHECKS] + ["Result", "Problems"]
    assert table[0][7:11] == ["Sarah Able", "Mother", "sarah@example.com", "07700 900111"]
    assert table[0][15:] == ["Good"] * 6 + ["Good", None]
    assert table[1][7:15] == [None] * 8
    assert table[1][-2:] == ["Bad", "No parent contact"]
    assert all(len(line) == len(headers) for line in table)


def test_an_empty_sheet_still_has_its_headings():
    headers, table = export_table([])
    assert "Parent 1 email" in headers and table == []
