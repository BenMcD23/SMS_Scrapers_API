"""Reference data shared by the SMS site and the cadet portal.

Both frontends used to ship their own copy of these lists, and they had
already drifted ("Skirt" vs "Skirts", different "gained where" rules). This
module is the single source of truth; the frontends fetch it from
``GET /reference`` (see routers/reference.py) instead of hardcoding it.

Everything here is plain data so it can move into database tables behind a
settings page later without changing the endpoint's shape. Names must match
what is already stored in the database (order items, issuances, stock), so
renaming an entry is a data migration, not just an edit here.
"""

from typing import TypedDict

from database.models import ISSUANCE_CATEGORIES, ISSUANCE_ITEM_TYPE_MAP, ITEM_GENDER_MAP

# ── Uniform ───────────────────────────────────────────────────────────────────

UNIFORM_ITEM_TYPES: tuple[str, ...] = (
    "Wedgewood Male",
    "Wedgewood Female",
    "Working Blue Male",
    "Working Blue Female",
    "Jumper",
    "Trousers",
    "Slacks",
    "Skirts",
    "Beret",
    "Tie",
    "Brassard",
    "Belt",
)

# Items issued without a size. Ties are the exception: they have a length
# variant (Short/Standard) that stores treat as the size on the demand form.
NO_SIZE_ITEMS: frozenset[str] = frozenset({"Tie", "Brassard", "Belt"})
TIE_VARIANTS: tuple[str, ...] = ("Short", "Standard")

_BERET = tuple(str(n) for n in range(48, 63))
_TROUSERS = (
    "66/68/76", "69/72/84", "72/72/88", "72/76/92", "72/80/96",
    "72/84/100", "72/88/104", "75/72/88", "75/76/92", "75/80/96",
    "75/84/100", "75/88/104", "75/92/108", "80/72/88", "80/76/92",
    "80/80/96", "80/84/100", "80/88/104", "80/92/108", "80/96/112",
    "80/100/116", "80/104/120", "85/76/92", "85/80/96", "85/84/100",
    "85/88/104", "85/92/108", "85/96/112", "85/100/116", "85/104/120",
    "85/108/124",
)
_SLACKS = (
    "70/60/84", "70/65/89", "70/70/94", "70/75/99", "70/80/104",
    "75/60/84", "75/65/89", "75/70/94", "75/75/99", "75/80/104",
    "75/85/109", "75/90/114", "75/95/119", "80/65/89", "80/70/94",
    "80/75/99", "80/80/104", "80/85/109", "80/90/114", "80/95/119",
    "80/100/124", "80/105/129", "85/65/89", "85/70/94", "85/75/99",
    "85/80/104", "85/85/109", "85/90/114", "85/95/119", "85/100/124",
    "85/105/129",
)
_SKIRTS = (
    "65/60/84", "65/65/89", "65/70/94", "65/75/99", "65/80/104",
    "65/85/109", "65/90/114", "65/95/119", "65/100/124", "65/105/129",
    "70/60/84", "70/65/89", "70/70/94", "70/75/99", "70/80/104",
    "70/85/109", "70/90/114", "70/95/119", "70/100/124", "70/105/129",
    "75/60/84", "75/65/89", "75/70/94", "75/75/99", "75/80/104",
    "75/85/109", "75/90/114", "75/95/119", "75/100/124", "75/105/129",
)

UNIFORM_SIZES: dict[str, tuple[str, ...]] = {
    "Beret": _BERET,
    "Wedgewood Male": (
        "85/30", "90/33", "95/35", "95/36", "100/36", "100/38",
        "105/38", "105/39", "110/39", "110/40", "115/40", "115/42",
        "120/42", "120/43", "125/43", "130/45", "135/48",
    ),
    "Wedgewood Female": (
        "85/31", "85/33", "90/33", "90/35", "95/35", "95/36",
        "100/36", "100/38", "105/38", "105/39", "110/39", "110/40",
        "115/40", "115/42", "120/42", "120/43",
    ),
    "Working Blue Male": (
        "95/36", "100/38", "105/39", "110/40", "115/42",
        "120/43", "125/43", "130/45", "135/48",
    ),
    "Working Blue Female": (
        "85/33", "90/35", "95/36", "100/38", "105/39",
        "110/40", "115/42", "120/43",
    ),
    "Jumper": ("74", "82", "88", "94", "100", "106", "112", "118", "124", "130", "136"),
    "Trousers": _TROUSERS,
    "Slacks": _SLACKS,
    "Skirts": _SKIRTS,
    "Tie": TIE_VARIANTS,
}

# Which measurement fields the "I need sizing" form asks for, per item.
SIZING_FIELDS: dict[str, tuple[str, ...]] = {
    "Wedgewood Male": ("chest", "collar"),
    "Wedgewood Female": ("chest", "collar"),
    "Working Blue Male": ("chest", "collar"),
    "Working Blue Female": ("chest", "collar"),
    "Jumper": ("chest",),
    "Trousers": ("waist", "leg", "seat"),
    "Slacks": ("waist", "leg"),
    "Skirts": ("waist", "leg"),
}

# C Flight initial kitting: both gendered variants are added and the QM
# deletes whichever doesn't apply.
KIT_FLIGHT_ITEMS: tuple[str, ...] = (
    "Beret", "Brassard", "Jumper", "Tie", "Belt",
    "Wedgewood Male", "Wedgewood Female", "Working Blue Male", "Working Blue Female",
    "Trousers", "Slacks", "Skirts",
)

# ── Badges ────────────────────────────────────────────────────────────────────


class BadgeCategory(TypedDict, total=False):
    id: str
    name: str
    # Fixed item names with no level (Core, Classification).
    items: list[str]
    # Levels on the category itself; the badge name is "<prefix> – <level>".
    levels: list[str]
    prefix: str


_STANDARD_LEVELS = ["Blue", "Bronze", "Silver", "Gold"]

BADGE_CATEGORIES: list[BadgeCategory] = [
    {"id": "core", "name": "Core Badges", "items": ["Squadron Num", "ATC", "TRF"]},
    {"id": "classification", "name": "Classification Badges",
     "items": ["First Class", "Leading", "Senior", "Master"]},
    {"id": "leadership", "name": "Leadership Badges", "prefix": "Leadership", "levels": _STANDARD_LEVELS},
    {"id": "music", "name": "Music Badges", "prefix": "Music", "levels": _STANDARD_LEVELS},
    {"id": "shooting", "name": "Shooting Badges", "prefix": "Shooting", "levels": _STANDARD_LEVELS},
    {"id": "radio", "name": "Radio Badges", "prefix": "Radio", "levels": _STANDARD_LEVELS},
    {"id": "cyber", "name": "Cyber Badges", "prefix": "Cyber", "levels": _STANDARD_LEVELS},
    {"id": "space", "name": "Space Badges", "prefix": "Space", "levels": _STANDARD_LEVELS},
    {"id": "road-marching", "name": "Road Marching Badges", "prefix": "Road Marching",
     "levels": ["Blue", "Bronze", "Silver", "Gold (Nijmegen)"]},
    {"id": "first-aid", "name": "First Aid Badges", "prefix": "First Aid", "levels": _STANDARD_LEVELS},
    {"id": "dofe", "name": "DofE Badges", "prefix": "DofE", "levels": _STANDARD_LEVELS},
    {"id": "flying", "name": "Flying Badge", "prefix": "Flying", "levels": _STANDARD_LEVELS},
]

# Replacement badges and the automatically-awarded Core/Classification badges
# don't record where they were gained.
BADGE_CATEGORIES_WITHOUT_GAINED_WHERE: frozenset[str] = frozenset({"core", "classification"})

# Every option collects the dates attended.
GAINED_WHERE_OPTIONS: list[dict[str, str]] = [
    {"value": "camp", "label": "On Camp"},
    {"value": "sector_training_weekend", "label": "Sector Training Weekend"},
    {"value": "wing_training_weekend", "label": "Wing Training Weekend"},
    {"value": "on_sqn", "label": "Squadron"},
    {"value": "other", "label": "Other"},
]


def reference_payload() -> dict:
    """The JSON body of GET /reference — one object so a client fetches once."""
    return {
        "uniform": {
            "itemTypes": list(UNIFORM_ITEM_TYPES),
            "noSizeItems": sorted(NO_SIZE_ITEMS),
            "sizes": {k: list(v) for k, v in UNIFORM_SIZES.items()},
            "sizingFields": {k: list(v) for k, v in SIZING_FIELDS.items()},
            "gender": dict(ITEM_GENDER_MAP),
            "issuanceCategories": list(ISSUANCE_CATEGORIES),
            "issuanceCategoryByItem": dict(ISSUANCE_ITEM_TYPE_MAP),
            "kitFlightItems": list(KIT_FLIGHT_ITEMS),
        },
        "badges": {
            "categories": BADGE_CATEGORIES,
            "categoriesWithoutGainedWhere": sorted(BADGE_CATEGORIES_WITHOUT_GAINED_WHERE),
            "gainedWhereOptions": GAINED_WHERE_OPTIONS,
        },
    }
