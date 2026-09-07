"""Move the old parade-night text list onto the people it belongs to.

Run once, by hand, after the schema migration that adds Cadets.phone_number and
Staff.phone_number. Every Sms_Recipients row is matched to a cadet or a staff
member by rank and surname; matched numbers move onto that person's record and
the row is deleted, and anything that can't be matched is left exactly where it
is so it keeps getting the texts.

Run it in the container — the database is only reachable from the compose
network, and the host has none of the dependencies:

    cd ~/sms-api/prod
    docker compose -p sms-prod exec api python -m scripts.migrate_sms_numbers
    docker compose -p sms-prod exec api python -m scripts.migrate_sms_numbers --apply

The first reports and changes nothing; the second does it. `--apply --keep-rows`
also leaves the old Sms_Recipients rows behind, which is only useful for a
rehearsal — see the warning below.

A dry run is the default on purpose: read the report, check the ambiguous names
by hand, then run it again with --apply. Running it twice is safe — rows that
have already moved are gone, and a number already on the right person is left
alone.

Don't finish with --keep-rows: it leaves the number in both places, and the
duplicate only shows itself later, when the person leaves SMS and their record
is deleted but the old row keeps texting them.
"""

import argparse
import sys

from database.database import SessionLocal
from database.models import SmsRecipient
from texts.matching import Roster
from texts.phone import is_uk_mobile, normalise_phone


def _describe(match) -> str:
    """The candidates an ambiguous name could have meant, for the report."""
    return ", ".join(
        f"{kind} {person.first_name} {person.last_name} ({person.rank or 'no rank'})"
        for kind, person in match.candidates
    )


def migrate(db, apply: bool = False, keep_rows: bool = False) -> dict:
    """Match every row and, when `apply`, write the numbers across.

    Returns the counts the CLI prints. Split out from main() so the tests can
    drive it against an in-memory database.
    """
    roster = Roster(db)
    rows = db.query(SmsRecipient).order_by(SmsRecipient.surname).all()

    moved, already, unmatched, invalid = [], [], [], []

    for row in rows:
        label = f"{row.rank} {row.surname}".strip() or "(no name)"
        phone = normalise_phone(row.phone_number)

        if not is_uk_mobile(phone):
            # Left on the list rather than dropped: it's still whatever number
            # has been getting the texts, and only a person can judge it.
            invalid.append((label, row.phone_number))
            continue

        match = roster.match(row.rank, row.surname)
        if not match.matched:
            unmatched.append((label, match.reason, _describe(match)))
            continue

        existing = normalise_phone(match.person.phone_number)
        if existing == phone:
            already.append((label, match.label, phone))
        else:
            moved.append((label, f"{match.kind} {match.label}", phone, match.reason, existing))
            if apply:
                match.person.phone_number = phone

        if apply and not keep_rows:
            db.delete(row)

    if apply:
        db.commit()

    return {
        "total": len(rows),
        "moved": moved,
        "already": already,
        "unmatched": unmatched,
        "invalid": invalid,
    }


def report(result: dict, apply: bool) -> None:
    verb = "Moved" if apply else "Would move"
    print(f"\n{result['total']} row(s) on the old list.\n")

    print(f"── {verb} onto a record: {len(result['moved'])} ──")
    for label, to, phone, reason, existing in result["moved"]:
        overwrite = f"  (replacing {existing})" if existing else ""
        print(f"  {label:<28} → {to:<40} {phone}  [{reason}]{overwrite}")

    print(f"\n── Already on the right record: {len(result['already'])} ──")
    for label, to, phone in result["already"]:
        print(f"  {label:<28} → {to:<40} {phone}")

    print(f"\n── No match, left on the list: {len(result['unmatched'])} ──")
    for label, reason, candidates in result["unmatched"]:
        print(f"  {label:<28} {reason}")
        if candidates:
            print(f"  {'':<28} could be: {candidates}")

    print(f"\n── Not a UK mobile, left on the list: {len(result['invalid'])} ──")
    for label, phone in result["invalid"]:
        print(f"  {label:<28} {phone!r}")

    if not apply:
        print("\nDry run — nothing was written. Re-run with --apply once the "
              "unmatched names above look right.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (without this it only reports)")
    parser.add_argument("--keep-rows", action="store_true",
                        help="keep the Sms_Recipients rows that were matched, "
                             "instead of deleting them once the number has moved")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        result = migrate(db, apply=args.apply, keep_rows=args.keep_rows)
        report(result, apply=args.apply)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
