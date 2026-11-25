"""Fill the RAFAC Logs Form 202 template from a logs-form batch.

Live Demands: write Qty per demanded line, drop the lines that weren't
demanded. Cadet Nominal Roll: list the cadets the demand is for.

openpyxl drops the drop-down menus that Excel stores as an extension (the
Information Sheet's unit/wing pickers and the Nominal Roll's rank and issue
type), so `_restore_menus` copies them back from the template after saving.
It can't keep the form's EMF crest either; that one only shows on screen.
"""

import io
import posixpath
import re
import zipfile
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
    "Skirt":              "Skirt RAF No 2 Dress {size}",
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
    return _restore_menus(TEMPLATE_PATH, buffer.getvalue())


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _sheet_paths(zf: zipfile.ZipFile) -> dict[str, str]:
    """Sheet name -> its XML part, read from the workbook and its rels so it
    holds whatever order Excel or openpyxl numbered the parts in."""
    import xml.etree.ElementTree as ET

    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    book = ET.fromstring(zf.read("xl/workbook.xml"))
    paths = {}
    for sheet in book.iter(f"{{{_MAIN_NS}}}sheet"):
        target = targets[sheet.get(f"{{{_REL_NS}}}id")]
        paths[sheet.get("name")] = (target.lstrip("/") if target.startswith("/")
                                    else posixpath.normpath(posixpath.join("xl", target)))
    return paths


# The <extLst> holding Excel 2010 list validations; namespaces are declared
# inside it, apart from the xr:uid revision ids, which are dropped because
# openpyxl's root element doesn't declare xr.
_EXT_MENUS = re.compile(r"<extLst>(?:(?!</extLst>).)*x14:dataValidations.*?</extLst>", re.S)
_XR_UID = re.compile(r'\s+xr:uid="[^"]*"')


def _restore_menus(template_path: str, xlsx: bytes) -> bytes:
    with zipfile.ZipFile(template_path) as tpl:
        tpl_paths = _sheet_paths(tpl)
        menus = {}
        for name, path in tpl_paths.items():
            found = _EXT_MENUS.search(tpl.read(path).decode("utf-8"))
            if found:
                menus[name] = _XR_UID.sub("", found.group(0))
    if not menus:
        return xlsx

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(xlsx)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        by_path = {path: menus[name] for name, path in _sheet_paths(src).items() if name in menus}
        for info in src.infolist():
            data = src.read(info.filename)
            ext = by_path.get(info.filename)
            if ext:
                xml = data.decode("utf-8")
                if "<extLst>" not in xml:  # openpyxl writes none; never add a second
                    xml = xml.replace("</worksheet>", ext + "</worksheet>")
                data = xml.encode("utf-8")
            dst.writestr(info, data)
    return out.getvalue()
