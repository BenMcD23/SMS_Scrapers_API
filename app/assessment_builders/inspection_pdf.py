"""Render a submitted inspection sheet as a printable PDF — one page per flight,
a grid of cadets each shown against the uniform figure with their faults circled
on the diagram and listed alongside, echoing the paper Flight Inspection Sheet.
"""

import io
import textwrap
from pathlib import Path

from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.utils import ImageReader

FIGURE_PATH = Path(__file__).resolve().parent.parent / "assets" / "inspection-figure.png"

# Uniform regions as fractions of the figure height (top, height) — mirrors the
# clickable bands on the inspection marking page so markers land in the right place.
REGIONS = {
    "Beret / Headdress":    (0.00, 0.14),
    "Hair / Face":          (0.14, 0.06),
    "Jumper / Shirt / Tie": (0.20, 0.27),
    "Trousers":             (0.47, 0.42),
    "Shoes":                (0.89, 0.11),
}

FAULT_COLOR = HexColor("#dc2626")
POSITIVE_COLOR = HexColor("#16a34a")
MUTED = HexColor("#6b7280")
BORDER = HexColor("#d1d5db")

PAGE_W, PAGE_H = landscape(A4)  # 842 x 595
MARGIN = 30
TITLE_H = 64
COLS = 3
ROWS = 3
PER_PAGE = COLS * ROWS

CELL_W = (PAGE_W - 2 * MARGIN) / COLS
CELL_H = (PAGE_H - MARGIN - TITLE_H - MARGIN) / ROWS

FIG_H = CELL_H - 44
FIG_W = FIG_H * (512 / 1536)  # figure aspect ratio


def _flight_order(flight: str) -> tuple:
    order = {"NCO": 0, "A": 1, "B": 2, "C": 3}
    return (order.get(flight, 98), flight)


def _draw_marker(c, x, y, n, color):
    """Small numbered dot — the key that ties a diagram mark to its comment."""
    r = 5.5
    c.setFillColor(color)
    c.circle(x, y, r, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 6.5)
    c.drawCentredString(x, y - 2.3, str(n))


def _draw_cadet(c, cadet, ox, oy_top):
    """One cadet cell with its top-left corner at (ox, oy_top)."""
    # Header: name with the score / status sitting just after it.
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 8.5)
    name = f"{cadet['last_name']}, {cadet['first_name']}"[:34]
    c.drawString(ox, oy_top - 9, name)
    sx = ox + c.stringWidth(name, "Helvetica-Bold", 8.5) + 8

    if cadet["absent"]:
        c.setFillColor(HexColor("#b45309"))
        c.setFont("Helvetica-Bold", 8)
        c.drawString(sx, oy_top - 9, "AWOL" if cadet["awol"] else "ABSENT")
    else:
        score = cadet["score"]
        c.setFillColor(HexColor("#374151"))
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(sx, oy_top - 9, f"{score:g}/10" if score is not None else "–/10")

    # Figure on the left of the cell body
    fig_x = ox + 2
    fig_bottom = oy_top - 20 - FIG_H
    try:
        c.drawImage(
            ImageReader(str(FIGURE_PATH)), fig_x, fig_bottom,
            width=FIG_W, height=FIG_H, preserveAspectRatio=True, mask="auto",
        )
    except Exception as e:  # pragma: no cover - asset should always be present
        print(f"[inspection_pdf] figure draw failed: {e}")

    # Collect comments with a running number, grouped by region for placement.
    comments = [("fault", f) for f in cadet["faults"]] + [
        ("positive", p) for p in cadet["positives"]
    ]
    by_region: dict[str, list] = {}
    for i, (kind, com) in enumerate(comments, start=1):
        by_region.setdefault(com.get("region") or "General", []).append((i, kind, com))

    # Markers on the diagram, spread down each region band.
    for region, items in by_region.items():
        top, height = REGIONS.get(region, (0.45, 0.1))
        band_top_y = fig_bottom + FIG_H * (1 - top)
        band_h = FIG_H * height
        for j, (n, kind, _com) in enumerate(items):
            step = band_h / (len(items) + 1)
            my = band_top_y - step * (j + 1)
            _draw_marker(c, fig_x + FIG_W / 2, my, n,
                         FAULT_COLOR if kind == "fault" else POSITIVE_COLOR)

    # Comment list to the right of the figure.
    tx = fig_x + FIG_W + 8
    tw = CELL_W - (FIG_W + 12)
    ty = oy_top - 22
    max_chars = max(12, int(tw / 3.4))
    line_h = 8.2
    bottom_limit = fig_bottom
    shown = 0
    total = len(comments)
    for n, kind, com in [(i, k, cm) for i, (k, cm) in enumerate(comments, start=1)]:
        color = FAULT_COLOR if kind == "fault" else POSITIVE_COLOR
        text = f"{n}. {com.get('text') or ''}"
        wrapped = textwrap.wrap(text, max_chars) or [f"{n}."]
        if ty - line_h * len(wrapped) < bottom_limit and shown < total:
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Oblique", 6.5)
            c.drawString(tx, ty - 6, f"+{total - shown} more…")
            break
        for k, line in enumerate(wrapped):
            c.setFillColor(color)
            c.setFont("Helvetica", 6.8)
            c.drawString(tx, ty - 6 - k * line_h, line)
        ty -= line_h * len(wrapped) + 1.5
        shown += 1

    if total == 0:
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(tx, oy_top - 28, "No faults logged.")


def _draw_page_frame(c, flight, date_str, summary, page_idx, page_count):
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN, PAGE_H - 30, "Flight Inspection Sheet")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10)
    label = f"{flight} Flight  ·  {date_str}"
    if page_count > 1:
        label += f"  ·  page {page_idx + 1} of {page_count}"
    c.drawString(MARGIN, PAGE_H - 46, label)

    # Summary box top-right
    box_w, box_h = 340, 40
    bx = PAGE_W - MARGIN - box_w
    by = PAGE_H - 52
    c.setStrokeColor(BORDER)
    c.setLineWidth(1)
    c.roundRect(bx, by, box_w, box_h, 5, stroke=1, fill=0)
    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    penalty = summary.get("penalty", 0)
    cells = [
        ("Present", str(summary["present"])),
        ("AWOL", str(summary.get("awol", 0))),
        ("Penalty", f"−{penalty:g}" if penalty else "0"),
        ("Total", f"{summary['total']:g}"),
        ("Average", f"{summary['average']:g}"),
    ]
    for i, (lab, val) in enumerate(cells):
        cx = bx + box_w * (i + 0.5) / len(cells)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.5)
        c.drawCentredString(cx, by + box_h - 13, lab)
        c.setFillColor(black)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(cx, by + 8, val)

    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(MARGIN, PAGE_H - 54, PAGE_W - MARGIN, PAGE_H - 54)


LIST_LINE_H = 11
LIST_HEADER_H = 24


def _absence_lists_height(awol: list, absent: list) -> float:
    """Height the AWOL / absent roll-call block needs."""
    rows = max(len(awol), len(absent), 1)
    return LIST_HEADER_H + rows * LIST_LINE_H + 8


def _person_name(p: dict) -> str:
    rank = f"{p['rank']} " if p.get("rank") else ""
    return f"{rank}{p['last_name']}, {p['first_name']}"[:60]


def _draw_absence_lists(c, awol: list, absent: list, x: float, y_top: float, width: float):
    """Two side-by-side lists — AWOL (with penalty) on the left, excused absent on
    the right — of just names, shown beneath the present cadets."""
    col_gap = 24
    col_w = (width - col_gap) / 2
    columns = [
        (f"AWOL ({len(awol)})  ·  −{len(awol) * 5:g}", FAULT_COLOR, awol),
        (f"Absent — excused ({len(absent)})", MUTED, absent),
    ]
    for ci, (title, color, people) in enumerate(columns):
        cx = x + ci * (col_w + col_gap)
        c.setFillColor(color)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(cx, y_top, title)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.5)
        c.line(cx, y_top - 5, cx + col_w, y_top - 5)
        ty = y_top - LIST_HEADER_H + 6
        if not people:
            c.setFillColor(MUTED)
            c.setFont("Helvetica-Oblique", 8.5)
            c.drawString(cx, ty, "None")
            continue
        c.setFillColor(black)
        c.setFont("Helvetica", 8.5)
        for p in people:
            c.drawString(cx, ty, _person_name(p))
            ty -= LIST_LINE_H


def _draw_list_page_header(c, flight: str, date_str: str):
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN, PAGE_H - 30, "Flight Inspection Sheet")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN, PAGE_H - 46, f"{flight} Flight  ·  {date_str}  ·  absentees")
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(MARGIN, PAGE_H - 54, PAGE_W - MARGIN, PAGE_H - 54)


def build_inspection_pdf(date_str: str, flights: list[dict]) -> bytes:
    """`flights` is the grouped sheet detail: a list of
    {flight, present, awol, penalty, total, average, cadets:[...]} in display
    order. Present cadets are drawn as full cells; AWOL and excused-absent cadets
    are listed compactly beneath them."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    for fl in sorted(flights, key=lambda f: _flight_order(f["flight"])):
        present = [c2 for c2 in fl["cadets"] if not c2["absent"]]
        awol = [c2 for c2 in fl["cadets"] if c2["awol"]]
        absent = [c2 for c2 in fl["cadets"] if c2["absent"] and not c2["awol"]]
        summary = {
            "present": fl["present"],
            "awol":    fl.get("awol", 0),
            "penalty": fl.get("penalty", 0),
            "total":   fl["total"],
            "average": fl["average"],
        }
        pages = [present[i : i + PER_PAGE] for i in range(0, len(present), PER_PAGE)] or [[]]
        for page_idx, page_cadets in enumerate(pages):
            _draw_page_frame(c, fl["flight"], date_str, summary, page_idx, len(pages))
            for i, cadet in enumerate(page_cadets):
                col = i % COLS
                row = i // COLS
                ox = MARGIN + col * CELL_W
                oy_top = PAGE_H - TITLE_H - row * CELL_H
                _draw_cadet(c, cadet, ox, oy_top)

            # On the last present page, append the AWOL / absent roll-call — below
            # the grid if it fits, otherwise on a fresh page.
            if page_idx == len(pages) - 1 and (awol or absent):
                rows_used = -(-len(page_cadets) // COLS)  # ceil
                y_top = PAGE_H - TITLE_H - rows_used * CELL_H - 14
                if y_top - _absence_lists_height(awol, absent) < MARGIN:
                    c.showPage()
                    _draw_list_page_header(c, fl["flight"], date_str)
                    y_top = PAGE_H - 72
                _draw_absence_lists(c, awol, absent, MARGIN, y_top, PAGE_W - 2 * MARGIN)
            c.showPage()

    if not flights:
        _draw_page_frame(c, "—", date_str,
                         {"present": 0, "awol": 0, "penalty": 0, "total": 0, "average": 0},
                         0, 1)
        c.showPage()

    c.save()
    return buf.getvalue()
