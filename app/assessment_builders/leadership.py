import io
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import HexColor, black
from pypdf import PdfReader, PdfWriter
import base64
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage
from datetime import datetime

# --- Configuration ---
TEMPLATE_PATH = "assessment_sheets/Blue_Leadership.pdf"
PAGE_W, PAGE_H = 595.28, 841.89
CIRCLE_RADIUS = 10 

SCORE_POSITIONS = {
    1:  {1: (354, 633), 2: (398, 633), 3: (426, 633), 4: (454, 633), 5: (500, 633)},
    2:  {1: (354, 604), 2: (398, 604), 3: (426, 604), 4: (454, 604), 5: (500, 604)},
    3:  {1: (354, 575), 2: (398, 575), 3: (426, 575), 4: (454, 575), 5: (500, 575)},
    4:  {1: (354, 547), 2: (398, 547), 3: (426, 547), 4: (454, 547), 5: (500, 547)},
    5:  {1: (354, 518), 2: (398, 518), 3: (426, 518), 4: (454, 518), 5: (500, 518)},
    6:  {1: (354, 490), 2: (398, 490), 3: (426, 490), 4: (454, 490), 5: (500, 490)},
    7:  {1: (354, 461), 2: (398, 461), 3: (426, 461), 4: (454, 461), 5: (500, 461)},
    8:  {1: (354, 433), 2: (398, 433), 3: (426, 433), 4: (454, 433), 5: (500, 433)},
    9:  {1: (354, 404), 2: (398, 404), 3: (426, 404), 4: (454, 404), 5: (500, 404)},
    10: {1: (354, 376), 2: (398, 376), 3: (426, 376), 4: (454, 376), 5: (500, 376)},
}

TEXT_FIELDS = {
    "cadet_name":         (270, 732),
    "exercise_no":        (157, 690),
    "exercise_name":      (298, 690),
    "total_score_columns": [
        (354, 352), # Col for score 1
        (398, 352), # Col for score 2
        (426, 352), # Col for score 3
        (454, 352), # Col for score 4
        (500, 352), # Col for score 5
    ],
    "total_score":        (428, 325),
    "pass_yes":           (395, 267),
    "pass_no":            (502, 267),
    "assessor_name":      (88, 200),
    "assessor_signature": (222, 200),
    "date":               (406, 200),
    "debrief_notes":      (75, 152),
}

def _build_overlay(
    scores: dict[int, int],
    cadet_name: str,
    exercise_no: str,
    exercise_name: str,
    total_score: int,
    passed: bool,
    assessor_name: str,
    date: str,
    debriefing_notes: str,
    assessor_signature_text: str,  # may be a base64 data URL or plain text
) -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))

    # -- 1. Hollow Circles & Column Calculation --
    c.setLineWidth(1.5)
    col_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    
    for q_id, score in scores.items():
        q_id = int(q_id)
        score = int(score)
        if q_id in SCORE_POSITIONS and score in SCORE_POSITIONS[q_id]:
            x, y = SCORE_POSITIONS[q_id][score]
            col_counts[score] += 1
            
            stroke_color = HexColor("#ef4444") if score == 1 else HexColor("#22c55e") if score == 5 else HexColor("#3b82f6")
            c.setStrokeColor(stroke_color)
            c.circle(x, y, CIRCLE_RADIUS, stroke=1, fill=0)

    # -- 2. Draw Column Totals --
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 12)
    for i, score_val in enumerate(range(1, 6)):
        count = col_counts[score_val]
        total = count * score_val
        x, y = TEXT_FIELDS["total_score_columns"][i]
        c.drawCentredString(x, y, str(total))

    # -- 3. Text Fields --
    c.setFont("Helvetica", 10)
    c.drawString(*TEXT_FIELDS["cadet_name"], cadet_name)
    c.drawString(*TEXT_FIELDS["exercise_no"], exercise_no)
    c.drawString(*TEXT_FIELDS["exercise_name"], exercise_name)
    c.drawString(*TEXT_FIELDS["assessor_name"], assessor_name)
    c.drawString(*TEXT_FIELDS["date"], date)

    # -- 4. Signature: image if base64 data URL, otherwise plain text --
    sig = assessor_signature_text
    if sig and sig.startswith("data:image"):
        try:
            header, b64data = sig.split(",", 1)
            img_bytes = base64.b64decode(b64data)

            # Crop transparent padding for drawn signatures
            pil_img = PILImage.open(io.BytesIO(img_bytes)).convert("RGBA")
            bbox = pil_img.getbbox()
            if bbox:
                pil_img = pil_img.crop(bbox)
            img_w, img_h = pil_img.size

            # Save cropped image back to bytes
            cropped_buf = io.BytesIO()
            pil_img.save(cropped_buf, format="PNG")
            cropped_buf.seek(0)

            # Box coordinates (from template measurement)
            BOX_X1, BOX_Y1 = 182, 191
            BOX_X2, BOX_Y2 = 355, 217
            box_w = BOX_X2 - BOX_X1  # 173
            box_h = BOX_Y2 - BOX_Y1  # 26

            # Scale to fit width, preserve aspect ratio, cap to box height
            aspect = img_w / img_h
            draw_w = box_w
            draw_h = draw_w / aspect
            if draw_h > box_h:
                draw_h = box_h
                draw_w = draw_h * aspect

            # Centre vertically within the box
            draw_x = BOX_X1 + (box_w - draw_w) / 2
            draw_y = BOX_Y1 + (box_h - draw_h) / 2

            img_reader = ImageReader(cropped_buf)
            c.drawImage(
                img_reader,
                draw_x, draw_y,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=False,  # we've already calculated it
                mask="auto",
            )
        except Exception as e:
            print(f"[PDF] Signature image error: {e}")
            c.setFont("Helvetica", 10)
            c.drawString(182, 200, "[signature error]")
    elif sig:
        c.setFont("Helvetica", 10)
        c.drawString(182, 200, sig)

    # -- 5. Overall Total Score --
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(TEXT_FIELDS["total_score"][0], TEXT_FIELDS["total_score"][1], str(total_score))

    # -- 6. Pass/Fail Ticks --
    c.setFont("Helvetica-Bold", 14)
    if passed:
        c.setFillColor(HexColor("#16a34a"))
        c.drawString(TEXT_FIELDS["pass_yes"][0], TEXT_FIELDS["pass_yes"][1], "✓")
    else:
        c.setFillColor(HexColor("#dc2626"))
        c.drawString(TEXT_FIELDS["pass_no"][0], TEXT_FIELDS["pass_no"][1], "✓")

    # -- 7. Multiline Debrief --
    if debriefing_notes:
        from reportlab.lib.utils import simpleSplit
        lines = simpleSplit(debriefing_notes, "Helvetica", 9, 450)
        x, y = TEXT_FIELDS["debrief_notes"]
        for line in lines:
            c.setFont("Helvetica", 9)
            c.setFillColor(black)
            c.drawString(x, y, line)
            y -= 12

    c.save()
    buf.seek(0)
    return buf.read()

def generate_leadership_pdf(data: dict) -> bytes:
    overlay_bytes = _build_overlay(
        scores=data.get("scores", {}),
        cadet_name=data.get("cadet_name", ""),
        exercise_no=data.get("exercise_no", ""),
        exercise_name=data.get("exercise_name", ""),
        total_score=data.get("total_score", 0),
        passed=data.get("passed", True),
        assessor_name=data.get("assessor_name", ""),
        date=data.get("date", ""),
        debriefing_notes=data.get("debriefing_notes", ""),
        assessor_signature_text=data.get("assessor_signature", "")
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

def process_assessment_data(payload: dict) -> dict:
    """
    Processes raw API payload to calculate totals and pass/fail status.
    Ensures backend logic matches the UI requirements.
    """
    scores = payload.get("scores", {})
    
    # 1. Convert score keys to ints if they are strings (common in JSON)
    # and filter out any None values
    clean_scores = {int(k): v for k, v in scores.items() if v is not None}
    
    # 2. Calculate Total Score
    total_score = sum(clean_scores.values())
    
    # 3. Determine Pass/Fail Status
    # Rule A: Must have all 10 questions answered
    # Rule B: Total score must be 30 or above
    # Rule C: Automatic fail if any single score is a 1
    has_a_one = any(v == 1 for v in clean_scores.values())
    all_answered = len(clean_scores) == 10
    
    passed = all_answered and total_score >= 30 and not has_a_one
    
    raw_date = payload.get("date", "")
    try:
        date = datetime.strptime(raw_date, "%Y-%m-%d").strftime("%d/%m/%y")
    except (ValueError, TypeError):
        date = raw_date  # fallback to whatever was sent if parsing fails
        
    # 4. Map back to the expected PDF builder format
    return {
        "cadet_name": payload.get("cadet_name", "Unknown"),
        "exercise_no": payload.get("exercise_no", ""),
        "exercise_name": payload.get("exercise_name", ""),
        "scores": clean_scores,
        "total_score": total_score,
        "passed": passed,
        "assessor_name": payload.get("assessor_name", ""),
        "assessor_signature": payload.get("assessor_signature"),
        "date": date,
        "debriefing_notes": payload.get("debriefing_notes", ""),
    }
