"""The contact details audit: does Bader hold a way to reach each cadet, and
each of their parents, that isn't the same number or address twice?

Shared by the audit endpoint and its Excel export, so the page and the sheet
can't disagree about who passes. Works on what the cadet scraper stores:
``Cadet.contact_methods`` (the cadet's own, off their profile) and
``Cadet.contacts`` (each contact off the Contacts tab, with theirs).
"""

from __future__ import annotations

from texts.phone import normalise_phone

GOOD, BAD, NOT_APPLICABLE, NOT_CHECKED = "good", "bad", "n/a", "not_checked"

# (key, column label), in the order the page and the sheet show them.
CHECKS = (
    ("cadet_email", "Cadet has email"),
    ("cadet_phone", "Cadet has phone"),
    ("email_differs", "Cadet email not a parent's"),
    ("phone_differs", "Cadet phone not a parent's"),
    ("has_contact", "Has a parent contact"),
    ("contacts_complete", "Every parent has email and phone"),
)


def method_kind(method: str | None) -> str | None:
    """"phone", "email" or None for one of Bader's contact method names.

    Bader's list is Mobile Phone, Home Phone, Primary Email, Secondary Email and
    Personal Account (Bader). The last is the rafac.mod.gov.uk account every
    cadet is given, so it says nothing about whether anyone set an email and
    is left out.
    """
    name = (method or "").casefold()
    if "bader" in name or "account" in name:
        return None
    if "phone" in name or "mobile" in name:
        return "phone"
    if "email" in name:
        return "email"
    return None


def _details(methods, kind: str) -> list[str]:
    """The non-empty details of one kind, in Bader's order, without repeats."""
    out: list[str] = []
    for m in methods or []:
        if not isinstance(m, dict) or method_kind(m.get("method")) != kind:
            continue
        detail = str(m.get("detail") or "").strip()
        if detail and detail not in out:
            out.append(detail)
    return out


def phones(methods) -> list[str]:
    return _details(methods, "phone")


def emails(methods) -> list[str]:
    return _details(methods, "email")


def _same_phone(a: str, b: str) -> bool:
    # "07700 900000" and "+447700900000" are the same phone.
    return normalise_phone(a) == normalise_phone(b)


def _same_email(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _clash(own: list[str], contacts, read, same) -> list[str]:
    """Names of the contacts sharing any of ``own``."""
    return [
        c.name for c in contacts
        if any(same(mine, theirs) for mine in own for theirs in read(c.contact_methods))
    ]


def audit_cadet(cadet) -> dict:
    """The audit row for one cadet: their details, their contacts, each check
    and, in plain English, what's wrong."""
    own_read = cadet.contact_methods is not None
    contacts_read = cadet.contacts_checked_at is not None
    contacts = list(cadet.contacts) if contacts_read else []
    own_emails, own_phones = emails(cadet.contact_methods), phones(cadet.contact_methods)
    problems: list[str] = []

    def presence(found: list[str], what: str) -> str:
        if not own_read:
            return NOT_CHECKED
        if found:
            return GOOD
        problems.append(f"No {what} for the cadet")
        return BAD

    def differs(own: list[str], read, same, what: str) -> str:
        if not (own_read and contacts_read):
            return NOT_CHECKED
        if not own or not contacts:
            return NOT_APPLICABLE  # nothing to compare; flagged by the other checks
        clashes = _clash(own, contacts, read, same)
        if not clashes:
            return GOOD
        problems.append(f"Cadet {what} is the same as {', '.join(clashes)}'s")
        return BAD

    checks = {
        "cadet_email": presence(own_emails, "email"),
        "cadet_phone": presence(own_phones, "phone"),
        "email_differs": differs(own_emails, emails, _same_email, "email"),
        "phone_differs": differs(own_phones, phones, _same_phone, "phone"),
    }

    if not contacts_read:
        checks["has_contact"] = checks["contacts_complete"] = NOT_CHECKED
    elif not contacts:
        checks["has_contact"] = BAD
        checks["contacts_complete"] = NOT_APPLICABLE
        problems.append("No parent contact")
    else:
        checks["has_contact"] = GOOD
        # Every contact, not only the first: whoever Bader lists is someone
        # the squadron may need to reach, and with one parent a missing
        # number is the same gap as with two.
        complete = True
        for c in contacts:
            missing = [w for w, read in (("email", emails), ("phone", phones)) if not read(c.contact_methods)]
            if missing:
                complete = False
                problems.append(f"{c.name} has no {' or '.join(missing)}")
        checks["contacts_complete"] = GOOD if complete else BAD

    values = checks.values()
    overall = BAD if BAD in values else NOT_CHECKED if NOT_CHECKED in values else GOOD
    return {
        "cin": cadet.cin,
        "first_name": cadet.first_name,
        "last_name": cadet.last_name,
        "rank": cadet.rank,
        "flight": cadet.flight,
        "emails": own_emails,
        "phones": own_phones,
        "contacts": [
            {
                "name": c.name,
                "title": c.title,
                "relationship": c.relation,
                "next_of_kin": c.next_of_kin,
                "emails": emails(c.contact_methods),
                "phones": phones(c.contact_methods),
            }
            for c in contacts
        ],
        "checks": checks,
        "result": overall,
        "problems": problems,
        "contacts_checked_at": cadet.contacts_checked_at.isoformat() if contacts_read else None,
    }


LABELS = {GOOD: "Good", BAD: "Bad", NOT_APPLICABLE: "N/A", NOT_CHECKED: "Not checked"}


def export_table(rows: list[dict]) -> tuple[list[str], list[list]]:
    """Headers and rows for the Excel sheet: the cadet, their details, one
    name/relationship/email/phone block per contact (as many as the cadet with
    the most), then Good/Bad for each check and the overall result."""
    most = max((len(r["contacts"]) for r in rows), default=0)
    headers = ["Surname", "First name", "CIN", "Rank", "Flight", "Cadet email", "Cadet phone"]
    for n in range(1, max(most, 1) + 1):
        headers += [f"Parent {n}", f"Parent {n} relationship", f"Parent {n} email", f"Parent {n} phone"]
    headers += [label for _, label in CHECKS] + ["Result", "Problems"]

    out = []
    for r in rows:
        line = [r["last_name"], r["first_name"], r["cin"], r["rank"], r["flight"],
                "; ".join(r["emails"]) or None, "; ".join(r["phones"]) or None]
        for n in range(max(most, 1)):
            c = r["contacts"][n] if n < len(r["contacts"]) else None
            line += [c["name"], c["relationship"], "; ".join(c["emails"]) or None,
                     "; ".join(c["phones"]) or None] if c else [None] * 4
        line += [LABELS[r["checks"][key]] for key, _ in CHECKS]
        line += [LABELS[r["result"]], "; ".join(r["problems"]) or None]
        out.append(line)
    return headers, out
