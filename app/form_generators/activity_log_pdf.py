"""A cadet's DofE section log as a PDF, laid out like the squadron's paper
"Activity Log" sheet so it can go to the assessor and into the award evidence
exactly as the spreadsheet did: the same banner, header boxes and
Date / What you did / Minutes / Initials table.

The Initials column is left blank on purpose: it's where the assessor signs
each row off by hand.
"""

import io
from datetime import date, datetime
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.paths import ASSETS_DIR

BANNER = ASSETS_DIR / "activity-log-banner.png"
# The sheet's own colours: red box borders, pale grey fields, pink table header.
LINE = colors.HexColor("#e06666")
FIELD = colors.HexColor("#f3f3f3")
HEADER = colors.HexColor("#f4cccc")
MUTED = colors.HexColor("#666666")


def _d(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


def build_activity_log_pdf(log: dict) -> bytes:
    """``log`` carries: cadet_name, section, level, duration_months, activity,
    goal, assessor_name, assessor_role, start_date, end_date (dates),
    weeks_done, weeks_needed, and entries [{date, description, minutes}]."""
    buf = io.BytesIO()
    title = f"{log['section']} Section {log['level']} DofE Activity Log — {log['cadet_name']}"
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=14 * mm, title=title,
    )
    styles = getSampleStyleSheet()
    label = ParagraphStyle("label", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9.5, leading=12)
    value = ParagraphStyle("value", parent=styles["Normal"], fontSize=10, leading=13)
    hint = ParagraphStyle("hint", parent=styles["Normal"], fontSize=8, textColor=MUTED)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=9.5, leading=12)

    def p(text, style=value):
        return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)

    story = []
    if BANNER.exists():
        story.append(Image(str(BANNER), width=doc.width, height=doc.width * 350 / 1270))
        story.append(Spacer(1, 6))

    # Header boxes: label, then a bordered grey field, two to a row like the sheet.
    w = doc.width
    lw, fw = 30 * mm, w / 2 - 30 * mm
    rows = [
        [p("Name:", label), p(log["cadet_name"]), "", ""],
        [p("Section:", label), p(log["section"]), p("Level:", label), p(log["level"])],
        [p("Timescale:", label), p(f"{log['duration_months']} months"),
         p("Weeks done:", label), p(f"{log['weeks_done']} of {log['weeks_needed']}")],
        [p("Activity:", label), p(log["activity"]), "", ""],
        [p("Goal:", label), p(log["goal"]), "", ""],
        [p("Assessor's name:", label), p(log["assessor_name"]), p("Assessor's role:", label), p(log["assessor_role"])],
        [p("Start date:", label), p(_d(log["start_date"])), p("End date:", label), p(_d(log["end_date"]))],
    ]
    header = Table(rows, colWidths=[lw, fw, lw, fw])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for r, row in enumerate(rows):
        spans = row[2] == ""
        if spans:
            style.append(("SPAN", (1, r), (3, r)))
        for c in ((1,) if spans else (1, 3)):
            end = 3 if spans else c
            style += [("BACKGROUND", (c, r), (end, r), FIELD), ("BOX", (c, r), (end, r), 0.75, LINE)]
    header.setStyle(TableStyle(style))
    story.append(header)
    story.append(Spacer(1, 4))
    story.append(p("Leader/Assessor: sign off each row with your initials.", hint))
    story.append(Spacer(1, 8))

    # The evidence table. Always a full first page of rows, so a short log
    # still prints as the sheet people know, with room to keep writing.
    entries = log["entries"]
    table_rows = [[p("", label), p("Date", label), p("What you did", label), p("Minutes", label), p("Initials", label)]]
    for i, e in enumerate(entries, start=1):
        table_rows.append([p(i, hint), p(_d(e["date"]), cell), p(e["description"], cell), p(e["minutes"], cell), ""])
    for i in range(len(entries) + 1, max(len(entries), 14) + 1):
        table_rows.append([p(i, hint), "", "", "", ""])
    total = sum(e["minutes"] for e in entries)
    table_rows.append(["", "", p("Total", label), p(total, label), ""])
    nw, dw, mw, iw = 10 * mm, 24 * mm, 20 * mm, 20 * mm
    table = Table(table_rows, colWidths=[nw, dw, w - nw - dw - mw - iw, mw, iw], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (-1, 0), HEADER),
        ("GRID", (1, 0), (-1, -2), 0.75, LINE),
        ("BOX", (2, -1), (3, -1), 0.75, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))
    story.append(p(f"Exported from 317 SMS on {datetime.now().strftime('%d/%m/%Y')}", hint))

    doc.build(story)
    return buf.getvalue()
