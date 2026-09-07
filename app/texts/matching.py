"""Putting a name from the old text list against the person it belongs to.

The list was meant to hold a rank and a surname — no CIN — and mostly does, but
plenty of rows carry a full name instead, which is the only thing that tells two
Wrights apart. So a name is read whole first and split only if that finds
nobody, because a squadron has surnames that are two words themselves.

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
    # "CI (Probationer)" is a CI as far as a text greeting is concerned.
    text = re.sub(r"\(.*?\)", " ", (rank or "").lower())
    cleaned = re.sub(r"[^a-z ]", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in RANK_ABBREVIATIONS:
        return RANK_ABBREVIATIONS[cleaned]
    # People write CI as "C.I", and the dots have just been turned into spaces
    # that split it into letters — so try it closed up again.
    return RANK_ABBREVIATIONS.get(cleaned.replace(" ", ""), cleaned)


def split_name(name: str) -> tuple[str, str]:
    """A list entry's name as (given name, surname).

    Only ever a guess at where one ends and the other starts, so it's the
    fallback: Roster.match tries the whole string as a surname first, which is
    what keeps "Lloyd Morris" a surname and not a Mr Morris called Lloyd.
    """
    parts = (name or "").split()
    if len(parts) < 2:
        return "", (name or "").strip()
    return " ".join(parts[:-1]), parts[-1]


def _same_start(roster_name: str, written: str) -> bool:
    """Two spellings of one first name — an initial, or the short form of it."""
    if not roster_name or not written:
        return False
    return roster_name.startswith(written) or written.startswith(roster_name)


def _phrase(parts: list[str]) -> str:
    """"rank, first name and surname" — what a match was made on, for the
    report a person reads before running the migration for real."""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


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

    def match(self, rank: str, name: str) -> Match:
        # The whole thing is a surname until that finds nobody: "Lloyd Morris"
        # is one person's surname, "Aiman Shahbaz" is a first name and a
        # surname, and only the roster can tell you which of the two you have.
        given = ""
        candidates = self._by_surname.get(name_key(name), [])
        if not candidates:
            head, surname = split_name(name)
            if head:
                found = self._by_surname.get(name_key(surname), [])
                if found:
                    given, candidates = head, found

        if not candidates:
            return Match(None, reason="nobody on either roster has that surname")

        matched_on = ["surname"]
        wanted_rank = expand_rank(rank)

        # A rank only one roster can hold narrows the field before anything else
        # — but only while it leaves someone standing.
        if wanted_rank in STAFF_ONLY_RANKS or wanted_rank in CADET_ONLY_RANKS:
            kind = "staff" if wanted_rank in STAFF_ONLY_RANKS else "cadet"
            narrowed = [c for c in candidates if c[0] == kind]
            if narrowed:
                candidates = narrowed

        if given:
            wanted_first = name_key(given)
            narrowed = [c for c in candidates
                        if name_key(c[1].first_name) == wanted_first]
            if not narrowed:
                # "C Wright" and "Ben McDonald", against a roster that spells
                # them Charlie and Benjamin.
                narrowed = [c for c in candidates
                            if _same_start(name_key(c[1].first_name), wanted_first)]
            if not narrowed:
                # The surname is on the roster but this isn't that person —
                # a Vanessa Tyrell where the roster only has an Elianna. Their
                # number stays on the list rather than going to the wrong phone.
                return Match(
                    None,
                    reason="nobody with that surname is called that",
                    candidates=candidates,
                )
            candidates = narrowed
            matched_on.insert(0, "first name")

        if len(candidates) == 1:
            kind, person = candidates[0]
            return Match(kind, person, reason=_phrase(matched_on), candidates=candidates)

        exact = [c for c in candidates if expand_rank(c[1].rank) == wanted_rank and wanted_rank]
        if len(exact) == 1:
            kind, person = exact[0]
            return Match(kind, person, reason=_phrase(["rank"] + matched_on),
                         candidates=candidates)

        return Match(
            None,
            reason=f"{len(candidates)} people share that {'name' if given else 'surname'}",
            candidates=candidates,
        )
