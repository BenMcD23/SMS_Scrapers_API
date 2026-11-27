"""Stock numbers (NSNs): which stores item and size a label's number means.

Every issued garment's label carries its 13-digit NATO stock number, and the
bag it comes in has it as a barcode. The Logs Form 202 we demand uniform on
lists the number of every line it covers beside its description, and
logs_form_gen.DESCRIPTION_TEMPLATES already says which description each of our
catalogue items and sizes is. Read backwards, that is the lookup the stores
scanner needs, with nothing to type in: a new version of the form brings its
numbers with it.

Labels the form doesn't list (older contracts, polos) are taught once by staff
and kept in Stores_Nsns. The form wins where both have a number, so a mistaken
teach can never override what the form says.
"""

import re
from datetime import datetime
from functools import lru_cache

import openpyxl
from sqlalchemy.orm import Session

from core import catalogue_store
from database.models import StoresNsn
from form_generators.logs_form_gen import DESCRIPTION_TEMPLATES, TEMPLATE_PATH, _norm

# What stock lines store as the size of an unsized item (the site's stockPayload).
NO_SIZE = "N/A"


def normalise(raw: str | None) -> str | None:
    """The 13 digits of a stock number as typed, printed ("8405-99-845-6634")
    or scanned, or None if it isn't one."""
    digits = re.sub(r"[\s-]", "", raw or "")
    return digits if re.fullmatch(r"\d{13}", digits) else None


def formatted(nsn: str) -> str:
    """The way labels print it: 8405-99-845-6634."""
    return f"{nsn[:4]}-{nsn[4:6]}-{nsn[6:9]}-{nsn[9:]}"


@lru_cache(maxsize=1)
def form_lines() -> dict[str, str]:
    """Stock number -> the form's description of it, for every Live Demands line.

    The number is split over three columns (NSC, the NIIN's country code and
    the rest), and Excel has dropped the leading zeros of the numeric ones."""
    ws = openpyxl.load_workbook(TEMPLATE_PATH, read_only=True)["Live Demands"]
    lines = {}
    for row in ws.iter_rows(min_row=2, max_col=4, values_only=True):
        nsc, country, rest, description = row
        if not description or None in (nsc, country, rest):
            continue
        try:
            nsn = f"{int(nsc):04d}{int(country):02d}{int(rest):07d}"
        except (TypeError, ValueError):
            continue
        lines[nsn] = str(description).strip()
    return lines


# Form lines that are a stores item but aren't how we demand it, so they aren't
# in DESCRIPTION_TEMPLATES: a booking-in scan should still know them. Both are
# unsized, so the description is the whole match.
SCAN_ONLY_DESCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("Brassard", "BRASSARD, Combined Cadet Force (RAF sections) and Air Training Corps"),
    ("Belt", "Belt Waist RAF (Unisex Item) Size 114-130cm"),
)


def _description_patterns() -> list[tuple[str, re.Pattern]]:
    """Each catalogue item's form description as a pattern, its size the group."""
    out = []
    for item_type, template in (*DESCRIPTION_TEMPLATES.items(), *SCAN_ONLY_DESCRIPTIONS):
        before, _, after = template.partition("{size}")
        size = "(?P<size>.+)" if "{size}" in template else "(?P<size>)"
        out.append((item_type, re.compile(re.escape(_norm(before)) + size + re.escape(_norm(after)))))
    return out


def catalogue_item(description: str, rules: catalogue_store.UniformRules) -> tuple[str, str] | None:
    """(item type, stock size) for a form description, or None for a line the
    stores don't keep (gloves, coveralls). The size comes back spelled the way
    the catalogue spells it, since the form's text is matched without case or
    spaces."""
    key = _norm(description)
    for item_type, pattern in _description_patterns():
        found = pattern.fullmatch(key)
        if not found or item_type not in rules.known:
            continue
        if item_type in rules.no_size:
            return item_type, NO_SIZE
        wanted = found.group("size")
        for size in rules.sizes.get(item_type, ()):
            if _norm(size) == wanted:
                return item_type, size
    return None


def lookup(db: Session, nsn: str) -> dict | None:
    """What a stock number means, or None if neither the form nor staff know it.

    ``source`` is "logs_form" when the form says which item it is, "added" when
    staff taught it. A form line for something the stores don't keep comes
    back with no item, so the page can say so rather than "unknown"."""
    rules = catalogue_store.uniform_rules(db)
    description = form_lines().get(nsn)
    match = catalogue_item(description, rules) if description else None
    if match:
        item_type, size, source, added_by = *match, "logs_form", None
    else:
        taught = db.query(StoresNsn).filter(StoresNsn.nsn == nsn).first()
        if taught:
            item_type, size, source, added_by = taught.item_type, taught.size, "added", taught.added_by
        elif description:
            item_type, size, source, added_by = None, None, "logs_form", None
        else:
            return None
    return {
        "nsn": nsn,
        "formatted": formatted(nsn),
        "description": description,
        "itemType": item_type,
        "size": size,
        "source": source,
        "addedBy": added_by,
    }


class TeachError(ValueError):
    """Why a stock number can't be taught; the message is shown to staff."""


def teach(db: Session, nsn: str, item_type: str, size: str, by: str | None) -> StoresNsn:
    """Record (or correct) what a stock number means. The caller commits."""
    description = form_lines().get(nsn)
    rules = catalogue_store.uniform_rules(db)
    if description and catalogue_item(description, rules):
        raise TeachError(f"The logs form already lists {formatted(nsn)} as {description}")
    active = {t.name for t in catalogue_store.item_types(db)}
    if item_type not in active:
        raise TeachError("Choose an item from the catalogue")
    if item_type in rules.no_size:
        size = NO_SIZE
    else:
        size = (size or "").strip()
        sizes = rules.sizes.get(item_type, ())
        if not size or (sizes and size not in sizes):
            raise TeachError(f"Choose one of the {item_type} sizes")
    row = db.query(StoresNsn).filter(StoresNsn.nsn == nsn).first()
    if row is None:
        row = StoresNsn(nsn=nsn)
        db.add(row)
    row.item_type, row.size, row.added_by, row.added_at = item_type, size, by, datetime.now()
    return row
