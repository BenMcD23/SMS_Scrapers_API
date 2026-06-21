"""Central catalog mapping Bader SMS qualifications → squadron badge types.

This is the single source of truth that two features consume:

* **Audit** (`routers/cadets.py`) — group a cadet's scraped qualifications by
  badge type and report the highest level held.
* **Assessment-sheet uploader** (`scripts/scraper_calls.py`) — turn a
  ``(badge_type, level)`` into the exact Bader dropdown name(s) + option id so
  the scraper can select the right qualification when uploading proof.

How matching works
------------------
The qualifications scraper stores ``CadetQualification.qual_type`` as the **raw
Bader text** exactly as it appears in a cadet's qualifications table. Detection
here mirrors the squadron's classification spreadsheet: each level lists
case-insensitive **substring patterns** that are searched against the cadet's
qualifications, checked **highest level first** (so the first match wins, exactly
like the spreadsheet's nested IFs). ``held_level`` returns that level's label.

Two kinds of badge:
* ``leveled``  — an ordered ladder (e.g. blue→bronze→silver→gold, or
  basic→intermediate→advanced for swimming). ``held_level`` returns the label.
* ``boolean``  — held or not (e.g. MOI). Modelled as a single ``YES`` level.

``Level.bader`` holds the exact "Add Qualification" dropdown name(s) + option id
for the uploader. It can be empty when a pattern matches qualifications that
aren't uploadable here (old Space module names, Swimming competences, etc.).
Items with ``bader_id=None`` need their option id confirmed from the full Bader
dropdown before the uploader can select them (marked ``# TODO``).
"""

from __future__ import annotations

from typing import NamedTuple, Optional


# ─── Level labels ─────────────────────────────────────────────────────────────

BLUE = "blue"
BRONZE = "bronze"
SILVER = "silver"
GOLD = "gold"
NIJMEGEN = "nijmegen"          # Road Marching — sits above gold
BASIC = "basic"               # Swimming
INTERMEDIATE = "intermediate"  # Swimming
ADVANCED = "advanced"          # Swimming
YES = "yes"                   # boolean badges

LEVELED = "leveled"
BOOLEAN = "boolean"


# ─── Data model ───────────────────────────────────────────────────────────────

class BaderQual(NamedTuple):
    """One concrete qualification as it exists in the Bader dropdown."""
    name: str                 # exact dropdown text (trimmed)
    bader_id: Optional[int]   # <option value> in the Add-Qualification dropdown


class Level(NamedTuple):
    """One rung of a badge. ``patterns`` are searched for the audit; ``bader``
    is the uploadable dropdown option(s) for that rung (may be empty)."""
    level: str                # one of the level labels above
    patterns: tuple[str, ...]  # case-insensitive substrings to detect this rung
    bader: tuple[BaderQual, ...] = ()


class BadgeType(NamedTuple):
    key: str                  # stable slug used by the API / frontend
    name: str                 # human-readable label
    kind: str                 # LEVELED | BOOLEAN
    levels: tuple[Level, ...]  # HIGHEST priority first


# ─── The catalog ──────────────────────────────────────────────────────────────
# Levels are listed highest-priority first to mirror the spreadsheet IF cascade.

BADGE_TYPES: tuple[BadgeType, ...] = (
    BadgeType("duke_of_edinburgh", "Duke of Edinburgh", LEVELED, (
        Level(GOLD,   ("Gold Duke of Edinburgh Award",),   (BaderQual("Gold Duke of Edinburgh Award", 465),)),
        Level(SILVER, ("Silver Duke of Edinburgh Award",), (BaderQual("Silver Duke of Edinburgh Award", 464),)),
        Level(BRONZE, ("Bronze Duke of Edinburgh Award",), (BaderQual("Bronze Duke of Edinburgh Award", 463),)),
        Level(BLUE,   ("Blue Pre-Duke of Edinburgh Award",), (BaderQual("Blue Pre-Duke of Edinburgh Award", 462),)),
    )),

    BadgeType("first_aid", "First Aid", LEVELED, (
        Level(GOLD,   ("Cadet First Aid Instructor Award",), (BaderQual("Cadet First Aid Instructor Award", 470),)),
        Level(SILVER, ("Activity First Aid",),               (BaderQual("St John Activity First Aid", 124),)),
        Level(BRONZE, ("Youth First Aid",),                  (BaderQual("St John Youth First Aid", 125),)),
        Level(BLUE,   ("Essential First Aid",),              (BaderQual("St John Essential First Aid", 2973),)),
    )),

    # Spreadsheet keys off "Air Cadet Foundation Leadership"; the dropdown labels
    # them "<Level> Leadership". Both naming variants are accepted for the audit.
    BadgeType("leadership", "Leadership", LEVELED, (
        Level(GOLD,   ("Gold Air Cadet Foundation Leadership", "Gold Leadership"),     (BaderQual("Gold Leadership", 5005),)),
        Level(SILVER, ("Silver Air Cadet Foundation Leadership", "Silver Leadership"), (BaderQual("Silver Leadership", 5004),)),
        Level(BRONZE, ("Bronze Air Cadet Foundation Leadership", "Bronze Leadership"), (BaderQual("Bronze Leadership", 5002),)),
        Level(BLUE,   ("Blue Air Cadet Foundation Leadership", "Blue Leadership"),     (BaderQual("Blue Leadership", 5003),)),
    )),

    BadgeType("cyber", "Cyber", LEVELED, (
        Level(GOLD,   ("Cyber - Specialist (Gold)",), (BaderQual("Cyber - Specialist (Gold)", 378),)),
        Level(SILVER, ("Cyber - Silver Award",),      (BaderQual("Cyber - Silver Award", 496),)),
        Level(BRONZE, ("Cyber - Bronze Award",),      (BaderQual("Cyber - Bronze Award", 497),)),
    )),

    BadgeType("radio", "Radio", LEVELED, (
        Level(GOLD,   ("Radio - Comms Specialist (Gold)",), (BaderQual("Radio - Comms Specialist (Gold)", 377),)),
        Level(SILVER, ("Radio - Communicator (Silver)",),   (BaderQual("Radio - Communicator (Silver)", 376),)),
        Level(BRONZE, ("Radio - Operator (Bronze)",),       (BaderQual("Radio - Operator (Bronze)", 375),)),
        Level(BLUE,   ("Radio - Basic Operator (Blue)",),   (BaderQual("Radio - Basic Operator (Blue)", 373),)),
    )),

    BadgeType("road_marching", "Road Marching", LEVELED, (
        Level(NIJMEGEN, ("Nijmegen Road Marching",), (BaderQual("Nijmegen Road Marching", 473),)),
        Level(GOLD,     ("Gold Road Marching",),     (BaderQual("Gold Road Marching", 1932),)),
        Level(SILVER,   ("Silver Road Marching",),   (BaderQual("Silver Road Marching", 471),)),
        Level(BRONZE,   ("Bronze Road Marching",),   (BaderQual("Bronze Road Marching", 472),)),
        Level(BLUE,     ("Blue Road Marching",),     (BaderQual("Blue Road Marching", 1931),)),
    )),

    # Older themed Space modules (Life on Mars, etc.) count toward a level too.
    BadgeType("space", "Space", LEVELED, (
        Level(GOLD,   ("Gold Space Studies",), (BaderQual("Gold Space Studies", 5001),)),
        Level(SILVER, ("Life on Mars (Silver)", "Planetary Landscapes (Silver)", "Silver Space Studies"),
                      (BaderQual("Silver Space Studies", 4995),)),
        Level(BRONZE, ("Exploring Space (Bronze)", "The Moon our Nearest Neighbour (Bronze)", "Bronze Space Studies"),
                      (BaderQual("Bronze Space Studies", 4994),)),
        Level(BLUE,   ("Applications of Space Technology (Blue)", "Blue Space Studies"),
                      (BaderQual("Blue Space Studies", 4993),)),
    )),

    # Collapsed across the four instrument variants — pattern matches any of them.
    BadgeType("music", "Music", LEVELED, (
        Level(GOLD,   ("National Musician",), (
            BaderQual("National Musician (Gold) - Crossed Trumpets", 1911),
            BaderQual("National Musician (Gold) - Drums", 1912),
            BaderQual("National Musician (Gold) - Lyre", 1910),
            BaderQual("National Musician (Gold) - Pipes", 1909),
        )),
        Level(SILVER, ("Regional Musician",), (
            BaderQual("Regional Musician (Silver) - Crossed Trumpets", 1907),
            BaderQual("Regional Musician (Silver) - Drums", 1908),
            BaderQual("Regional Musician (Silver) - Lyre", 1906),
            BaderQual("Regional Musician (Silver) - Pipes", 1905),
        )),
        Level(BRONZE, ("Wing Musician",), (
            BaderQual("Wing Musician (Bronze) - Crossed Trumpets", 1903),
            BaderQual("Wing Musician (Bronze) - Drums", 1904),
            BaderQual("Wing Musician (Bronze) - Lyre", 1902),
            BaderQual("Wing Musician (Bronze) - Pipes", 1901),
        )),
        # "Musician" is a substring of the higher tiers, so it must be checked last.
        Level(BLUE,   ("Musician",), (
            BaderQual("Musician (Blue) - Crossed Trumpets", 1899),
            BaderQual("Musician (Blue) - Drum", 1900),
            BaderQual("Musician (Blue) - Lyre", 1898),
            BaderQual("Musician (Blue) - Pipes", 1897),
        )),
    )),

    BadgeType("flying", "Flying Badge", LEVELED, (
        Level(GOLD,   ("RAFAC Gold Flying Badge",),   (BaderQual("RAFAC Gold Flying Badge", 2992),)),
        Level(SILVER, ("RAFAC Silver Flying Badge",), (BaderQual("RAFAC Silver Flying Badge", 2991),)),
        Level(BRONZE, ("RAFAC Aviation Training Package Bronze Training Badge",),
                      (BaderQual("RAFAC Aviation Training Package Bronze Training Badge", 2990),)),
        Level(BLUE,   ("RAFAC Aviation Training Package Blue Training Badge",),
                      (BaderQual("RAFAC Aviation Training Package Blue Training Badge", 2989),)),
    )),

    BadgeType("fieldcraft", "Fieldcraft", LEVELED, (
        Level(GOLD,   ("Gold Fieldcraft Skills",), (
            BaderQual("Gold Fieldcraft Skills", 4983),
            BaderQual("Gold Fieldcraft Skills (MOD A)", 4984),
        )),
        Level(SILVER, ("Silver Fieldcraft Skills",), (BaderQual("Silver Fieldcraft Skills", 4982),)),
        Level(BRONZE, ("Bronze Fieldcraft Skills",), (BaderQual("Bronze Fieldcraft Skills", 4981),)),
        Level(BLUE,   ("Blue Fieldcraft Skills",),   (BaderQual("Blue Fieldcraft Skills", 4980),)),
    )),

    # Collapsed across the four weapon variants — pattern matches any of them.
    BadgeType("shooting", "Shooting", LEVELED, (
        Level(GOLD,   ("Gold Shot",), (
            BaderQual("Gold Shot (Air Rifle)", 1665),
            BaderQual("Gold Shot (L98A2)", 1673),
            BaderQual("Gold Shot (Small Bore)", 1669),
            BaderQual("Gold Shot (Target Rifle)", 1677),
        )),
        Level(SILVER, ("Silver Shot",), (
            BaderQual("Silver Shot (Air Rifle)", 1664),
            BaderQual("Silver Shot (L98A2)", 1672),
            BaderQual("Silver Shot (Small Bore)", 1668),
            BaderQual("Silver Shot (Target Rifle)", 1676),
        )),
        Level(BRONZE, ("Bronze Shot",), (
            BaderQual("Bronze Shot (Air Rifle)", 1663),
            BaderQual("Bronze Shot (L98A2)", 1671),
            BaderQual("Bronze Shot (Small Bore)", 1667),
            BaderQual("Bronze Shot (Target Rifle)", 1675),
        )),
        Level(BLUE,   ("Blue Shot",), (
            BaderQual("Blue Shot (Air Rifle)", 1662),
            BaderQual("Blue Shot (L98A2)", 1670),
            BaderQual("Blue Shot (Small Bore)", 1666),
            BaderQual("Blue Shot (Target Rifle)", 1674),
        )),
    )),

    BadgeType("swimming", "Swimming Proficiency", LEVELED, (
        Level(ADVANCED,     ("Advanced Swimming Competence",)),
        Level(INTERMEDIATE, ("Intermediate Swimming Competence",)),
        Level(BASIC,        ("Basic Swimming Competence",)),
    )),

    BadgeType("presentation_skills", "Presentation Skills", BOOLEAN, (
        Level(YES, ("Presentation Skills",), (BaderQual("Presentation Skills", 1918),)),
    )),

    BadgeType("moi", "MOI", BOOLEAN, (
        Level(YES, ("Instructor Cadet",), (
            BaderQual("Instructor Cadet", 1920),
            BaderQual("Instructor Cadet (pre May2019)", 1921),
        )),
    )),

    BadgeType("climatic_injuries", "Climatic Injuries", BOOLEAN, (
        Level(YES, ("Heat Injury - Cadet Presentation",), (BaderQual("Heat Injury - Cadet Presentation", 3979),)),
    )),
)


# ─── Lookups & helpers ────────────────────────────────────────────────────────

BADGE_TYPE_BY_KEY: dict[str, BadgeType] = {b.key: b for b in BADGE_TYPES}


def held_level(badge: BadgeType, qual_names) -> Optional[str]:
    """The level of ``badge`` held by a cadet, given an iterable of their raw
    ``qual_type`` strings. Returns the highest-priority level whose pattern
    matches (mirroring the spreadsheet cascade), or ``None`` if none match."""
    blob = "\n".join(qual_names).casefold()
    for lvl in badge.levels:  # highest priority first
        if any(p.casefold() in blob for p in lvl.patterns):
            return lvl.level
    return None


def bader_quals_for(badge_key: str, level: str) -> tuple[BaderQual, ...]:
    """The Bader dropdown qual(s) for a ``(badge_key, level)`` — used by the
    uploader to select the option (default to the first entry)."""
    badge = BADGE_TYPE_BY_KEY.get(badge_key)
    if not badge:
        return ()
    for lvl in badge.levels:
        if lvl.level == level:
            return lvl.bader
    return ()
