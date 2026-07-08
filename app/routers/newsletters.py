"""Newsletter management — PDFs and metadata live in the newsletter repo on GitHub."""

import base64
import json

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.config import (
    NEWSLETTER_REPO,
    NEWSLETTER_BRANCH,
    NEWSLETTER_JSON_PATH,
    GITHUB_REPO,
    GITHUB_BRANCH,
)
from core.github import github_headers, commit_files_to_github
from core.security import require_staff

router = APIRouter()

# Where the current issue is mirrored on the main cadet website repo so the
# /newsletter page can embed it. Website public/ is served at the site root.
WEBSITE_CURRENT_JSON_PATH = "src/data/currentNewsletter.json"


def _newsletter_pdf_path(issue: int) -> str:
    return f"317_newsletter/public/newsletters/issue-{issue}.pdf"


def _website_pdf_path(issue: int) -> str:
    return f"public/newsletters/issue-{issue}.pdf"


def current_newsletter(newsletters: list[dict]) -> dict | None:
    """The homepage/current issue = highest issue number. None if list is empty."""
    if not newsletters:
        return None
    return max(newsletters, key=lambda n: n.get("issue", 0))


async def fetch_newsletter_pdf(client: httpx.AsyncClient, issue: int) -> bytes:
    """Download an issue's PDF bytes from the newsletter repo (raw, size-independent)."""
    url = f"https://api.github.com/repos/{NEWSLETTER_REPO}/contents/{_newsletter_pdf_path(issue)}"
    resp = await client.get(
        url,
        headers={**github_headers(), "Accept": "application/vnd.github.raw"},
        params={"ref": NEWSLETTER_BRANCH},
    )
    if resp.status_code != 200 or resp.content[:4] != b"%PDF":
        raise HTTPException(
            status_code=500,
            detail=f"Could not fetch issue-{issue}.pdf from {NEWSLETTER_REPO}@{NEWSLETTER_BRANCH}",
        )
    return resp.content


async def list_old_website_pdfs(client: httpx.AsyncClient, keep_issue: int) -> list[str]:
    """Repo paths of newsletter PDFs on the website other than the current issue."""
    keep = f"issue-{keep_issue}.pdf"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/public/newsletters"
    resp = await client.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200 or not isinstance(resp.json(), list):
        return []
    return [
        f"public/newsletters/{f['name']}"
        for f in resp.json()
        if f.get("name", "").endswith(".pdf") and f["name"] != keep
    ]


async def sync_current_to_website(
    client: httpx.AsyncClient,
    current: dict,
    pdf_bytes: bytes | None = None,
):
    """Mirror the current issue (metadata + PDF) into the cadet website repo.

    `pdf_bytes` is passed through when the caller already holds them (the common
    case: the issue being uploaded/edited IS the current one); otherwise the PDF
    is fetched from the newsletter repo. Older newsletter PDFs on the website are
    pruned so only the current issue is kept.
    """
    issue = current["issue"]
    if pdf_bytes is None:
        pdf_bytes = await fetch_newsletter_pdf(client, issue)

    json_bytes = (json.dumps(current, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    old_pdfs = await list_old_website_pdfs(client, keep_issue=issue)

    await commit_files_to_github(
        client,
        GITHUB_REPO,
        GITHUB_BRANCH,
        f"Sync current newsletter (issue-{issue}) to website",
        files=[
            {"path": WEBSITE_CURRENT_JSON_PATH, "content": json_bytes},
            {"path": _website_pdf_path(issue), "content": pdf_bytes},
        ],
        delete_paths=old_pdfs,
    )


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

        # Mirror the current issue onto the website (pass bytes if we just uploaded it)
        current = current_newsletter(newsletters)
        await sync_current_to_website(
            client, current, pdf_bytes if current["issue"] == issue else None
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
        new_pdf_bytes = None
        if file is not None and file.filename:
            if file.content_type != "application/pdf":
                raise HTTPException(status_code=400, detail="File must be a PDF")
            new_pdf_bytes = await file.read()
            files.append({"path": _newsletter_pdf_path(issue), "content": new_pdf_bytes})

        await commit_files_to_github(
            client,
            NEWSLETTER_REPO,
            NEWSLETTER_BRANCH,
            f"Update newsletter issue-{issue}",
            files=files,
        )

        # Re-mirror the current issue to the website. Only reuse the new PDF
        # bytes if the issue we just edited is the current one.
        current = current_newsletter(newsletters)
        await sync_current_to_website(
            client, current, new_pdf_bytes if current["issue"] == issue else None
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

        # Re-mirror the new current issue to the website (fetches its PDF).
        # ponytail: if the last issue was deleted there's no current issue; we
        # leave the website's stale copy in place rather than blanking the page.
        current = current_newsletter(remaining)
        if current is not None:
            await sync_current_to_website(client, current)

    return {"status": "success", "id": f"issue-{issue}"}
