"""Monthly programme updater — pulls the PDF from Drive and commits it to the website repo."""

import base64
import io
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pdf2image import convert_from_bytes

from core.config import GITHUB_REPO, GITHUB_BRANCH, PROGRAMME_APPS_SCRIPT_URL
from core.github import github_headers, commit_files_to_github
from core.security import require_staff

router = APIRouter()


async def list_old_programme_pdfs(client: httpx.AsyncClient, exclude_filename: str) -> list[str]:
    """Return repo paths of existing programme PDFs other than `exclude_filename`."""
    folder_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/public/programme"
    resp = await client.get(folder_url, headers=github_headers(), params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200 or not isinstance(resp.json(), list):
        return []

    return [
        f"public/programme/{f['name']}"
        for f in resp.json()
        if f.get("name", "").endswith("_programme.pdf") and f["name"] != exclude_filename
    ]


@router.post("/update-programme")
async def update_programme(
    month: int = None,
    year: int = None,
    idinfo: dict = Depends(require_staff),
):
    now = datetime.now()
    month = month or now.month
    year = year or now.year
    month_str = str(month).zfill(2)
    short_year = str(year)[-2:]
    pdf_filename = f"{month_str}_{short_year}_programme.pdf"

    # Fetch PDF URL from Apps Script, then download the actual PDF
    async with httpx.AsyncClient() as client:
        script_url = f"{PROGRAMME_APPS_SCRIPT_URL}?month={month}&year={year}"
        script_resp = await client.get(script_url, timeout=60, follow_redirects=True)

        if script_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to reach Apps Script")

        data = script_resp.json()
        if "error" in data:
            raise HTTPException(status_code=502, detail=f"Apps Script error: {data['error']}")

        pdf_resp = await client.get(data["downloadUrl"], timeout=30, follow_redirects=True)
        if pdf_resp.status_code != 200 or pdf_resp.content[:4] != b'%PDF':
            raise HTTPException(status_code=502, detail="Failed to download PDF from Drive")

        pdf_bytes = pdf_resp.content

    # Convert PDF pages to webp
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=200, fmt="webp")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF conversion failed: {str(e)}")

    if len(pages) < 1:
        raise HTTPException(status_code=500, detail="PDF has no pages")

    def page_to_bytes(page) -> bytes:
        buf = io.BytesIO()
        page.save(buf, format="WEBP")
        return buf.getvalue()

    page1_bytes = page_to_bytes(pages[0])
    page2_bytes = page_to_bytes(pages[1]) if len(pages) > 1 else page1_bytes

    jsx_path = "src/pages/programme.jsx"
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Update Programme.jsx to point at the new PDF filename
        jsx_api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{jsx_path}"
        jsx_get = await client.get(jsx_api_url, headers=github_headers(), params={"ref": GITHUB_BRANCH})
        if jsx_get.status_code != 200:
            raise HTTPException(status_code=500, detail="Could not fetch Programme.jsx from GitHub")

        jsx_content = base64.b64decode(jsx_get.json()["content"]).decode("utf-8")
        updated_jsx = re.sub(
            r'/programme/\d{2}_\d{2}_programme\.pdf',
            f'/programme/{pdf_filename}',
            jsx_content,
        )

        old_pdf_paths = await list_old_programme_pdfs(client, exclude_filename=pdf_filename)

        # JSX update, both webp pages, and the new PDF in a single commit
        await commit_files_to_github(
            client,
            GITHUB_REPO,
            GITHUB_BRANCH,
            f"Update programme to {pdf_filename}",
            files=[
                {"path": jsx_path, "content": updated_jsx.encode("utf-8")},
                {"path": "src/assets/programme/programme.webp", "content": page1_bytes},
                {"path": "src/assets/programme/rooms.webp", "content": page2_bytes},
                {"path": f"public/programme/{pdf_filename}", "content": pdf_bytes},
            ],
            delete_paths=old_pdf_paths,
        )

    return {
        "status": "success",
        "message": f"Programme updated for {month_str}/{short_year}",
        "pdf": pdf_filename,
        "pages_converted": len(pages),
    }
