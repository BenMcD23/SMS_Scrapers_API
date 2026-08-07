"""Render an NCO session plan as a PDF, for printing or handing over on the night.

Deliberately carries the plan only — not staff feedback, amendment notes or the
comment thread. Those are review chatter; what gets printed is the thing you
stand up and run.
"""

import io
from html import escape
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ACCENT = colors.HexColor("#1565c0")
MUTED = colors.HexColor("#555555")
BORDER = colors.HexColor("#dddddd")

SECTION_LABELS = [
    ("situation", "Situation"),
    ("mission", "Mission"),
    ("execution", "Execution"),
    ("any_questions", "Any Questions"),
    ("check_understanding", "Check Understanding"),
]


def _para(text: str, style) -> Paragraph:
    """Plan text is free-form NCO writing, so it reaches reportlab escaped —
    a stray '&' or '<' would otherwise blow up the mini-HTML parser."""
    return Paragraph(escape(text or ""), style)


def _multiline(text: str, style) -> list:
    """Blank lines become spacing, so pasted multi-paragraph text keeps shape."""
    out = []
    for line in (text or "").strip().split("\n"):
        out.append(Paragraph(escape(line) if line.strip() else "&nbsp;", style))
    return out or [Paragraph("&nbsp;", style)]


def build_session_plan_pdf(plan: dict) -> bytes:
    """``plan`` is the SessionPlan row as a dict of its content fields."""
    buf = io.BytesIO()
    name = plan.get("session_name") or "Untitled plan"
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Session Plan — {name}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=2,
                        textColor=colors.black)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10, textColor=MUTED,
                         spaceAfter=12)
    label = ParagraphStyle("label", parent=styles["Normal"], fontSize=9, textColor=MUTED)
    value = ParagraphStyle("value", parent=styles["Normal"], fontSize=10.5, leading=14)
    section = ParagraphStyle("section", parent=styles["Heading2"], fontSize=12,
                             spaceBefore=14, spaceAfter=5, textColor=ACCENT)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=15)

    story = []
    story.append(Paragraph("317 (Failsworth) Squadron RAFAC", h1))
    story.append(Paragraph("Session Plan", sub))
    story.append(Table(
        [[""]], colWidths=[doc.width],
        style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.5, ACCENT)]),
    ))
    story.append(Spacer(1, 10))

    # Header block — the paper plan's top-of-page details.
    meta_rows = [
        ("Session", plan.get("session_name") or "—"),
        ("Session I/C", plan.get("session_ic") or "—"),
        ("Date", plan.get("session_date") or "—"),
        ("Aim", plan.get("aim") or "—"),
        ("Participants", plan.get("participants") or "—"),
        ("Rooming", plan.get("rooming") or "—"),
        ("Equipment", plan.get("equipment") or "—"),
    ]
    meta = Table(
        [[_para(k, label), _para(v, value)] for k, v in meta_rows],
        colWidths=[32 * mm, doc.width - 32 * mm],
    )
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER),
    ]))
    story.append(meta)

    # The five-part section plan.
    for key, heading in SECTION_LABELS:
        story.append(Paragraph(heading, section))
        story.extend(_multiline(plan.get(key, ""), body))

    # Timetable, when there is one.
    timetable = [r for r in (plan.get("timetable") or []) if any(r.values())]
    if timetable:
        rows = [[
            _para("Timing", label), _para("Event", label), _para("Location", label),
        ]]
        for row in timetable:
            rows.append([
                _para(row.get("timing", ""), body),
                _para(row.get("event", ""), body),
                _para(row.get("location", ""), body),
            ])
        table = Table(
            rows,
            colWidths=[25 * mm, doc.width - 25 * mm - 35 * mm, 35 * mm],
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 1, ACCENT),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(KeepTogether([Paragraph("Timetable", section), table]))

    if (plan.get("notes") or "").strip():
        story.append(Paragraph("Notes", section))
        story.extend(_multiline(plan["notes"], body))

    # Footer line: who wrote it, and whether it has been signed off.
    story.append(Spacer(1, 18))
    footer_bits = [f"Written by {plan.get('author_name') or 'unknown'}"]
    if plan.get("approved_by"):
        approved = f"Approved by {plan['approved_by']}"
        if plan.get("approved_at"):
            approved += f" on {plan['approved_at']}"
        footer_bits.append(approved)
    else:
        footer_bits.append("Not yet approved")
    footer_bits.append(f"Exported {datetime.now().strftime('%d/%m/%Y')}")
    story.append(_para(" · ".join(footer_bits), label))

    doc.build(story)
    return buf.getvalue()
