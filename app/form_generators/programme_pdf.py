"""A month's programme from the builder as a PDF, laid out like the Word doc
the squadron used to write each month (see the 2024-26 programmes): the
month as the title, the crest faint behind it, a gold header, a row per night
with a column per flight in each period and the break between them, sessions
in bold with who's running them in red, the uniform in its colour, the duty
NCO, and the month's important dates underneath. Page 2 is the roomings: the
same grid with each session's room in that room's colour, and a room key.

The website shows the first two pages as images
(routers/programme.publish_pdf_to_website), so the programme has to fit
page 1 and the roomings page 2.
"""

import io
import math
from datetime import date
from functools import cache
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# The doc's colours.
GOLD = colors.HexColor("#ffc000")
RED = colors.HexColor("#ff0000")
GREY = colors.HexColor("#d9d9d9")

PERIOD_TIMES = {1: "19:25 – 20:05", 2: "20:20 – 21:00"}
BREAK_TIME = "20:05-20:20"

# (background, text) for each room, in the order of the doc's room key.
ROOM_COLOURS = [
    ("#f4cccc", "#cc0000"), ("#fce5cd", "#e69138"), ("#d9ead3", "#6aa84f"), ("#d9d2e9", "#8e7cc3"),
    ("#c9daf8", "#3c78d8"), ("#ead1dc", "#a64d79"), ("#fff2cc", "#bf9000"), ("#d0e0e3", "#45818e"),
]

# The uniform column's fill, first match on the uniform's name; white text on each.
UNIFORM_COLOURS = [("sport", "#ff0000"), ("no.2c", "#073763"), ("no.2", "#a4c2f4"), ("no.3", "#93c47d"),
                   ("no.1", "#000000"), ("mtp", "#6aa84f"), ("civ", "#999999")]


@cache
def _fonts() -> tuple[str, str]:
    """Carlito, the open metric twin of the doc's Calibri."""
    for name in ("Carlito-Regular", "Carlito-Bold"):
        pdfmetrics.registerFont(TTFont(name, str(ASSETS / "fonts" / f"{name}.ttf")))
    return "Carlito-Regular", "Carlito-Bold"


def _style(size: float, bold: bool = True, colour=colors.black, align: int = 1) -> ParagraphStyle:
    regular, heavy = _fonts()
    return ParagraphStyle(f"s{size}{bold}{align}", fontName=heavy if bold else regular, fontSize=size,
                          leading=size * 1.2, alignment=align, textColor=colour)


def _p(text: str | None, style: ParagraphStyle) -> Paragraph:
    """Free text from staff reaches reportlab escaped: a stray "&" or "<"
    would otherwise break its mini-HTML."""
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def _hex(c) -> str:
    return c if isinstance(c, str) else "#" + c.hexval()[2:]


def _uniform_fill(uniform: str | None):
    key = (uniform or "").lower().replace(" ", "")
    return next((colors.HexColor(c) for word, c in UNIFORM_COLOURS if word in key), None)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def when_label(start: date, end: date | None = None) -> str:
    """"8th March", "21-22nd March", "30th March - 2nd April": the doc's way."""
    end = end or start
    if end <= start:
        return f"{_ordinal(start.day)} {start:%B}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}-{_ordinal(end.day)} {start:%B}"
    return f"{_ordinal(start.day)} {start:%B} - {_ordinal(end.day)} {end:%B}"


def _columns(flights: list[dict]) -> list:
    """The grid's columns after the date: each period's flights with the break
    between them. A flight is (period, key); the break is "break"."""
    return [(1, f["key"]) for f in flights] + ["break"] + [(2, f["key"]) for f in flights]


def _header(flights: list[dict], last: list[str]) -> tuple[list, list, list]:
    """The doc's two header rows: a flight kept apart (Probationary) heads its
    own column over both rows, the rest sit under "1st Period (times)"."""
    head = _style(10)
    apart = [f.get("apart", False) for f in flights]
    if all(apart):
        apart = [False] * len(flights)
    row0, row1, spans = [_p("Date", head)], [""], [("SPAN", (0, 0), (0, 1))]
    col = 1
    for period in (1, 2):
        under = None
        for f, alone in zip(flights, apart, strict=True):
            if alone:
                row0.append(_p(f["label"], head))
                row1.append("")
                spans.append(("SPAN", (col, 0), (col, 1)))
            else:
                row0.append(Paragraph(f"{'1<super>st</super>' if period == 1 else '2<super>nd</super>'} Period"
                                      f"<br/>({PERIOD_TIMES[period]})", head) if under is None else "")
                row1.append(_p(f["label"], head))
                under = col if under is None else under
                spans.append(("SPAN", (under, 0), (col, 0)))
            col += 1
        if period == 1:
            row0.append(Paragraph(f"Break<br/><font size=6.5>({BREAK_TIME})</font>", head))
            row1.append("")
            spans.append(("SPAN", (col, 0), (col, 1)))
            col += 1
    for name in last:
        row0.append(_p(name, head))
        row1.append("")
        spans.append(("SPAN", (col, 0), (col, 1)))
        col += 1
    return row0, row1, spans


def _cover(night: dict, columns: list) -> list[tuple]:
    """What's in each column of a night: a tuple of slot ids, or "break".
    When one whole-night session has everyone, it runs across the break too."""
    out = []
    for col in columns:
        if col == "break":
            out.append("break")
            continue
        period, flight = col
        out.append(tuple(s["id"] for s in night["slots"]
                         if (s["period"] is None or s["period"] == period) and flight in s["flights"]))
    firsts = {c for c in out if c != "break"}
    if len(firsts) == 1 and (only := next(iter(firsts))):
        slots = {s["id"]: s for s in night["slots"]}
        if all(slots[i]["period"] is None for i in only):
            out = [only] * len(out)
    return out


def _runs(keys: list) -> list[tuple[int, int]]:
    """Neighbouring columns with the same non-empty key, as (first, last)."""
    runs, c = [], 0
    while c < len(keys):
        end = c
        while end + 1 < len(keys) and keys[end + 1] == keys[c] and keys[c]:
            end += 1
        runs.append((c, end))
        c = end + 1
    return runs


def _grid_style() -> list:
    return [
        ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
        ("BACKGROUND", (0, 0), (-1, 1), GOLD),
        ("BACKGROUND", (0, 2), (0, -1), GOLD),
        ("BACKGROUND", (1, 2), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]


def _date_cell(d: date) -> Paragraph:
    return Paragraph(f"{d:%A}<br/>{d:%d/%m/%y}", _style(10))


def _duty(text: str | None, width: float):
    """The doc's duty NCO cell: the cadet on top, the duty adult under them
    in grey. Two names typed as "Cpl Tyrell / Fg Off Barker" (or on two lines)."""
    names = [n.strip() for n in (text or "").replace(" / ", "\n").split("\n") if n.strip()]
    if len(names) < 2:
        return _p(text, _style(8))
    inner = Table([[_p(n, _style(8))] for n in names], colWidths=[width])
    inner.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.75, colors.black),
        ("BACKGROUND", (0, 1), (-1, -1), GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return inner


def _session_cell(slots: list[dict]) -> tuple[Paragraph, colors.Color | None]:
    """"Title / Title" in bold over "who / who" in red, as the doc writes a
    flight split between two things. A kind with a colour fills the cell."""
    titles = " / ".join(escape(s["title"]) for s in slots)
    running = " / ".join(escape(s["running"]) for s in slots if s.get("running"))
    fill = next((s["colour"] for s in slots if s.get("colour")), None)
    title_colour = "#ffffff" if fill else "#000000"
    text = f'<font color="{title_colour}">{titles}</font>'
    if running:
        text += f'<br/><font color="{_hex(RED)}">{running}</font>'
    return Paragraph(text, _style(8)), colors.HexColor(fill) if fill else None


def _room_colours(rooms: list[dict]) -> dict[str, tuple[str, str]]:
    return {r["name"]: ROOM_COLOURS[i % len(ROOM_COLOURS)] for i, r in enumerate(rooms)}


def _on_page(canvas, doc):
    """The crest, faint and centred behind the page, as the doc's header has it."""
    w, h = 125 * mm, 165 * mm
    page_w, page_h = doc.pagesize
    canvas.drawImage(str(ASSETS / "squadron-crest-watermark.jpg"), (page_w - w) / 2, (page_h - h) / 2, w, h)


def build_programme_pdf(data: dict) -> bytes:
    """``data``: {title ("March 2026"), flights: [{key, label, apart}], notes,
    key_dates: [{when, what}], rooms: [{name, show}] (every room, in order, so
    a room keeps its colour; ``show`` puts it in the key),
    nights: [{date, uniform, duty_nco, slots: [{id, period, flights, title,
    running, room, colour}]}]} with each slot's flights filled in (never empty)."""
    flights = data["flights"]
    columns = _columns(flights)
    buf = io.BytesIO()
    margin = 8 * mm
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin,
                            topMargin=6 * mm, bottomMargin=margin, title=f"{data['title']} programme",
                            author="317 SMS")
    width = doc.width
    title = _p(data["title"], _style(22, bold=False))
    cell = _style(8)
    date_w, break_w, uniform_w, duty_w = 60, 44, 62, 70

    # ── page 1: the programme ──────────────────────────────────────────────
    col_w = (width - date_w - break_w - uniform_w - duty_w) / (2 * len(flights))
    widths = [date_w] + [break_w if c == "break" else col_w for c in columns] + [uniform_w, duty_w]
    row0, row1, style = _header(flights, ["Uniform", "Duty NCO"])
    rows = [row0, row1]
    style += _grid_style()
    for night in data["nights"]:
        r = len(rows)
        slots = {s["id"]: s for s in night["slots"]}
        cover = _cover(night, columns)
        row = [_date_cell(night["date"])] + [""] * len(columns)
        for first, last in _runs(cover):
            key = cover[first]
            if key == "break":
                row[1 + first] = _p("Break", _style(10))
                style.append(("BACKGROUND", (1 + first, r), (1 + first, r), GOLD))
                continue
            if key:
                content, fill = _session_cell([slots[i] for i in key])
                row[1 + first] = content
                if fill:
                    style.append(("BACKGROUND", (1 + first, r), (1 + last, r), fill))
            if last > first:
                style.append(("SPAN", (1 + first, r), (1 + last, r)))
        uniform_fill = _uniform_fill(night.get("uniform"))
        row.append(_p(night.get("uniform"), _style(9, colour=colors.white if uniform_fill else colors.black)))
        if uniform_fill:
            style.append(("BACKGROUND", (-2, r), (-2, r), uniform_fill))
        row.append(_duty(night.get("duty_nco"), duty_w))
        if "\n" in (night.get("duty_nco") or "") or " / " in (night.get("duty_nco") or ""):
            style.append(("LEFTPADDING", (-1, r), (-1, r), 0))
            style.append(("RIGHTPADDING", (-1, r), (-1, r), 0))
            style.append(("TOPPADDING", (-1, r), (-1, r), 0))
            style.append(("BOTTOMPADDING", (-1, r), (-1, r), 0))
        rows.append(row)
    if len(rows) == 2:
        rows.append([_p("No nights in this programme yet", cell)] + [""] * (len(widths) - 1))
        style.append(("SPAN", (0, 2), (-1, 2)))
    table = Table(rows, colWidths=widths)
    table.setStyle(TableStyle(style))
    story = [title, Spacer(1, 2 * mm), table]

    key_dates = [d for d in data.get("key_dates") or [] if (d.get("when") or d.get("what"))]
    if key_dates:
        story += [Spacer(1, 3 * mm), _p("Important Dates", _style(11, align=0)), Spacer(1, 2 * mm)]
        per = math.ceil(len(key_dates) / 3)
        groups = [key_dates[i * per:(i + 1) * per] for i in range(3)]
        gap = 10 * mm
        w = (width - 2 * gap) / 3
        cells = []
        for i in range(per):
            line = []
            for g, group in enumerate(groups):
                if i < len(group):
                    line += [_p(group[i]["when"], _style(10, align=0)),
                             _p(group[i]["what"], _style(10, bold=False, align=0))]
                else:
                    line += ["", ""]
                if g < 2:
                    line.append("")
            cells.append(line)
        dates = Table(cells, colWidths=[w * 0.35, w * 0.65, gap] * 2 + [w * 0.35, w * 0.65])
        dates_style = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                       ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        for g, group in enumerate(groups):
            if group:
                c = g * 3
                dates_style += [("BOX", (c, 0), (c + 1, len(group) - 1), 0.75, colors.black),
                                ("INNERGRID", (c, 0), (c + 1, len(group) - 1), 0.75, colors.black),
                                ("BACKGROUND", (c, 0), (c + 1, len(group) - 1), colors.white)]
        dates.setStyle(TableStyle(dates_style))
        story.append(dates)
    if (data.get("notes") or "").strip():
        story += [Spacer(1, 3 * mm), _p(data["notes"], _style(9, bold=False, align=0))]

    # ── page 2: the roomings ───────────────────────────────────────────────
    room_colour = _room_colours(data.get("rooms") or [])
    widths = [date_w] + [break_w if c == "break" else col_w for c in columns] + [uniform_w + duty_w]
    row0, row1, style = _header(flights, ["Uniform"])
    rows = [row0, row1]
    style += _grid_style()
    for night in data["nights"]:
        r = len(rows)
        slots = {s["id"]: s for s in night["slots"]}
        cover = _cover(night, columns)
        names = ["break" if k == "break" else
                 " / ".join(dict.fromkeys(slots[i]["room"] or "" for i in k)).strip(" /") for k in cover]
        row = [_date_cell(night["date"])] + [""] * len(columns)
        for first, last in _runs(names):
            name = names[first]
            if name == "break":
                row[1 + first] = _p("Break", _style(10))
                style.append(("BACKGROUND", (1 + first, r), (1 + first, r), GOLD))
                continue
            bg, fg = room_colour.get(name, ("#ffffff", _hex(RED)))
            row[1 + first] = _p(name, _style(8, colour=colors.HexColor(fg)))
            if name:
                style.append(("BACKGROUND", (1 + first, r), (1 + last, r), colors.HexColor(bg)))
            if last > first:
                style.append(("SPAN", (1 + first, r), (1 + last, r)))
        uniform_fill = _uniform_fill(night.get("uniform"))
        row.append(_p(night.get("uniform"), _style(9, colour=colors.white if uniform_fill else colors.black)))
        if uniform_fill:
            style.append(("BACKGROUND", (-1, r), (-1, r), uniform_fill))
        rows.append(row)
    if len(rows) == 2:
        rows.append([_p("No nights in this programme yet", cell)] + [""] * (len(widths) - 1))
        style.append(("SPAN", (0, 2), (-1, 2)))
    table = Table(rows, colWidths=widths)
    table.setStyle(TableStyle(style))
    story += [PageBreak(), title, Spacer(1, 2 * mm), table]

    if any(r.get("show", True) for r in data.get("rooms") or []):
        story += [Spacer(1, 2 * mm), _p("Room Key", _style(9, align=0)), Spacer(1, 1 * mm)]
        names = [r["name"] for r in data["rooms"] if r.get("show", True)]
        per = math.ceil(len(names) / 3)
        cells, key_style = [], [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        for i in range(per):
            line = []
            for g in range(3):
                n = g * per + i
                if n < len(names):
                    line += [_p(names[n], _style(9, align=0)), ""]
                    c = g * 3
                    key_style += [("BOX", (c, i), (c + 1, i), 0.75, colors.black),
                                  ("LINEAFTER", (c, i), (c, i), 0.75, colors.black),
                                  ("BACKGROUND", (c, i), (c, i), colors.white),
                                  ("BACKGROUND", (c + 1, i), (c + 1, i),
                                   colors.HexColor(room_colour[names[n]][0]))]
                else:
                    line += ["", ""]
                if g < 2:
                    line.append("")
            cells.append(line)
        key = Table(cells, colWidths=[30 * mm, 13 * mm, 12 * mm] * 2 + [30 * mm, 13 * mm], hAlign="LEFT")
        key.setStyle(TableStyle(key_style + [("TOPPADDING", (0, 0), (-1, -1), 5),
                                             ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story.append(key)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
