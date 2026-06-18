"""Central catalog mapping Bader SMS qualifications → squadron badge types.

This is the single source of truth that two features consume:

* **Audit** (`routers/cadets.py`) — group a cadet's scraped qualifications by
  badge type and report the highest level held (blue → bronze → silver → gold).
* **Assessment-sheet uploader** (`scripts/scraper_calls.py`) — turn a
  ``(badge_type, level)`` into the exact Bader dropdown name(s) + option id so
  the scraper can select the right qualification when uploading proof.

Background — how the data lines up
----------------------------------
The qualifications scraper stores ``CadetQualification.qual_type`` as the **raw
Bader text** exactly as it appears in a cadet's qualifications table, e.g.
``"Bronze Duke of Edinburgh Award"``. So matching here is by Bader name. The
``bader_id`` is the ``<option value>`` from the "Add Qualification" dropdown —
only the uploader needs it; the audit only needs the names.

NOTE: this is a *best-guess* mapping built from the Bader dropdown. Items marked
``# TODO`` could not be confirmed from the captured dropdown HTML (the option id
was missing/truncated) and should be filled in before wiring the uploader.
Names are stored trimmed; when matching against stored ``qual_type`` values,
strip/casefold both sides to be safe.
"""

from __future__ import annotations

from typing import NamedTuple, Optional


# ─── Level ordering ───────────────────────────────────────────────────────────
# "single" is for badge types with no progression (e.g. First Aid).

BLUE = "blue"
BRONZE = "bronze"
SILVER = "silver"
GOLD = "gold"
SINGLE = "single"

LEVEL_ORDER: dict[str, int] = {SINGLE: 0, BLUE: 1, BRONZE: 2, SILVER: 3, GOLD: 4}


# ─── Data model ───────────────────────────────────────────────────────────────

class BaderQual(NamedTuple):
    """One concrete qualification as it exists in Bader."""
    name: str                 # exact dropdown / qualifications-table text (trimmed)
    bader_id: Optional[int]   # <option value> in the Add-Qualification dropdown


class Level(NamedTuple):
    """A single rung of a badge ladder. May map to several Bader quals
    (e.g. Music collapses the four instrument variants into one level)."""
    level: str                # BLUE | BRONZE | SILVER | GOLD | SINGLE
    quals: tuple[BaderQual, ...]


class BadgeType(NamedTuple):
    key: str                  # stable slug used by the API / frontend
    name: str                 # human-readable label
    levels: tuple[Level, ...]


# ─── The catalog ──────────────────────────────────────────────────────────────

BADGE_TYPES: tuple[BadgeType, ...] = (
    BadgeType("duke_of_edinburgh", "Duke of Edinburgh", (
        Level(BLUE,   (BaderQual("Blue Pre-Duke of Edinburgh Award", 462),)),
        Level(BRONZE, (BaderQual("Bronze Duke of Edinburgh Award", 463),)),
        Level(SILVER, (BaderQual("Silver Duke of Edinburgh Award", 464),)),
        Level(GOLD,   (BaderQual("Gold Duke of Edinburgh Award", 465),)),
    )),

    BadgeType("leadership", "Leadership", (
        Level(BLUE,   (BaderQual("Blue Leadership", 5003),)),
        Level(BRONZE, (BaderQual("Bronze Leadership", 5002),)),
        Level(SILVER, (BaderQual("Silver Leadership", 5004),)),
        Level(GOLD,   (BaderQual("Gold Leadership", 5005),)),
    )),

    BadgeType("fieldcraft", "Fieldcraft", (
        Level(BLUE,   (BaderQual("Blue Fieldcraft Skills", 4980),)),
        Level(BRONZE, (BaderQual("Bronze Fieldcraft Skills", 4981),)),
        Level(SILVER, (BaderQual("Silver Fieldcraft Skills", 4982),)),
        Level(GOLD,   (
            BaderQual("Gold Fieldcraft Skills", 4983),
            BaderQual("Gold Fieldcraft Skills (MOD A)", 4984),
        )),
    )),

    BadgeType("road_marching", "Road Marching", (
        Level(BLUE,   (BaderQual("Blue Road Marching", 1931),)),
        Level(BRONZE, (BaderQual("Bronze Road Marching", 472),)),
        Level(SILVER, (BaderQual("Silver Road Marching", 471),)),
        Level(GOLD,   (BaderQual("Gold Road Marching", 1932),)),
    )),

    BadgeType("space", "Space", (
        Level(BLUE,   (BaderQual("Blue Space Studies", 4993),)),
        Level(BRONZE, (BaderQual("Bronze Space Studies", 4994),)),
        Level(SILVER, (BaderQual("Silver Space Studies", 4995),)),
        Level(GOLD,   (BaderQual("Gold Space Studies", 5001),)),
    )),

    # Core radio operator badge ladder. (Other "Radio - ..." entries such as
    # Datacomms / LAN-WAN / Technical Skills are add-on modules, not the ladder.)
    BadgeType("radio", "Radio", (
        Level(BLUE,   (BaderQual("Radio - Basic Operator (Blue)", 373),)),
        Level(BRONZE, (BaderQual("Radio - Operator (Bronze)", 375),)),
        Level(SILVER, (BaderQual("Radio - Communicator (Silver)", 376),)),
        Level(GOLD,   (BaderQual("Radio - Comms Specialist (Gold)", 377),)),
    )),

    # Cyber has no blue rung. (The older First/Leading/Senior Class scheme is
    # intentionally omitted — add a separate badge type if it's still tracked.)
    BadgeType("cyber", "Cyber", (
        Level(BRONZE, (BaderQual("Cyber - Bronze Award", 497),)),
        Level(SILVER, (BaderQual("Cyber - Silver Award", 496),)),
        Level(GOLD,   (BaderQual("Cyber - Specialist (Gold)", 378),)),
    )),

    BadgeType("flying", "Aviation / Flying Badge", (
        Level(BLUE,   (BaderQual("RAFAC Aviation Training Package Blue Training Badge", 2989),)),
        Level(BRONZE, (BaderQual("RAFAC Aviation Training Package Bronze Training Badge", 2990),)),
        Level(SILVER, (BaderQual("RAFAC Silver Flying Badge", 2991),)),
        Level(GOLD,   (BaderQual("RAFAC Gold Flying Badge", 2992),)),
    )),

    # Collapsed across the four instrument variants per the design decision.
    BadgeType("music", "Music", (
        Level(BLUE, (
            BaderQual("Musician (Blue) - Crossed Trumpets", 1899),
            BaderQual("Musician (Blue) - Drum", 1900),
            BaderQual("Musician (Blue) - Lyre", 1898),
            BaderQual("Musician (Blue) - Pipes", 1897),
        )),
        Level(BRONZE, (
            BaderQual("Wing Musician (Bronze) - Crossed Trumpets", 1903),
            BaderQual("Wing Musician (Bronze) - Drums", 1904),
            BaderQual("Wing Musician (Bronze) - Lyre", 1902),
            BaderQual("Wing Musician (Bronze) - Pipes", 1901),
        )),
        Level(SILVER, (
            BaderQual("Regional Musician (Silver) - Crossed Trumpets", 1907),
            BaderQual("Regional Musician (Silver) - Drums", 1908),
            BaderQual("Regional Musician (Silver) - Lyre", 1906),
            BaderQual("Regional Musician (Silver) - Pipes", 1905),
        )),
        Level(GOLD, (
            BaderQual("National Musician (Gold) - Crossed Trumpets", 1911),
            BaderQual("National Musician (Gold) - Drums", 1912),
            BaderQual("National Musician (Gold) - Lyre", 1910),
            BaderQual("National Musician (Gold) - Pipes", 1909),
        )),
    )),

    # Collapsed across the four weapon variants. NOTE: only Blue and Bronze were
    # present in the captured dropdown HTML — Silver/Gold names are inferred from
    # the naming pattern and their option ids are unknown.
    BadgeType("shooting", "Shooting (Marksman)", (
        Level(BLUE, (
            BaderQual("Blue Shot (Air Rifle)", 1662),
            BaderQual("Blue Shot (L98A2)", 1670),
            BaderQual("Blue Shot (Small Bore)", 1666),
            BaderQual("Blue Shot (Target Rifle)", 1674),
        )),
        Level(BRONZE, (
            BaderQual("Bronze Shot (Air Rifle)", 1663),
            BaderQual("Bronze Shot (L98A2)", 1671),
            BaderQual("Bronze Shot (Small Bore)", 1667),
            BaderQual("Bronze Shot (Target Rifle)", 1675),
        )),
        Level(SILVER, (
            BaderQual("Silver Shot (Air Rifle)", None),     # TODO: confirm name + bader_id
            BaderQual("Silver Shot (L98A2)", None),         # TODO
            BaderQual("Silver Shot (Small Bore)", None),    # TODO
            BaderQual("Silver Shot (Target Rifle)", None),  # TODO
        )),
        Level(GOLD, (
            BaderQual("Gold Shot (Air Rifle)", None),       # TODO: confirm name + bader_id
            BaderQual("Gold Shot (L98A2)", None),           # TODO
            BaderQual("Gold Shot (Small Bore)", None),      # TODO
            BaderQual("Gold Shot (Target Rifle)", None),    # TODO
        )),
    )),

    # No progression — best-guess set of the first-aid quals a cadet might hold.
    # Trim this to whatever the squadron actually counts as "has first aid".
    BadgeType("first_aid", "First Aid", (
        Level(SINGLE, (
            BaderQual("St John Youth First Aid", 125),
            BaderQual("St John Activity First Aid", 124),
            BaderQual("St John Essential First Aid", 2973),
            BaderQual("First Aid At Work", 36),
            BaderQual("Emergency First Aid at Work (6 hours)", 265),
            BaderQual("Red Cross Practical First Aid", 267),
            BaderQual("Cadet First Aid Instructor Award", 470),
        )),
    )),
)


# ─── Lookups & helpers ────────────────────────────────────────────────────────

BADGE_TYPE_BY_KEY: dict[str, BadgeType] = {b.key: b for b in BADGE_TYPES}


def _normalize(name: str) -> str:
    return name.strip().casefold()


# Bader qual name (normalized) → (badge_key, level). Lets you reverse a scraped
# qual_type back to the badge type / level it represents.
BADER_NAME_INDEX: dict[str, tuple[str, str]] = {
    _normalize(q.name): (badge.key, lvl.level)
    for badge in BADGE_TYPES
    for lvl in badge.levels
    for q in lvl.quals
}


def held_level(badge: BadgeType, qual_names: set[str]) -> Optional[str]:
    """Highest level of ``badge`` represented in ``qual_names`` (a set of the
    cadet's raw ``qual_type`` strings), or ``None`` if none are held."""
    wanted = {_normalize(n) for n in qual_names}
    best: Optional[str] = None
    for lvl in badge.levels:
        if any(_normalize(q.name) in wanted for q in lvl.quals):
            if best is None or LEVEL_ORDER[lvl.level] > LEVEL_ORDER[best]:
                best = lvl.level
    return best


def bader_quals_for(badge_key: str, level: str) -> tuple[BaderQual, ...]:
    """The Bader qual(s) for a ``(badge_key, level)`` — used by the uploader to
    pick the dropdown option (default to the first entry)."""
    badge = BADGE_TYPE_BY_KEY.get(badge_key)
    if not badge:
        return ()
    for lvl in badge.levels:
        if lvl.level == level:
            return lvl.quals
    return ()
