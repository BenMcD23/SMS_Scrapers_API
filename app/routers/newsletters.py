"""Newsletter management. Every issue lives in the cadet website repo: the list
in src/data/newsletters.json and each PDF in public/newsletters/. The website
shows the highest issue as current and the rest as back issues, so one commit
per change is all it takes."""

import base64
import json

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.config import GITHUB_BRANCH, GITHUB_REPO, NEWSLETTER_JSON_PATH
from core.github import commit_files_to_github, github_headers
from core.security import require_staff

router = APIRouter()

MAX_PDF_BYTES = 25 * 1024 * 1024


async def _read_pdf(file: UploadFile) -> bytes:
    """Read an uploaded newsletter PDF, refusing anything over MAX_PDF_BYTES.

    The other upload paths cap their payloads (2 MB signatures, 5 MB receipts);
    these commit the bytes straight into a GitHub repo, so they need a ceiling
    too. 25 MB is comfortably above a real newsletter.
    """
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail="Newsletter PDF must be under 25 MB")
    return data


def _newsletter_pdf_path(issue: int) -> str:
    """Repo path of an issue's PDF; the website serves public/ at its root."""
    return f"public/newsletters/issue-{issue}.pdf"


async def fetch_newsletters_json(client: httpx.AsyncClient) -> list[dict]:
    """Read the issue list from the website repo. Every endpoint starts here,
    so GitHub not answering is turned into a clear 502 rather than a crash."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{NEWSLETTER_JSON_PATH}"
    try:
        resp = await client.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub did not answer ({type(exc).__name__}). Try again.")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not fetch {NEWSLETTER_JSON_PATH} from {GITHUB_REPO}@{GITHUB_BRANCH} "
                f"(GitHub returned {resp.status_code}). Make sure the file is pushed to that branch."
            ),
        )
    return json.loads(base64.b64decode(resp.json()["content"]).decode("utf-8"))


def newsletters_json_bytes(newsletters: list[dict]) -> bytes:
    """Serialise newsletters sorted by issue (descending) to JSON bytes.

    The website sorts again itself, but newest first keeps the file readable
    and its diffs small.
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

    pdf_bytes = await _read_pdf(file)
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
            GITHUB_REPO,
            GITHUB_BRANCH,
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
            files.append({"path": _newsletter_pdf_path(issue), "content": await _read_pdf(file)})

        await commit_files_to_github(
            client,
            GITHUB_REPO,
            GITHUB_BRANCH,
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
            GITHUB_REPO,
            GITHUB_BRANCH,
            f"Delete newsletter issue-{issue}",
            files=[{"path": NEWSLETTER_JSON_PATH, "content": newsletters_json_bytes(remaining)}],
            delete_paths=[_newsletter_pdf_path(issue)],
        )

    return {"status": "success", "id": f"issue-{issue}"}
