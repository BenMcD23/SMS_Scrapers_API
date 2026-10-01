import io
import logging
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import black
from reportlab.pdfgen import canvas as rl_canvas

from assessment_builders.pdf_utils import draw_multiline, draw_signature
from core.paths import ASSESSMENT_SHEETS_DIR

logger = logging.getLogger(__name__)

# --- Configuration ---
TEMPLATE_PATH = str(ASSESSMENT_SHEETS_DIR / "Blue_Space.pdf")
PAGE_W, PAGE_H = 595.2, 841.92

# Centre of the hexagon tick boxes (reportlab coords: 0 = bottom of page)
TICK_X = 84.5
CHECKLIST_Y = {
    "pts":         559.4,
    "section_1a":  536.9,
    "section_1b":  513.9,
    "section_2":   491.4,
    "section_3":   468.9,
    "section_4":   445.9,
    "section_5":   423.4,
    "practical":   400.4,
}

# Text sits 2pt above the start of each underline.
TEXT_FIELDS = {
    "name":            (106, 618),
    "sqn":             (425, 618),
    "date_completed":  (157, 603),
    "pts_date":        (283, 559),
    "cadet_date":      (289, 155),
    "instructor_name": (173, 91),
    "instructor_date": (439, 91),
}

# The ruled box under "Practical experiments to support PTS (list below)"
# spans x 71.5–550, y 241–316; six 9pt lines fit inside it.
EXPERIMENTS_POS = (76, 305)
EXPERIMENTS_MAX_X = 545
EXPERIMENTS_MAX_LINES = 6

# Signature boxes (x1, y1, x2, y2). The instructor's name takes the left of
# their underline, so the signature goes in the right-hand part.
CADET_SIG_BOX = (113, 154, 252, 172)
INSTRUCTOR_SIG_BOX = (290, 90, 400, 108)


def _draw_tick(c, cx: float, cy: float):
    """A tick stroked as a path — a dingbat glyph renders as a box in viewers
    that don't embed the symbol font."""
    c.setStrokeColor(black)
    c.setLineWidth(1.8)
    c.setLineCap(1)
    c.setLineJoin(1)
    path = c.beginPath()
    path.moveTo(cx - 5, cy)
    path.lineTo(cx - 1.5, cy - 4)
    path.lineTo(cx + 5.5, cy + 5)
    c.drawPath(path, stroke=1, fill=0)


def _build_overlay(data: dict) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setFillColor(black)

    # -- 1. Header --
    c.setFont("Helvetica", 10)
    c.drawString(*TEXT_FIELDS["name"], data.get("cadet_name", ""))
    c.drawString(*TEXT_FIELDS["sqn"], "317")
    c.drawString(*TEXT_FIELDS["date_completed"], data.get("date", ""))

    # -- 2. Ticks in the hexagons --
    for key, checked in data.get("checklist", {}).items():
        if checked and key in CHECKLIST_Y:
            _draw_tick(c, TICK_X, CHECKLIST_Y[key])

    c.setFont("Helvetica", 10)
    if data.get("pts_date"):
        c.drawString(*TEXT_FIELDS["pts_date"], data["pts_date"])

    # -- 3. Practical experiments --
    draw_multiline(c, data.get("experiments", ""), *EXPERIMENTS_POS,
                   max_x=EXPERIMENTS_MAX_X, max_lines=EXPERIMENTS_MAX_LINES)

    # -- 4. Cadet sign-off --
    if data.get("cadet_signature"):
        draw_signature(c, data["cadet_signature"], CADET_SIG_BOX)
        c.setFont("Helvetica", 10)
        c.drawString(*TEXT_FIELDS["cadet_date"], data.get("date", ""))

    # -- 5. Instructor sign-off --
    c.setFont("Helvetica", 10)
    c.drawString(*TEXT_FIELDS["instructor_name"], data.get("assessor_name", ""))
    c.drawString(*TEXT_FIELDS["instructor_date"], data.get("date", ""))
    if data.get("assessor_signature"):
        draw_signature(c, data["assessor_signature"], INSTRUCTOR_SIG_BOX)

    c.save()
    buf.seek(0)
    return buf.read()


def generate_space_pdf(data: dict) -> bytes:
    overlay_bytes = _build_overlay(data)
    try:
        template_reader = PdfReader(TEMPLATE_PATH)
        overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
        writer = PdfWriter()
        page = template_reader.pages[0]
        page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)
        out_buf = io.BytesIO()
        writer.write(out_buf)
        return out_buf.getvalue()
    except Exception as e:
        logger.error(f"Template merge error: {e}")
        return overlay_bytes


def _display_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%d/%m/%y")
    except (ValueError, TypeError):
        return raw or ""


def process_space_data(payload: dict, cadet) -> dict:
    """Process raw API payload into PDF builder format."""
    checklist = {key: bool(payload.get("checklist", {}).get(key)) for key in CHECKLIST_Y}
    return {
        "cadet_name": f"{cadet.first_name or ''} {(cadet.last_name or '').upper()}".strip(),
        "checklist": checklist,
        "pts_date": _display_date(payload.get("pts_date", "")),
        "experiments": payload.get("experiments", ""),
        "passed": all(checklist.values()),
        "assessor_name": payload.get("assessor_name", ""),
        "assessor_signature": payload.get("assessor_signature"),
        "cadet_signature": payload.get("cadet_signature"),
        "date": _display_date(payload.get("date", "")),
    }
