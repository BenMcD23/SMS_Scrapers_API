"""Website photo gallery — photos and their list live in the cadet website repo.

`src/data/gallery.json` is the list (section, file, size, alt text); the
images sit in `src/assets/gallery/`. The site groups photos into sections in
the order each section first appears, so a new section is just a new name.
"""

import base64
import io
import json
import re
import unicodedata

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError

from core.config import GITHUB_BRANCH, GITHUB_REPO
from core.github import commit_files_to_github, github_headers
from core.security import require_staff

router = APIRouter()

GALLERY_JSON_PATH = "src/data/gallery.json"
GALLERY_DIR = "src/assets/gallery"
# Matches the existing photos: longest side 1100px.
MAX_SIDE = 1100
MAX_SECTION_LEN = 40


async def fetch_gallery(client: httpx.AsyncClient) -> list[dict]:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GALLERY_JSON_PATH}"
    resp = await client.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=f"Could not fetch {GALLERY_JSON_PATH} from {GITHUB_REPO}@{GITHUB_BRANCH} "
            f"(GitHub returned {resp.status_code}).",
        )
    return json.loads(base64.b64decode(resp.json()["content"]).decode("utf-8"))


def gallery_json_bytes(photos: list[dict]) -> bytes:
    return (json.dumps(photos, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "photo"


def next_number(photos: list[dict]) -> int:
    """Files are named `<section>-<n>.webp` with n unique across the gallery."""
    nums = [int(m.group(1)) for p in photos if (m := re.search(r"-(\d+)\.webp$", p.get("file", "")))]
    return max(nums, default=-1) + 1


def to_webp(data: bytes) -> tuple[bytes, int, int]:
    """Phone photo → upright webp no bigger than MAX_SIDE. Raises 400 if not an image."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise HTTPException(status_code=400, detail="One of the files isn't an image we can read (use JPEG, PNG or WebP).")
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue(), img.width, img.height


@router.get("/gallery")
async def list_gallery(idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await fetch_gallery(client)


@router.post("/gallery")
async def add_photos(
    section: str = Form(...),
    alts: list[str] = Form(...),
    files: list[UploadFile] = File(...),
    idinfo: dict = Depends(require_staff),
):
    section = " ".join(section.split())
    if not section or len(section) > MAX_SECTION_LEN:
        raise HTTPException(status_code=400, detail=f"Section name must be 1–{MAX_SECTION_LEN} characters.")
    alts = [a.strip() for a in alts]
    if len(alts) != len(files):
        raise HTTPException(status_code=400, detail="Each photo needs a description.")
    if not all(alts):
        raise HTTPException(status_code=400, detail="Every photo needs a description (it's read out to blind visitors).")

    # Convert everything before touching GitHub, so one bad file adds nothing.
    # Pillow is CPU-bound: off the event loop, or every other request waits.
    converted = [await run_in_threadpool(to_webp, await f.read()) for f in files]

    async with httpx.AsyncClient(timeout=120.0) as client:
        photos = await fetch_gallery(client)
        # Reuse an existing section's exact spelling, ignoring case.
        section = next((p["cat"] for p in photos if p["cat"].lower() == section.lower()), section)
        n = next_number(photos)
        added, blobs = [], []
        for i, ((webp, w, h), alt) in enumerate(zip(converted, alts, strict=True)):
            name = f"{slug(section)}-{n + i}.webp"
            added.append({"cat": section, "file": name, "w": w, "h": h, "alt": alt})
            blobs.append({"path": f"{GALLERY_DIR}/{name}", "content": webp})

        # New photos go after the last one already in their section, so
        # sections stay together and keep their order on the page.
        last = max((i for i, p in enumerate(photos) if p["cat"] == section), default=len(photos) - 1)
        photos[last + 1:last + 1] = added

        await commit_files_to_github(
            client,
            GITHUB_REPO,
            GITHUB_BRANCH,
            f"Add {len(added)} gallery photo{'s' if len(added) != 1 else ''} to {section}",
            files=[{"path": GALLERY_JSON_PATH, "content": gallery_json_bytes(photos)}, *blobs],
        )

    return {"status": "success", "section": section, "added": added}


@router.delete("/gallery/{file}")
async def delete_photo(file: str, idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=120.0) as client:
        photos = await fetch_gallery(client)
        if not any(p.get("file") == file for p in photos):
            raise HTTPException(status_code=404, detail=f"Photo {file} not found")
        remaining = [p for p in photos if p.get("file") != file]
        await commit_files_to_github(
            client,
            GITHUB_REPO,
            GITHUB_BRANCH,
            f"Remove gallery photo {file}",
            files=[{"path": GALLERY_JSON_PATH, "content": gallery_json_bytes(remaining)}],
            delete_paths=[f"{GALLERY_DIR}/{file}"],
        )
    return {"status": "success", "file": file}
