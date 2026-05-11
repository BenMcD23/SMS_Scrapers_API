import io
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import HexColor, black
from pypdf import PdfReader, PdfWriter
import base64
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage
from datetime import datetime
from pathlib import Path

# --- Configuration ---
TEMPLATE_PATH = str(Path(__file__).parent.parent / "assessment_sheets" / "MOI.pdf")
PAGE_W, PAGE_H = 842.00, 596.00  # landscape A4
CIRCLE_RADIUS = 8

SCORE_X = {1: 717, 2: 737, 3: 757, 4: 776, 5: 796}

# Questions split by page — values are the y coordinate for that question's row
PAGE1_SCORES = {
    1:  415,
    2:  397,
    3:  325,
    4:  308,
    5:  290,
    6:  207,
    7:  185,
    8:  104,
    9:  86,
}

PAGE2_SCORES = {
    10: 559,
    11: 536,
    12: 452,
    13: 431,
}

# Section comment anchor points — (page, x, y)
SECTION_COMMENTS = {
    "identifying": (1, 32, 365),
    "planning":    (1, 32, 258),
    "resources":   (1, 32, 155),
    "delivery":    (1, 32,  57),
    "assessment":  (2, 32, 504),
    "evaluation":  (2, 32, 400),
}

# Text fields — (page, x, y)
TEXT_FIELDS = {
    "cadet_surname":      (1, 150, 505),
    "cadet_forename":     (1, 548, 505),
    "sqn_df":             (1,  98, 483),
    "wing_ccf":           (1, 303, 483),
    "date":               (1, 548, 483),
    "bader_reference":    (1, 155, 462),
    "place_of_assessment":(1, 548, 462),
    "strengths":          (2,  32, 300),
    "improvements":       (2,  32, 240),
    "general_comments":   (2,  32, 178),
    "total_score":        (2, 755, 167),
    "pass_yes":           (2, 735, 124),
    "pass_no":            (2, 789, 125),
    "assessor_name":      (2, 237,  50),
    "assessor_role":      (2, 630,  50),
}

# Signature bounding boxes — (x1, y1, x2, y2) in page 2 coords
ASSESSOR_SIG_BOX  = (225, 70, 419, 97)
CANDIDATE_SIG_BOX = (620, 72, 810, 97)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _draw_score_circles(c, scores: dict, page_scores: dict):
    c.setLineWidth(1.5)
    for q_id, y in page_scores.items():
        score = scores.get(q_id) or scores.get(str(q_id))
        if score is None:
            continue
        score = int(score)
        x = SCORE_X.get(score)
        if x is None:
            continue
        color = (HexColor("#ef4444") if score == 1
                 else HexColor("#22c55e") if score == 5
                 else HexColor("#3b82f6"))
        c.setStrokeColor(color)
        c.circle(x, y, CIRCLE_RADIUS, stroke=1, fill=0)


def _draw_multiline(c, text: str, x: float, y: float,
                    max_x: float = 810, max_lines: int = 4, font_size: int = 9):
    if not text:
        return
    font_name = "Helvetica"
    c.setFont(font_name, font_size)
    c.setFillColor(black)
    available_w = max_x - x
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        if c.stringWidth(test, font_name, font_size) <= available_w:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    for line in lines[:max_lines]:
        c.drawString(x, y, line)
        y -= font_size + 3


def _draw_signature(c, sig: str, box: tuple):
    """Draw a base64 image signature (or plain text) into a bounding box."""
    x1, y1, x2, y2 = box
    box_w, box_h = x2 - x1, y2 - y1

    if sig and sig.startswith("data:image"):
        try:
            _, b64data = sig.split(",", 1)
            img_bytes = base64.b64decode(b64data)
            pil_img = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA")
            bbox = pil_img.getbbox()
            if bbox:
                pil_img = pil_img.crop(bbox)
            img_w, img_h = pil_img.size
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            buf.seek(0)

            aspect = img_w / img_h
            draw_w = box_w
            draw_h = draw_w / aspect
            if draw_h > box_h:
                draw_h = box_h
                draw_w = draw_h * aspect

            draw_x = x1 + (box_w - draw_w) / 2
            draw_y = y1 + (box_h - draw_h) / 2

            c.drawImage(ImageReader(buf), draw_x, draw_y,
                        width=draw_w, height=draw_h,
                        preserveAspectRatio=False, mask="auto")
        except Exception as e:
            print(f"[PDF] Signature error: {e}")
            c.setFont("Helvetica", 9)
            c.drawString(x1, y1 + 5, "[signature error]")
    elif sig:
        c.setFont("Helvetica", 9)
        c.drawString(x1, y1 + 5, sig)


# ─── Overlay builders ─────────────────────────────────────────────────────────

def _build_page1_overlay(data: dict) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    scores = data["scores"]
    section_comments = data.get("section_comments", {})

    _draw_score_circles(c, scores, PAGE1_SCORES)

    # Header text
    c.setFont("Helvetica", 10)
    c.setFillColor(black)
    for key in ("cadet_surname", "cadet_forename", "sqn_df", "wing_ccf",
                "date", "bader_reference", "place_of_assessment"):
        _, x, y = TEXT_FIELDS[key]
        c.drawString(x, y, str(data.get(key, "")))

    # Section comments (page 1 only)
    for section_id, (page, x, y) in SECTION_COMMENTS.items():
        if page != 1:
            continue
        _draw_multiline(c, section_comments.get(section_id, ""), x, y, max_x=810)

    c.save()
    buf.seek(0)
    return buf.read()


def _build_page2_overlay(data: dict) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    scores = data["scores"]
    section_comments = data.get("section_comments", {})

    _draw_score_circles(c, scores, PAGE2_SCORES)

    # Section comments (page 2 only)
    for section_id, (page, x, y) in SECTION_COMMENTS.items():
        if page != 2:
            continue
        _draw_multiline(c, section_comments.get(section_id, ""), x, y, max_x=810)

    # Feedback text blocks
    for key in ("strengths", "improvements"):
        _, x, y = TEXT_FIELDS[key]
        _draw_multiline(c, data.get(key, ""), x, y, max_x=810, max_lines=3)
    _, x, y = TEXT_FIELDS["general_comments"]
    _draw_multiline(c, data.get("general_comments", ""), x, y, max_x=701, max_lines=3)

    # Total score
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(black)
    _, tx, ty = TEXT_FIELDS["total_score"]
    c.drawCentredString(tx, ty, str(data.get("total_score", 0)))

    # Pass / Fail circle
    c.setLineWidth(1.5)
    c.setFillColor(black)
    if data.get("passed"):
        c.setStrokeColor(HexColor("#16a34a"))
        _, x, y = TEXT_FIELDS["pass_yes"]
    else:
        c.setStrokeColor(HexColor("#dc2626"))
        _, x, y = TEXT_FIELDS["pass_no"]
    c.circle(x, y, 12, stroke=1, fill=0)

    # Assessor name & role
    c.setFont("Helvetica", 10)
    c.setFillColor(black)
    _, x, y = TEXT_FIELDS["assessor_name"]
    c.drawString(x, y, data.get("assessor_name", ""))
    _, x, y = TEXT_FIELDS["assessor_role"]
    c.drawString(x, y, data.get("assessor_role", ""))

    # Signatures
    assessor_sig = data.get("assessor_signature", "")
    if assessor_sig:
        _draw_signature(c, assessor_sig, ASSESSOR_SIG_BOX)

    candidate_sig = data.get("cadet_signature", "")
    if candidate_sig:
        _draw_signature(c, candidate_sig, CANDIDATE_SIG_BOX)

    c.save()
    buf.seek(0)
    return buf.read()


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_moi_pdf(data: dict) -> bytes:
    p1_overlay = _build_page1_overlay(data)
    p2_overlay = _build_page2_overlay(data)

    try:
        template = PdfReader(TEMPLATE_PATH)
        ov1 = PdfReader(io.BytesIO(p1_overlay))
        ov2 = PdfReader(io.BytesIO(p2_overlay))

        writer = PdfWriter()

        page1 = template.pages[0]
        page1.merge_page(ov1.pages[0])
        writer.add_page(page1)

        page2 = template.pages[1]
        page2.merge_page(ov2.pages[0])
        writer.add_page(page2)

        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as e:
        print(f"[PDF] Template merge error: {e}")
        return p1_overlay


def process_assessment_data(payload: dict) -> dict:
    scores = payload.get("scores", {})
    clean_scores = {int(k): v for k, v in scores.items() if v is not None}

    total_score = sum(clean_scores.values())
    has_a_one = any(v == 1 for v in clean_scores.values())
    all_answered = len(clean_scores) == 13
    passed = all_answered and total_score >= 35 and not has_a_one

    raw_date = payload.get("date", "")
    try:
        date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%d/%m/%y")
    except (ValueError, TypeError):
        date = raw_date

    return {
        "cadet_surname":       payload.get("cadet_surname", ""),
        "cadet_forename":      payload.get("cadet_forename", ""),
        "sqn_df":              payload.get("sqn_df", ""),
        "wing_ccf":            payload.get("wing_ccf", ""),
        "date":                date,
        "bader_reference":     payload.get("bader_reference", ""),
        "place_of_assessment": payload.get("place_of_assessment", ""),
        "scores":              clean_scores,
        "section_comments":    payload.get("section_comments", {}),
        "strengths":           payload.get("strengths_summary", ""),
        "improvements":        payload.get("improvements_summary", ""),
        "general_comments":    payload.get("general_comments", ""),
        "total_score":         total_score,
        "passed":              passed,
        "assessor_name":       payload.get("assessor_name", ""),
        "assessor_role":       payload.get("assessor_role", ""),
        "assessor_signature":  payload.get("assessor_signature"),
        "cadet_signature":     payload.get("cadet_signature"),
    }
