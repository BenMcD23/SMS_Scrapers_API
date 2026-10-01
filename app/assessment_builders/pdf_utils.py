import base64
import io
import logging

from PIL import Image as PILImage
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import black
from reportlab.lib.utils import ImageReader

logger = logging.getLogger(__name__)


def merge_pdfs(pdf_blobs: list[bytes | None]) -> bytes:
    """Concatenate PDF byte blobs (in order) into a single PDF. None/empty entries are skipped."""
    writer = PdfWriter()
    for blob in pdf_blobs:
        if not blob:
            continue
        reader = PdfReader(io.BytesIO(blob))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def decode_pdf_data_url(value: str | None) -> bytes | None:
    """Decode a `data:application/pdf;base64,...` string into raw PDF bytes."""
    if not value:
        return None
    if "," in value and value.strip().lower().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value)
    except Exception:
        return None


def draw_multiline(c, text: str, x: float, y: float,
                    max_x: float = 810, max_lines: int = 4, font_size: int = 9):
    """Word-wrap text between x and max_x, dropping anything past max_lines so
    long input never spills over the printed form."""
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


def draw_signature(c, sig: str, box: tuple):
    """Draw a base64 image signature (or plain text) into a bounding box,
    trimmed and scaled to fit. Shared by every builder with a signature line."""
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
            logger.error(f"Signature error: {e}")
            c.setFont("Helvetica", 9)
            c.drawString(x1, y1 + 5, "[signature error]")
    elif sig:
        c.setFont("Helvetica", 9)
        c.drawString(x1, y1 + 5, sig)
