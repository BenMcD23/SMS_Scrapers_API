"""Who gets the parade-night text.

One list out of three sources: cadets and staff who've put a mobile on their own
record, plus the Sms_Recipients rows left for people with no account behind them
(parents, mostly). Numbers live on the person wherever there is one, so the
squadron's own roster keeps them current instead of a separate list drifting out
of date beside it.

Everything downstream — the recipients page, the CSV export, the send — reads
the list from here, so what staff see is exactly who gets texted.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from database.models import Cadet, SmsRecipient, Staff
from texts.matching import abbreviate_rank
from texts.phone import normalise_phone

# What a cadet with no rank on their record is greeted as.
DEFAULT_CADET_RANK = "Cdt"

SOURCES = ("cadet", "staff", "extra")


@dataclass(frozen=True)
class Recipient:
    """One person the text goes to, whatever it's stored on."""
    key: str            # "cadet:1234567" / "staff:1234567" / "extra:12"
    source: str         # one of SOURCES
    rank: str           # greeting rank, e.g. "Sgt"
    surname: str        # greeting surname
    name: str           # full name where we have one, for the UI
    phone_number: str

    @property
    def id(self) -> int:
        return int(self.key.split(":", 1)[1])


def parse_key(key: str) -> tuple[str, int]:
    """Split a recipient key back into its source and row id, or raise
    ValueError — the API turns that into a 404 rather than a 500."""
    source, _, raw = (key or "").partition(":")
    if source not in SOURCES or not raw.isdigit():
        raise ValueError(f"Not a recipient: {key!r}")
    return source, int(raw)


def person_recipient(source: str, person) -> Recipient:
    # The roster spells ranks out ("Corporal"); a text greeting doesn't.
    rank = abbreviate_rank(person.rank)
    if source == "cadet" and not rank:
        rank = DEFAULT_CADET_RANK
    return Recipient(
        key=f"{source}:{person.cin}",
        source=source,
        rank=rank,
        surname=(person.last_name or "").strip(),
        name=f"{person.first_name or ''} {person.last_name or ''}".strip(),
        phone_number=(person.phone_number or "").strip(),
    )


def extra_recipient(extra: SmsRecipient) -> Recipient:
    return Recipient(
        key=f"extra:{extra.id}",
        source="extra",
        rank=(extra.rank or "").strip(),
        surname=(extra.surname or "").strip(),
        name=f"{extra.rank or ''} {extra.surname or ''}".strip(),
        phone_number=(extra.phone_number or "").strip(),
    )


def list_recipients(db: Session) -> list[Recipient]:
    """Everyone with a number, in surname order, each number appearing once.

    Staff and cadets come before the extras so that when the same number is on
    both — a parent left on the old list who is also a CFAV — the greeting comes
    from the account, which is the one somebody keeps up to date.
    """
    found: list[Recipient] = []

    for source, model in (("staff", Staff), ("cadet", Cadet)):
        rows = (
            db.query(model)
            .filter(model.phone_number.isnot(None), model.phone_number != "")
            .all()
        )
        found.extend(person_recipient(source, person) for person in rows)

    found.extend(extra_recipient(row) for row in db.query(SmsRecipient).all())

    seen: set[str] = set()
    deduped = []
    for recipient in found:
        number = normalise_phone(recipient.phone_number)
        if not number or number in seen:
            continue
        seen.add(number)
        deduped.append(recipient)

    return sorted(deduped, key=lambda r: (r.surname.lower(), r.name.lower()))
