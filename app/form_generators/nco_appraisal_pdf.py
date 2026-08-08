"""NCO appraisal as a PDF — the download option, and what gets emailed to the NCO.

Deliberately rebuilt in reportlab rather than converted from the Word template:
there's no office suite in the container, and the appraisal is a fixed set of
boxes that lays out cleanly on its own. The wording of every field comes from
form_generators.nco_appraisal_gen so the PDF and the .docx always agree.
"""

import io
from html import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from form_generators.nco_appraisal_gen import appraisal_context

ACCENT = colors.HexColor("#1565c0")
MUTED = colors.HexColor("#555555")
BORDER = colors.HexColor("#dddddd")
HEAD_BG = colors.HexColor("#f4f6f9")
CONCERN = colors.HexColor("#c62828")

# Body sections in form order. Targets carry their own numbering from
# `appraisal_context`, so they render like any other block of lines.
SECTIONS = [
    ("general_observations", "General Observations"),
    ("effectiveness_in_role", "Effectiveness in Role"),
    ("strengths", "Strengths"),
    ("weaknesses", "Weaknesses"),
    ("targets_numbered", "Targets"),
]

# The form's sign-off row. Fixed post holders, matching the Word template —
# printed blank for wet signatures.
SIGNATORIES = [
    ("Commanding Officer", "Flt Lt Doherty"),
    ("Sqn SNCO", "Sgt Lloyd Morris"),
    ("Sqn FS", ""),
]


def _para(text: str, style) -> Paragraph:
    """Appraisal text is free-form staff writing, so it reaches reportlab
    escaped — a stray '&' or '<' would otherwise break the mini-HTML parser."""
    return Paragraph(escape(text or ""), style)


def _multiline(text: str, style) -> list:
    """Blank lines become spacing, so a pasted multi-paragraph section keeps
    its shape instead of collapsing into one block."""
    return [
        Paragraph(escape(line) if line.strip() else "&nbsp;", style)
        for line in (text or "").strip().split("\n")
    ] or [Paragraph("&nbsp;", style)]


def _boxed_row(labels_values, width, styles, highlight: set[int] | None = None) -> Table:
    """A header/value strip like the form's top and bottom rows. `highlight`
    holds column indexes to print in red (a Yes that needs attention)."""
    col = width / len(labels_values)
    table = Table(
        [
            [_para(label, styles["label"]) for label, _ in labels_values],
            [
                Paragraph(
                    escape(value or "—"),
                    styles["flagged"] if highlight and i in highlight else styles["value"],
                )
                for i, (_, value) in enumerate(labels_values)
            ],
        ],
        colWidths=[col] * len(labels_values),
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def build_appraisal_pdf(appraisal) -> bytes:
    """`appraisal` is an NcoAppraisal row; returns the PDF bytes."""
    ctx = appraisal_context(appraisal)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"NCO Appraisal — {ctx['name'] or 'Unnamed'}",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=2,
                        textColor=colors.black)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10, textColor=MUTED,
                         spaceAfter=12)
    label = ParagraphStyle("label", parent=styles["Normal"], fontSize=8.5, textColor=MUTED)
    value = ParagraphStyle("value", parent=styles["Normal"], fontSize=11, leading=14)
    flagged = ParagraphStyle("flagged", parent=value, textColor=CONCERN,
                             fontName="Helvetica-Bold")
    section = ParagraphStyle("section", parent=styles["Heading2"], fontSize=12,
                             spaceBefore=16, spaceAfter=5, textColor=ACCENT)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=15)

    box_styles = {"label": label, "value": value, "flagged": flagged}

    story = [
        Paragraph("317 (Failsworth) Squadron RAFAC", h1),
        Paragraph(
            "NCO Appraisal"
            + (f" — {appraisal.appraisal_date.strftime('%d/%m/%Y')}"
               if appraisal.appraisal_date else ""),
            sub,
        ),
        Table(
            [[""]], colWidths=[doc.width],
            style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.5, ACCENT)]),
        ),
        Spacer(1, 12),
        _boxed_row(
            [("NCO Name", ctx["name"]), ("Age", ctx["age"]), ("Attendance", ctx["attendance"])],
            doc.width, box_styles,
        ),
    ]

    for key, heading in SECTIONS:
        story.append(Paragraph(heading, section))
        story.extend(_multiline(ctx[key], body))

    # Footer row — a "Yes" on either flag is the thing a reader must not miss.
    flags = [
        ("Next NCO Review", ctx["next_review"]),
        ("Cause for Concern", ctx["cause_for_concern"]),
        ("Extend Probation", ctx["extend_probation"]),
    ]
    highlight = {
        i for i, (_, v) in enumerate(flags) if i and v == "Yes"
    }
    story.append(Spacer(1, 18))
    story.append(_boxed_row(flags, doc.width, box_styles, highlight))

    # Sign-off block, left blank for signatures.
    signoff = Table(
        [
            [_para(post, label) for post, _ in SIGNATORIES],
            [_para(name, value) for _, name in SIGNATORIES],
        ],
        colWidths=[doc.width / 3] * 3,
        rowHeights=[None, 22 * mm],
    )
    signoff.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 1), (-1, 1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(KeepTogether([Paragraph("Agreed by", section), signoff]))

    doc.build(story)
    return buf.getvalue()
