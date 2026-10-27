"""Render a committee purchase request as a one-page PDF.

Generated once at submission and stored on the CommitteeRequest row, then reused
for the OC/committee emails and site downloads.
"""

import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ACCENT = colors.HexColor("#1565c0")
MUTED = colors.HexColor("#555555")
BORDER = colors.HexColor("#dddddd")


def _gbp(amount: float) -> str:
    return f"£{amount:,.2f}"


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_committee_request_pdf(
    reference: str,
    title: str,
    requester_name: str,
    requester_email: str,
    created_at: datetime,
    items: list[dict],
    total: float,
    justification: str,
) -> bytes:
    """Return the request as PDF bytes. ``items`` is
    [{"description", "quantity", "unit_cost", "cost"}], ``cost`` being the line
    total; items without a quantity read as one."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Committee Request {reference}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=2,
                        textColor=colors.black)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10, textColor=MUTED,
                         spaceAfter=12)
    label = ParagraphStyle("label", parent=styles["Normal"], fontSize=9, textColor=MUTED)
    value = ParagraphStyle("value", parent=styles["Normal"], fontSize=11)
    section = ParagraphStyle("section", parent=styles["Heading2"], fontSize=12,
                             spaceBefore=16, spaceAfter=6, textColor=ACCENT)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=15)
    cost_cell = ParagraphStyle("cost", parent=styles["Normal"], fontSize=10,
                               alignment=TA_RIGHT)

    story = []

    story.append(Paragraph("317 Failsworth & Newton Heath Squadron ATC", h1))
    story.append(Paragraph("Committee Purchase Request", sub))
    story.append(Table(
        [[""]], colWidths=[doc.width],
        style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.5, ACCENT)]),
    ))
    story.append(Spacer(1, 10))

    # Header meta grid.
    meta = [
        [Paragraph("Reference", label), Paragraph(f"<b>{reference}</b>", value)],
        [Paragraph("Date", label),
         Paragraph(created_at.strftime("%d/%m/%Y"), value)],
        [Paragraph("Requested by", label),
         Paragraph(f"{requester_name} &lt;{requester_email}&gt;", value)],
        [Paragraph("Title", label), Paragraph(escape(title), value)],
    ]
    meta_tbl = Table(meta, colWidths=[35 * mm, doc.width - 35 * mm])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(meta_tbl)

    # Justification first: the committee reads why before what.
    if justification and justification.strip():
        story.append(Paragraph("Justification", section))
        for para in justification.strip().split("\n"):
            story.append(Paragraph(escape(para) or "&nbsp;", body))

    # Items table.
    story.append(Paragraph("Items", section))
    num_w = 20 * mm
    money_w = 28 * mm
    rows = [[
        Paragraph("<b>Description</b>", body),
        Paragraph("<b>Qty</b>", cost_cell),
        Paragraph("<b>Unit cost</b>", cost_cell),
        Paragraph("<b>Total</b>", cost_cell),
    ]]
    for item in items:
        desc = escape(str(item.get("description", "")))
        cost = _num(item.get("cost"))
        quantity = item.get("quantity") or 1
        unit_cost = item.get("unit_cost")
        unit_cost = _num(unit_cost) if unit_cost is not None else cost / quantity
        rows.append([
            Paragraph(desc, body),
            Paragraph(str(quantity), cost_cell),
            Paragraph(_gbp(unit_cost), cost_cell),
            Paragraph(_gbp(cost), cost_cell),
        ])
    rows.append([Paragraph("<b>Total</b>", body), "", "",
                 Paragraph(f"<b>{_gbp(total)}</b>", cost_cell)])

    items_tbl = Table(rows, colWidths=[doc.width - num_w - 2 * money_w, num_w, money_w, money_w],
                      repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 1, ACCENT),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, BORDER),
        ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT),
        ("SPAN", (0, -1), (2, -1)),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(items_tbl)

    doc.build(story)
    return buf.getvalue()
