import base64
import io

from pypdf import PdfReader, PdfWriter


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
