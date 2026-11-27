import base64
import io
import logging

from PIL import Image as PILImage
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, StreamObject
from reportlab.lib.colors import black
from reportlab.lib.utils import ImageReader

logger = logging.getLogger(__name__)


def _flatten_widgets(writer: PdfWriter, page) -> None:
    """Paint each form-field widget's appearance into the page, then drop the
    widget. pypdf's own flatten drops appearances it can't regenerate, which
    would blank the entry; this draws the stored appearance stream as-is."""
    annots = page.get("/Annots")
    if annots is None:
        return
    resources = page.setdefault(NameObject("/Resources"), DictionaryObject())
    resources = resources.get_object()
    xobjs = resources.setdefault(NameObject("/XObject"), DictionaryObject()).get_object()
    ops, keep = [], ArrayObject()
    for n, ref in enumerate(annots.get_object()):
        annot = ref.get_object()
        if annot.get("/Subtype") != "/Widget":
            keep.append(ref)
            continue
        ap = annot.get("/AP")
        normal = ap.get_object().get("/N") if ap is not None else None
        normal = normal.get_object() if normal is not None else None
        if normal is None:
            continue
        if not isinstance(normal, StreamObject):
            # Checkbox/radio: /N maps state names to streams.
            normal = normal.get(annot.get("/AS"))
            normal = normal.get_object() if normal is not None else None
            if normal is None:
                continue
        if int(annot.get("/F", 0)) & 2:  # hidden
            continue
        rect = [float(v) for v in annot["/Rect"]]
        bbox = [float(v) for v in normal.get("/BBox", [0, 0, rect[2] - rect[0], rect[3] - rect[1]])]
        bw, bh = (bbox[2] - bbox[0]) or 1, (bbox[3] - bbox[1]) or 1
        sx, sy = (rect[2] - rect[0]) / bw, (rect[3] - rect[1]) / bh
        name = NameObject(f"/FlatW{n}")
        xobjs[name] = writer._add_object(normal)
        ops.append(f"q {sx:.4f} 0 0 {sy:.4f} {rect[0] - bbox[0] * sx:.4f} {rect[1] - bbox[1] * sy:.4f} cm {name} Do Q")
    if ops:
        stream = StreamObject()
        stream.set_data("\n".join(ops).encode())
        existing = page.get("/Contents")
        contents = ArrayObject()
        if existing is not None:
            e = existing.get_object()
            contents.extend(e if isinstance(e, list) else [existing])
        contents.append(writer._add_object(stream))
        page[NameObject("/Contents")] = contents
    page[NameObject("/Annots")] = keep


def merge_pdfs(pdf_blobs: list[bytes | None]) -> bytes:
    """Concatenate PDF byte blobs (in order) into a single PDF. None/empty entries are skipped.

    An unreadable blob (a lesson plan that wasn't really a PDF) is skipped with a
    log line rather than raised: it is stored against the cadet, so raising would
    break their combined record on every download, not just once."""
    writer = PdfWriter()
    for i, blob in enumerate(pdf_blobs):
        if not blob:
            continue
        try:
            reader = PdfReader(io.BytesIO(blob))
            # Fillable lesson plans: add_page() leaves the field widgets orphaned
            # (no AcroForm), so typed-in entries can render blank; append() keeps
            # them live but same-named fields across two sheets then share one
            # value. Bake each plan's entries into its pages instead, so every
            # sheet keeps exactly what was typed into it.
            part = PdfWriter()
            part.append(reader, import_outline=False)
            for page in part.pages:
                _flatten_widgets(part, page)
            for page in part.pages:
                writer.add_page(page)
        except Exception as e:
            logger.warning(f"merge_pdfs: skipping unreadable PDF #{i}: {e}")
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
