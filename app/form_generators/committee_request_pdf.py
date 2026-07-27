"""Render a committee purchase request as a one-page PDF.

Generated once at submission and stored on the CommitteeRequest row, then reused
for the OC/committee emails and site downloads.
"""

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

ACCENT = colors.HexColor("#1565c0")
MUTED = colors.HexColor("#555555")
BORDER = colors.HexColor("#dddddd")


def _gbp(amount: float) -> str:
    return f"£{amount:,.2f}"


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
    """Return the request as PDF bytes. ``items`` is [{"description", "cost"}]."""
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

    story.append(Paragraph("317 (Wimbledon) Squadron ATC", h1))
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
        [Paragraph("Title", label), Paragraph(title, value)],
    ]
    meta_tbl = Table(meta, colWidths=[35 * mm, doc.width - 35 * mm])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(meta_tbl)

    # Items table.
    story.append(Paragraph("Items", section))
    rows = [[Paragraph("<b>Description</b>", body), Paragraph("<b>Cost</b>", cost_cell)]]
    for item in items:
        desc = str(item.get("description", ""))
        try:
            cost = float(item.get("cost", 0) or 0)
        except (TypeError, ValueError):
            cost = 0.0
        rows.append([Paragraph(desc, body), Paragraph(_gbp(cost), cost_cell)])
    rows.append([Paragraph("<b>Total</b>", body), Paragraph(f"<b>{_gbp(total)}</b>", cost_cell)])

    items_tbl = Table(rows, colWidths=[doc.width - 35 * mm, 35 * mm])
    items_tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 1, ACCENT),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, BORDER),
        ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(items_tbl)

    # Justification.
    if justification and justification.strip():
        story.append(Paragraph("Justification", section))
        for para in justification.strip().split("\n"):
            story.append(Paragraph(para or "&nbsp;", body))

    doc.build(story)
    return buf.getvalue()
