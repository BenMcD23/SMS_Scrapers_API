"""Fill the RAFAC Logs Form 202 template from a logs-form batch.

Live Demands: write Qty per demanded line, drop the lines that weren't
demanded. Cadet Nominal Roll: list the cadets the demand is for.
"""

import io
import re
from collections import Counter
from datetime import date, timedelta

import openpyxl

from core.paths import TEMPLATES_DIR

TEMPLATE_PATH = str(TEMPLATES_DIR / "Logs Form 202.xlsx")

# item_type (app catalogue) -> Live Demands "Description" text, {size} substituted
DESCRIPTION_TEMPLATES: dict[str, str] = {
    "Beret":               "Beret RAF Size {size}",
    "Wedgewood Male":      "Shirt Long Sleeve Wedgewood Blue Male Size {size}",
    "Wedgewood Female":    "Shirt Long Sleeve Wedgewood Blue Female {size}",
    "Working Blue Male":   "Shirt Long Sleeve Mid Blue Male Size {size}",
    "Working Blue Female": "Shirt Long Sleeve Mid Blue Female {size}",
    "Jumper":              "Jumper Utility Blue Grey V-Neck (Unisex Item) Size {size}",
    "Trousers":            "Trousers Man's RAF No 2 Dress {size}",
    "Slacks":              "Trousers Woman's RAF {size}",
    "Skirts":              "Skirt RAF No 2 Dress {size}",
    "Tie":                 "Necktie Black (Unisex Item) {size}",  # size = Short|Standard
    "Belt":                "Belt Waist RAF (Unisex Item) Size 64-114cm",
}


def build_description(item_type: str, size: str) -> str | None:
    """Column-D description for an entry, or None if the item has no demand line."""
    template = DESCRIPTION_TEMPLATES.get(item_type)
    if not template:
        return None
    return template.format(size=size)


def _norm(value) -> str:
    # The template has typos ("Female115/42", trailing spaces, curly quotes) —
    # compare lowercased with all whitespace stripped and quotes unified.
    return re.sub(r"\s+", "", str(value)).replace("’", "'").lower()


def generate_logs_form(
    entries: list[tuple[str, str]],
    nominal_roll: list[tuple[str, str, str]],
) -> bytes:
    """entries: (item_type, size) per demanded item.
    nominal_roll: (rank, name, issue_or_exchange) per distinct person."""
    counts = Counter(
        desc for item_type, size in entries
        if (desc := build_description(item_type, size)) is not None
    )
    wanted = {_norm(desc): (desc, qty) for desc, qty in counts.items()}

    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    rdd = date.today() + timedelta(days=21)

    ws = wb["Live Demands"]
    unmatched = dict(wanted)
    described_rows = [
        r for r in range(2, ws.max_row + 1) if ws.cell(r, 4).value not in (None, "")
    ]
    for r in described_rows:
        match = unmatched.pop(_norm(ws.cell(r, 4).value), None)
        if match:
            ws.cell(r, 5).value = match[1]
            ws.cell(r, 7).value = rdd
    # remove the demand lines that weren't ordered, bottom-up
    for r in reversed(described_rows):
        if ws.cell(r, 5).value in (None, ""):
            ws.delete_rows(r)
    # anything that didn't match a template line (template typos etc.) still
    # gets demanded — appended with the description for the QM to fix up
    next_row = 2
    while ws.cell(next_row, 4).value not in (None, ""):
        next_row += 1
    for desc, qty in unmatched.values():
        ws.cell(next_row, 4).value = desc
        ws.cell(next_row, 5).value = qty
        ws.cell(next_row, 6).value = "EA"
        ws.cell(next_row, 7).value = rdd
        ws.cell(next_row, 7).number_format = "D/M/YYYY"
        ws.cell(next_row, 8).value = "Routine"
        next_row += 1

    nr = wb["Cadet Nominal Roll"]
    for idx, (rank, name, issue) in enumerate(nominal_roll):
        row = 3 + idx  # row 3 holds the "A Example" placeholder
        nr.cell(row, 1).value = idx + 1
        nr.cell(row, 2).value = rank
        nr.cell(row, 3).value = name
        nr.cell(row, 4).value = issue
    for row in range(3 + len(nominal_roll), nr.max_row + 1):
        for col in range(1, 5):
            nr.cell(row, col).value = None

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    # Self-check: every item_type/size combo from the app catalogue
    # (lib/stores-items.ts in the frontend) must match a Live Demands row.
    from core.catalogue import UNIFORM_SIZES

    CATALOGUE_SIZES = {**UNIFORM_SIZES, "Belt": [""]}
    ws = openpyxl.load_workbook(TEMPLATE_PATH)["Live Demands"]
    template_rows = {
        _norm(ws.cell(r, 4).value)
        for r in range(2, ws.max_row + 1)
        if ws.cell(r, 4).value not in (None, "")
    }
    missing = [
        (item_type, size)
        for item_type, sizes in CATALOGUE_SIZES.items()
        for size in sizes
        if _norm(build_description(item_type, size)) not in template_rows
    ]
    # Known template typo: "Trousers Woman's RAF 80/70/99" where the catalogue has 80/75/99
    assert missing == [("Slacks", "80/75/99")], f"Unexpected unmatched combos: {missing}"
    entries = [("Beret", "56"), ("Beret", "56"), ("Tie", "Standard"), ("Slacks", "80/75/99")]
    out = generate_logs_form(entries, [("Cdt", "A Cadet", "Initial Issue")])
    check = openpyxl.load_workbook(io.BytesIO(out))["Live Demands"]
    rows = {check.cell(r, 4).value: check.cell(r, 5).value for r in range(2, check.max_row + 1)
            if check.cell(r, 4).value not in (None, "")}
    assert rows == {
        "Beret RAF Size 56": 2,
        "Necktie Black (Unisex Item) Standard": 1,
        "Trousers Woman's RAF 80/75/99": 1,  # appended — no template row
    }, f"Unexpected output rows: {rows}"
    rdds = {check.cell(r, 7).value.date() if hasattr(check.cell(r, 7).value, "date") else check.cell(r, 7).value
            for r in range(2, check.max_row + 1) if check.cell(r, 4).value not in (None, "")}
    assert rdds == {date.today() + timedelta(days=21)}, f"Unexpected RDDs: {rdds}"
    print("logs_form_gen self-check OK")
