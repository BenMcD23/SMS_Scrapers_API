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
TEMPLATE_PATH = str(Path(__file__).parent.parent / "assessment_sheets" / "Blue_Radio.pdf")
PAGE_W, PAGE_H = 595.28, 841.89

# X position of the "Initial" column
INITIAL_X = 522

# Y positions for each criterion row in the "Initial" column
# (reportlab coords: 0 = bottom of page)
CRITERIA_Y = {
    "callsigns":            314,
    "auth_1a":              296,
    "auth_1b":              279,
    "radio_2a":             262,
    "radio_2b":             246,
    "tactical_3":           228,
    "say_again_4":          212,
    "say_again_5":          195,
    "prowords":             178,
    "verbal_understanding": 157,
    "verbal_security":      128,
    "cyber_video":          106,
}

TEXT_FIELDS = {
    "cadet_surname":    (152, 772),
    "forename":         (346, 772),
    "rank":             (463, 772),
    # sqn and wing are fixed — 317 and GM respectively
    "sqn":              (95,  742),
    "wing":             (325, 742),
    "cyber_sec_date":   (378, 105),
    "pass_circle":      (84,  91),    # centre of circle around "PASS"
    "fail_circle":      (114, 91),    # centre of circle around "FAIL"
    "comments":         (204, 91),
    "assessor_name":    (200, 64),
    "assessor_sig":     (346, 64),
    "date":             (446, 66),
}

# Radius of the pass/fail circle
PASS_FAIL_CIRCLE_R = 10
PASS_FAIL_ELLIPSE_RX = 12

def _get_initials(name: str) -> str:
    """Extract initials from a full name, e.g. 'John Smith' -> 'JS'."""
    parts = name.strip().split()
    return "".join(p[0].upper() for p in parts if p)[:3]


def _build_overlay(
    cadet_surname: str,
    forename: str,
    rank: str,
    criteria: dict[str, bool],
    cyber_sec_date: str,
    passed: bool,
    comments: str,
    assessor_name: str,
    assessor_signature: str,  # base64 data URL or plain text
    date: str,
    assessor_initials: str = "",
) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    initials = assessor_initials or _get_initials(assessor_name)

    # -- 1. Header fields --
    c.setFont("Helvetica", 10)
    c.setFillColor(black)
    c.drawString(*TEXT_FIELDS["cadet_surname"], cadet_surname)
    c.drawString(*TEXT_FIELDS["forename"], forename)
    c.drawString(*TEXT_FIELDS["rank"], rank)
    c.drawString(*TEXT_FIELDS["sqn"], "317")
    c.drawString(*TEXT_FIELDS["wing"], "GM")

    # -- 2. Initials for each criterion --
    c.setFont("Helvetica-Bold", 9)
    for key, checked in criteria.items():
        if checked and key in CRITERIA_Y:
            c.drawCentredString(INITIAL_X, CRITERIA_Y[key], initials)
    if cyber_sec_date:
        c.drawCentredString(INITIAL_X, CRITERIA_Y["cyber_video"], initials)

    # -- 3. Cyber security video date --
    if cyber_sec_date:
        c.setFont("Helvetica", 9)
        c.drawString(*TEXT_FIELDS["cyber_sec_date"], cyber_sec_date)

    # -- 4. Pass / Fail circle --
    c.setLineWidth(1.5)
    if passed:
        c.setStrokeColor(HexColor("#16a34a"))
        cx, cy = TEXT_FIELDS["pass_circle"]
        c.ellipse(cx - PASS_FAIL_ELLIPSE_RX, cy - PASS_FAIL_CIRCLE_R,
                cx + PASS_FAIL_ELLIPSE_RX, cy + PASS_FAIL_CIRCLE_R,
                stroke=1, fill=0)
    else:
        c.setStrokeColor(HexColor("#dc2626"))
        cx, cy = TEXT_FIELDS["fail_circle"]
        c.ellipse(cx - PASS_FAIL_ELLIPSE_RX, cy - PASS_FAIL_CIRCLE_R,
                cx + PASS_FAIL_ELLIPSE_RX, cy + PASS_FAIL_CIRCLE_R,
                stroke=1, fill=0)

    # -- 5. Comments --
    if comments:
        from reportlab.lib.utils import simpleSplit
        # Comments field starts at x=204; wrap at x=544 → available width = 340 pts
        lines = simpleSplit(comments, "Helvetica", 9, 340)
        x, y = TEXT_FIELDS["comments"]
        for line in lines:
            c.setFont("Helvetica", 9)
            c.setFillColor(black)
            c.drawString(x, y, line)
            y -= 11

    # -- 6. Assessor name and date --
    c.setFont("Helvetica", 10)
    c.setFillColor(black)
    c.drawString(*TEXT_FIELDS["assessor_name"], assessor_name)
    c.drawString(*TEXT_FIELDS["date"], date)

    # -- 7. Signature: image or plain text --
    sig = assessor_signature
    if sig and sig.startswith("data:image"):
        try:
            _header, b64data = sig.split(",", 1)
            img_bytes = base64.b64decode(b64data)

            pil_img = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA")
            bbox = pil_img.getbbox()
            if bbox:
                pil_img = pil_img.crop(bbox)
            img_w, img_h = pil_img.size

            cropped_buf = io.BytesIO()
            pil_img.save(cropped_buf, format="PNG")
            cropped_buf.seek(0)

            BOX_X1, BOX_Y1 = 340, 58
            BOX_X2, BOX_Y2 = 417, 78
            box_w = BOX_X2 - BOX_X1        # 69
            box_h = BOX_Y2 - BOX_Y1        # 13

            aspect = img_w / img_h
            draw_w = box_w
            draw_h = draw_w / aspect
            if draw_h > box_h:
                draw_h = box_h
                draw_w = draw_h * aspect

            draw_x = BOX_X1 + (box_w - draw_w) / 2
            draw_y = BOX_Y1 + (box_h - draw_h) / 2

            img_reader = ImageReader(cropped_buf)
            c.drawImage(
                img_reader,
                draw_x, draw_y,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=False,
                mask="auto",
            )
        except Exception as e:
            print(f"[PDF] Signature image error: {e}")
            c.setFont("Helvetica", 10)
            c.drawString(346, 67, "[signature error]")
    elif sig:
        c.setFont("Helvetica", 10)
        c.drawString(346, 67, sig)

    c.save()
    buf.seek(0)
    return buf.read()


def generate_radio_pdf(data: dict) -> bytes:
    overlay_bytes = _build_overlay(
        cadet_surname=data.get("cadet_surname", ""),
        forename=data.get("forename", ""),
        rank=data.get("rank", ""),
        criteria=data.get("criteria", {}),
        cyber_sec_date=data.get("cyber_sec_date", ""),
        passed=data.get("passed", False),
        comments=data.get("comments", ""),
        assessor_name=data.get("assessor_name", ""),
        assessor_signature=data.get("assessor_signature", ""),
        date=data.get("date", ""),
        assessor_initials=data.get("assessor_initials", ""),
    )

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
    except Exception:
        return overlay_bytes


def process_radio_data(payload: dict, cadet) -> dict:
    """Process raw API payload into PDF builder format."""
    raw_date = payload.get("date", "")
    try:
        date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%d/%m/%y")
    except (ValueError, TypeError):
        date = raw_date

    cyber_raw = payload.get("cyber_sec_date", "")
    try:
        cyber_sec_date = datetime.strptime(cyber_raw, "%Y-%m-%d").strftime("%d/%m/%y")
    except (ValueError, TypeError):
        cyber_sec_date = cyber_raw

    surname = (cadet.last_name or "").upper()
    forename = cadet.first_name or ""
    rank = cadet.rank or ""

    return {
        "cadet_surname": surname,
        "forename": forename,
        "rank": rank,
        "criteria": payload.get("criteria", {}),
        "cyber_sec_date": cyber_sec_date,
        "passed": payload.get("passed", False),
        "comments": payload.get("comments", ""),
        "assessor_name": payload.get("assessor_name", ""),
        "assessor_initials": payload.get("assessor_initials", ""),
        "assessor_signature": payload.get("assessor_signature"),
        "date": date,
    }
