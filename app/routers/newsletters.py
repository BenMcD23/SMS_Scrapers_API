"""Newsletter management — PDFs and metadata live in the newsletter repo on GitHub."""

import base64
import json

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.config import NEWSLETTER_REPO, NEWSLETTER_BRANCH, NEWSLETTER_JSON_PATH
from core.github import github_headers, commit_files_to_github
from core.security import require_staff

router = APIRouter()


def _newsletter_pdf_path(issue: int) -> str:
    return f"317_newsletter/public/newsletters/issue-{issue}.pdf"


async def fetch_newsletters_json(client: httpx.AsyncClient) -> list[dict]:
    url = f"https://api.github.com/repos/{NEWSLETTER_REPO}/contents/{NEWSLETTER_JSON_PATH}"
    resp = await client.get(url, headers=github_headers(), params={"ref": NEWSLETTER_BRANCH})
    if resp.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not fetch {NEWSLETTER_JSON_PATH} from {NEWSLETTER_REPO}@{NEWSLETTER_BRANCH} "
                f"(GitHub returned {resp.status_code}). Make sure the file is pushed to that branch."
            ),
        )
    return json.loads(base64.b64decode(resp.json()["content"]).decode("utf-8"))


def newsletters_json_bytes(newsletters: list[dict]) -> bytes:
    """Serialise newsletters sorted by issue (descending) to JSON bytes.

    The first entry is the homepage 'current' issue, so the highest issue
    number is always written first.
    """
    ordered = sorted(newsletters, key=lambda n: n.get("issue", 0), reverse=True)
    return (json.dumps(ordered, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


@router.get("/newsletters")
async def list_newsletters(idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=60.0) as client:
        newsletters = await fetch_newsletters_json(client)
    return sorted(newsletters, key=lambda n: n.get("issue", 0), reverse=True)


@router.post("/upload-newsletter")
async def upload_newsletter(
    file: UploadFile = File(...),
    title: str = Form(...),
    date: str = Form(...),
    issue: int = Form(...),
    description: str = Form(...),
    cover_color: str = Form("#1F2E4A"),
    idinfo: dict = Depends(require_staff),
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF")

    pdf_bytes = await file.read()
    newsletter_id = f"issue-{issue}"

    entry = {
        "id": newsletter_id,
        "title": title,
        "date": date,
        "issue": issue,
        "description": description,
        "pdfPath": f"/newsletters/issue-{issue}.pdf",
        "coverColor": cover_color,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        newsletters = await fetch_newsletters_json(client)
        # Reject duplicate issue numbers — editing an existing issue goes via PUT
        if any(n.get("issue") == issue for n in newsletters):
            raise HTTPException(
                status_code=409,
                detail=f"Issue {issue} already exists. Edit that newsletter or choose a different issue number.",
            )
        newsletters.append(entry)

        await commit_files_to_github(
            client,
            NEWSLETTER_REPO,
            NEWSLETTER_BRANCH,
            f"Add newsletter {newsletter_id}: {title}",
            files=[
                {"path": NEWSLETTER_JSON_PATH, "content": newsletters_json_bytes(newsletters)},
                {"path": _newsletter_pdf_path(issue), "content": pdf_bytes},
            ],
        )

    return {"status": "success", "id": newsletter_id, "filename": f"issue-{issue}.pdf"}


@router.put("/newsletters/{issue}")
async def update_newsletter(
    issue: int,
    title: str = Form(None),
    date: str = Form(None),
    description: str = Form(None),
    cover_color: str = Form(None),
    file: UploadFile = File(None),
    idinfo: dict = Depends(require_staff),
):
    async with httpx.AsyncClient(timeout=120.0) as client:
        newsletters = await fetch_newsletters_json(client)
        entry = next((n for n in newsletters if n.get("issue") == issue), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Newsletter issue {issue} not found")

        # Apply only the metadata fields that were provided
        if title is not None:
            entry["title"] = title
        if date is not None:
            entry["date"] = date
        if description is not None:
            entry["description"] = description
        if cover_color is not None:
            entry["coverColor"] = cover_color

        files = [{"path": NEWSLETTER_JSON_PATH, "content": newsletters_json_bytes(newsletters)}]

        # Optionally replace the PDF (overwrites issue-{issue}.pdf)
        if file is not None and file.filename:
            if file.content_type != "application/pdf":
                raise HTTPException(status_code=400, detail="File must be a PDF")
            files.append({"path": _newsletter_pdf_path(issue), "content": await file.read()})

        await commit_files_to_github(
            client,
            NEWSLETTER_REPO,
            NEWSLETTER_BRANCH,
            f"Update newsletter issue-{issue}",
            files=files,
        )

    return {"status": "success", "id": f"issue-{issue}"}


@router.delete("/newsletters/{issue}")
async def delete_newsletter(issue: int, idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=120.0) as client:
        newsletters = await fetch_newsletters_json(client)
        if not any(n.get("issue") == issue for n in newsletters):
            raise HTTPException(status_code=404, detail=f"Newsletter issue {issue} not found")

        remaining = [n for n in newsletters if n.get("issue") != issue]

        await commit_files_to_github(
            client,
            NEWSLETTER_REPO,
            NEWSLETTER_BRANCH,
            f"Delete newsletter issue-{issue}",
            files=[{"path": NEWSLETTER_JSON_PATH, "content": newsletters_json_bytes(remaining)}],
            delete_paths=[_newsletter_pdf_path(issue)],
        )

    return {"status": "success", "id": f"issue-{issue}"}
