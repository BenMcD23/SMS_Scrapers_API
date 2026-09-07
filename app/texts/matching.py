"""Putting a name from the old text list against the person it belongs to.

The list only ever held a rank and a surname — no CIN, no first name — so this
is everything there is to go on, and some entries genuinely can't be resolved.
It answers with what it found rather than guessing: a caller gets a person, or
the reason there wasn't one, and never a coin-flip between two Smiths.

Used by the by-hand migration of the old list (scripts/migrate_sms_numbers) and
by the recipients CSV import, so both read a name the same way.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from database.models import Cadet, Staff

# Bader spells ranks out in full; a squadron writes them short, in texts and on
# the old list alike. This is the one place that knows both, so a greeting reads
# "Sgt Smith" whether the number came off a roster or off a spreadsheet.
RANK_DISPLAY = {
    "cadet": "Cdt",
    "corporal": "Cpl",
    "sergeant": "Sgt",
    "flight sergeant": "FS",
    "cadet warrant officer": "CWO",
    "warrant officer": "WO",
    "civilian instructor": "CI",
    "civilian gliding instructor": "CGI",
    "pilot officer": "Plt Off",
    "flying officer": "Fg Off",
    "flight lieutenant": "Flt Lt",
    "squadron leader": "Sqn Ldr",
    "wing commander": "Wg Cdr",
    "group captain": "Gp Capt",
}

# The same map read the other way, plus the spellings people also use.
RANK_ABBREVIATIONS = {
    short.lower(): full for full, short in RANK_DISPLAY.items()
} | {
    "flt sgt": "flight sergeant",
    "cdt wo": "cadet warrant officer",
}

# Ranks only one of the two rosters can hold, so they say which roster to look
# in. Sergeant and flight sergeant are deliberately absent — a squadron has both
# cadet and staff ones, and pretending otherwise is how you text the wrong Smith.
STAFF_ONLY_RANKS = {
    "civilian instructor", "civilian gliding instructor", "warrant officer",
    "pilot officer", "flying officer", "flight lieutenant", "squadron leader",
    "wing commander", "group captain", "chaplain",
}
CADET_ONLY_RANKS = {"cadet", "leading cadet", "corporal", "cadet warrant officer"}


def name_key(name: str) -> str:
    """Surnames compared the way people mistype them — O'Brien, Obrien and
    o brien are one name; McDonald and Mcdonald certainly are."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def expand_rank(rank: str) -> str:
    """A rank in its long form, lower-cased. Unknown ranks pass through as
    written so an exact string match can still catch them."""
    cleaned = re.sub(r"[^a-z ]", " ", (rank or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return RANK_ABBREVIATIONS.get(cleaned, cleaned)


def abbreviate_rank(rank: str) -> str:
    """A rank as it's written in a text greeting. A rank we don't know is left
    exactly as it was typed."""
    return RANK_DISPLAY.get(expand_rank(rank), (rank or "").strip())


@dataclass
class Match:
    """Who a list entry belongs to, or why we can't say."""
    kind: str | None                 # "cadet" | "staff" | None
    person: object | None = None     # the Cadet / Staff row
    reason: str = ""                 # "rank and surname" / "surname" / why not
    candidates: list = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.person is not None

    @property
    def cin(self) -> int | None:
        return self.person.cin if self.person else None

    @property
    def label(self) -> str:
        if not self.person:
            return ""
        return f"{self.person.first_name} {self.person.last_name}".strip()


class Roster:
    """Both rosters indexed by surname, read once and queried many times."""

    def __init__(self, db: Session):
        self._by_surname: dict[str, list[tuple[str, object]]] = {}
        for kind, model in (("staff", Staff), ("cadet", Cadet)):
            for person in db.query(model).all():
                self._by_surname.setdefault(name_key(person.last_name), []).append((kind, person))

    def match(self, rank: str, surname: str) -> Match:
        candidates = self._by_surname.get(name_key(surname), [])
        if not candidates:
            return Match(None, reason="nobody on either roster has that surname")

        wanted_rank = expand_rank(rank)

        # A rank only one roster can hold narrows the field before anything else
        # — but only while it leaves someone standing.
        if wanted_rank in STAFF_ONLY_RANKS or wanted_rank in CADET_ONLY_RANKS:
            kind = "staff" if wanted_rank in STAFF_ONLY_RANKS else "cadet"
            narrowed = [c for c in candidates if c[0] == kind]
            if narrowed:
                candidates = narrowed

        if len(candidates) == 1:
            kind, person = candidates[0]
            return Match(kind, person, reason="surname", candidates=candidates)

        exact = [c for c in candidates if expand_rank(c[1].rank) == wanted_rank and wanted_rank]
        if len(exact) == 1:
            kind, person = exact[0]
            return Match(kind, person, reason="rank and surname", candidates=candidates)

        return Match(
            None,
            reason=f"{len(candidates)} people share that surname",
            candidates=candidates,
        )
