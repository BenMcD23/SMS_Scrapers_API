"""A month's programme from the builder as a PDF, laid out like the Google Doc
it replaces so the website and the noticeboard look the same as before.

Page 1 is the programme: a row per night, a column per flight in each half,
with a session shared by neighbouring flights merged across their columns.
Page 2 is rooms and who's running what. The website shows the first two pages
as images (routers/programme.publish_pdf_to_website), so the programme has to
fit page 1: the type is small and the table never splits a night.
"""

import io
from datetime import date
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#1f3a68")
MUTED = colors.HexColor("#555555")
GRID = colors.HexColor("#b8c2d3")
HEAD = colors.HexColor("#e8edf5")

# Light tints, one per kind of night, so a printed month shows its balance at
# a glance. Keys are core.programme.CATEGORIES.
TINTS = {
    "drill": "#e3ebf7", "classifications": "#efe6f7", "lessons": "#e6f2f7", "fieldcraft": "#e7f3e3",
    "first_aid": "#fbe9e7", "stem": "#e3f2f1", "sport": "#fdf3dc", "dofe": "#eef3dd", "music": "#f6e6ef",
    "leadership": "#e9e7f6", "admin": "#f0f0f0", "social": "#fcebdc", "other": "#f5f5f5",
}

PERIOD_TIMES = {1: "19:25–20:05", 2: "20:20–21:00"}


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=15, leading=18, textColor=NAVY,
                                alignment=0, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontSize=8, leading=10, textColor=MUTED),
        "head": ParagraphStyle("h", parent=base["Normal"], fontSize=7.5, leading=9, alignment=1,
                               fontName="Helvetica-Bold", textColor=NAVY),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=7, leading=8.4, alignment=1),
        "left": ParagraphStyle("l", parent=base["Normal"], fontSize=7.5, leading=9),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=12, textColor=NAVY, spaceAfter=4),
    }


def _p(text: str | None, style) -> Paragraph:
    """Free text from staff reaches reportlab escaped: a stray "&" or "<"
    would otherwise break its mini-HTML."""
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


def _night(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def build_programme_pdf(data: dict) -> bytes:
    """``data``: {title, subtitle, flights: [{key, label}], notes,
    nights: [{date, uniform, duty_nco, notes, slots: [{id, period, flights,
    title, category, room, running}]}]} with each slot's flights already
    filled in (never empty)."""
    st = _styles()
    flights = data["flights"]
    columns = [(period, f["key"]) for period in (1, 2) for f in flights]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=9 * mm, bottomMargin=9 * mm, title=data["title"], author="317 SMS")
    width = doc.width
    story = [_p(data["title"], st["title"])]
    if data.get("subtitle"):
        story.append(_p(data["subtitle"], st["sub"]))
    story.append(Spacer(1, 4 * mm))

    # Two header rows: the halves, then the flights under each.
    date_w, uniform_w, dnco_w = 24 * mm, 28 * mm, 28 * mm
    col_w = (width - date_w - uniform_w - dnco_w) / len(columns)
    header1 = [_p("Date", st["head"])]
    for period in (1, 2):
        header1 += [_p(f"{'1st' if period == 1 else '2nd'} half ({PERIOD_TIMES[period]})", st["head"])]
        header1 += [""] * (len(flights) - 1)
    header1 += [_p("Uniform", st["head"]), _p("Duty NCO", st["head"])]
    header2 = [""] + [_p(f["label"], st["head"]) for _ in (1, 2) for f in flights] + ["", ""]
    rows = [header1, header2]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("BACKGROUND", (0, 0), (-1, 1), HEAD),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("SPAN", (0, 0), (0, 1)),
        ("SPAN", (-2, 0), (-2, 1)),
        ("SPAN", (-1, 0), (-1, 1)),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(2):
        start = 1 + i * len(flights)
        style.append(("SPAN", (start, 0), (start + len(flights) - 1, 0)))

    for night in data["nights"]:
        r = len(rows)
        covering: list[list[dict]] = []
        for period, flight in columns:
            covering.append([s for s in night["slots"]
                             if (s["period"] is None or s["period"] == period) and flight in s["flights"]])
        row = [_p(_night(night["date"]), st["head"])]
        c = 0
        while c < len(columns):
            ids = [s["id"] for s in covering[c]]
            end = c
            while end + 1 < len(columns) and [s["id"] for s in covering[end + 1]] == ids and ids:
                end += 1
            text = "\n— or —\n".join(
                s["title"] + (f"\n{s['running']}" if s.get("running") else "") for s in covering[c])
            row.append(_p(text, st["cell"]))
            row += [""] * (end - c)
            if end > c:
                style.append(("SPAN", (1 + c, r), (1 + end, r)))
            if covering[c]:
                tint = TINTS.get(covering[c][0]["category"], TINTS["other"])
                style.append(("BACKGROUND", (1 + c, r), (1 + end, r), colors.HexColor(tint)))
            c = end + 1
        row += [_p(night.get("uniform"), st["cell"]), _p(night.get("duty_nco"), st["cell"])]
        rows.append(row)

    if len(rows) == 2:
        rows.append([_p("No nights in this programme yet", st["cell"])] + [""] * (len(columns) + 2))
        style.append(("SPAN", (0, 2), (-1, 2)))
    table = Table(rows, colWidths=[date_w] + [col_w] * len(columns) + [uniform_w, dnco_w], repeatRows=2)
    table.setStyle(TableStyle(style))
    story.append(table)

    notes = [(n["date"], n["notes"]) for n in data["nights"] if (n.get("notes") or "").strip()]
    if data.get("notes") or notes:
        story.append(Spacer(1, 4 * mm))
        if data.get("notes"):
            story.append(_p(data["notes"], st["left"]))
        for d, text in notes:
            story.append(Paragraph(f"<b>{_night(d)}:</b> {escape(text)}", st["left"]))

    story += [PageBreak(), _p("Rooms and who's running what", st["h2"])]
    room_rows = [[_p(h, st["head"]) for h in ("Date", "Half", "Session", "Who", "Room", "Running it")]]
    for night in data["nights"]:
        for s in sorted(night["slots"], key=lambda s: (s["period"] or 0, s["id"])):
            half = {1: "1st", 2: "2nd"}.get(s["period"], "All night")
            room_rows.append([_p(_night(night["date"]), st["left"]), _p(half, st["left"]),
                              _p(s["title"], st["left"]), _p(s.get("who"), st["left"]),
                              _p(s.get("room") or "—", st["left"]), _p(s.get("running") or "—", st["left"])])
    if len(room_rows) == 1:
        room_rows.append([_p("No sessions yet", st["left"])] + [""] * 5)
    rooms = Table(room_rows, colWidths=[24 * mm, 18 * mm, 70 * mm, 50 * mm, 45 * mm, width - 207 * mm],
                  repeatRows=1)
    rooms.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("BACKGROUND", (0, 0), (-1, 0), HEAD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(rooms)
    doc.build(story)
    return buf.getvalue()
