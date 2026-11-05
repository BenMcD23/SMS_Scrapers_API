"""The uniform and badge catalogue as staff have edited it.

``core/catalogue.py`` holds the squadron's original lists. They are only the
starting point: the first read copies them into the database (the way
``texts.settings.get_text_settings`` creates its row on first use, so a deploy
needs no data step), and from then on the tables are the source of truth. The
rest of the API asks this module, never the constants, for item types, sizes
and badge categories.

Nothing recorded against a catalogue entry is ever rewritten here. Names are
what stock rows, order items and badge orders store, so they are fixed once
created and an entry is retired rather than renamed or deleted once anything
uses it.
"""

import json
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import catalogue as defaults
from database.models import (
    ISSUANCE_CATEGORIES,
    ISSUANCE_ITEM_TYPE_MAP,
    ITEM_GENDER_MAP,
    CatalogueBadgeCategory,
    CatalogueItemType,
)

GENDERS = ("male", "female", "unisex")


def _loads(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def ensure_seeded(db: Session) -> None:
    """Copy the defaults in when a table is empty. Each table is checked on its
    own, so adding the second one later doesn't need the first to be empty."""
    seeded = False
    if not db.query(CatalogueItemType.id).first():
        # Several defaults share a category (Slacks and Trousers); the stored
        # value is only set where it differs from the item's own name.
        for pos, name in enumerate(defaults.UNIFORM_ITEM_TYPES):
            category = ISSUANCE_ITEM_TYPE_MAP.get(name)
            db.add(CatalogueItemType(
                name=name,
                position=pos,
                gender=ITEM_GENDER_MAP.get(name, "unisex"),
                issuance_category=category if category != name else None,
                no_size=name in defaults.NO_SIZE_ITEMS,
                kit_flight=name in defaults.KIT_FLIGHT_ITEMS,
                on_order_list=name not in defaults.NOT_ON_ORDER_LIST,
                sizes=json.dumps(list(defaults.UNIFORM_SIZES.get(name, ()))),
                sizing_fields=json.dumps(list(defaults.SIZING_FIELDS.get(name, ()))),
            ))
        seeded = True
    if not db.query(CatalogueBadgeCategory.id).first():
        for pos, cat in enumerate(defaults.BADGE_CATEGORIES):
            db.add(CatalogueBadgeCategory(
                slug=cat["id"],
                name=cat["name"],
                position=pos,
                prefix=cat.get("prefix"),
                levels=json.dumps(cat.get("levels", [])),
                items=json.dumps(cat.get("items", [])),
                gained_where=cat["id"] not in defaults.BADGE_CATEGORIES_WITHOUT_GAINED_WHERE,
            ))
        seeded = True
    if not seeded:
        return
    try:
        db.commit()
    except IntegrityError:
        # Two first requests raced; the other one's rows are the seed.
        db.rollback()


def item_types(db: Session, *, include_retired: bool = False) -> list[CatalogueItemType]:
    ensure_seeded(db)
    q = db.query(CatalogueItemType)
    if not include_retired:
        q = q.filter(CatalogueItemType.active.is_(True))
    return q.order_by(CatalogueItemType.position, CatalogueItemType.id).all()


def badge_categories(db: Session, *, include_retired: bool = False) -> list[CatalogueBadgeCategory]:
    ensure_seeded(db)
    q = db.query(CatalogueBadgeCategory)
    if not include_retired:
        q = q.filter(CatalogueBadgeCategory.active.is_(True))
    return q.order_by(CatalogueBadgeCategory.position, CatalogueBadgeCategory.id).all()


@dataclass(frozen=True)
class UniformRules:
    """What the stores code needs to know about items, read once per request.

    Retired items are included: stock and orders recorded against them still
    have to size-sort, map to an issuance line and count, they just don't
    appear in a picker.
    """
    known: frozenset[str]
    no_size: frozenset[str]
    sizes: dict[str, tuple[str, ...]]
    gender: dict[str, str]
    issuance_category: dict[str, str]
    kit_flight: tuple[str, ...]
    off_order_list: tuple[str, ...]
    low_stock_level: dict[str, int]

    def category_of(self, item_type: str) -> str:
        return self.issuance_category.get(item_type, item_type)

    def category_for_item(self, item_type: str) -> str | None:
        """The issuance line for an item the catalogue knows, retired or not;
        None for a name it has never heard of, so a typo on an issuance request
        is skipped rather than becoming a new line on a cadet's record."""
        return self.category_of(item_type) if item_type in self.known else None


# The original lists as rules, for code that is handed no database (unit tests
# of the stock arithmetic, mainly).
DEFAULT_RULES = UniformRules(
    known=frozenset(defaults.UNIFORM_ITEM_TYPES),
    no_size=defaults.NO_SIZE_ITEMS,
    sizes={k: tuple(v) for k, v in defaults.UNIFORM_SIZES.items()},
    gender=dict(ITEM_GENDER_MAP),
    issuance_category={
        k: v for k, v in ISSUANCE_ITEM_TYPE_MAP.items() if k != v
    },
    kit_flight=defaults.KIT_FLIGHT_ITEMS,
    off_order_list=defaults.NOT_ON_ORDER_LIST,
    low_stock_level={},
)


def uniform_rules(db: Session) -> UniformRules:
    rows = item_types(db, include_retired=True)
    return UniformRules(
        known=frozenset(r.name for r in rows),
        no_size=frozenset(r.name for r in rows if r.no_size),
        sizes={r.name: tuple(_loads(r.sizes)) for r in rows},
        gender={r.name: r.gender for r in rows},
        issuance_category={r.name: r.issuance_category for r in rows if r.issuance_category},
        kit_flight=tuple(r.name for r in rows if r.active and r.kit_flight),
        off_order_list=tuple(r.name for r in rows if not r.on_order_list or not r.active),
        low_stock_level={r.name: r.low_stock_level for r in rows if r.low_stock_level is not None},
    )


def badge_category_json(row: CatalogueBadgeCategory) -> dict:
    """A category in the shape the frontends already read from /reference."""
    out: dict = {"id": row.slug, "name": row.name}
    items, levels = _loads(row.items), _loads(row.levels)
    if items:
        out["items"] = items
    else:
        out["prefix"] = row.prefix or row.name
        out["levels"] = levels
    return out


def reference_payload(db: Session) -> dict:
    """The JSON body of GET /reference — one object so a client fetches once.
    Same shape as before the catalogue was editable, so the cadet portal and
    the site keep working unchanged."""
    types = item_types(db)
    rules = uniform_rules(db)
    cats = badge_categories(db)
    # The issuance lines a cadet's record can hold, in item order, each once.
    # The original order first (the issuance card reads in it), then any a
    # custom item added.
    wanted = {rules.category_of(t.name) for t in types}
    categories = [c for c in ISSUANCE_CATEGORIES if c in wanted]
    categories += [c for c in dict.fromkeys(rules.category_of(t.name) for t in types) if c not in categories]
    return {
        "uniform": {
            "itemTypes": [t.name for t in types],
            "noSizeItems": sorted(n for n in rules.no_size if n in {t.name for t in types}),
            "sizes": {t.name: list(rules.sizes[t.name]) for t in types if rules.sizes[t.name]},
            "sizingFields": {t.name: _loads(t.sizing_fields) for t in types if _loads(t.sizing_fields)},
            "gender": {t.name: t.gender for t in types},
            "issuanceCategories": categories,
            "issuanceCategoryByItem": {t.name: rules.category_of(t.name) for t in types},
            "kitFlightItems": list(rules.kit_flight),
        },
        "badges": {
            "categories": [badge_category_json(c) for c in cats],
            "categoriesWithoutGainedWhere": sorted(c.slug for c in cats if not c.gained_where),
            "gainedWhereOptions": defaults.GAINED_WHERE_OPTIONS,
        },
    }
