"""Staff/NCO photo management.

People metadata lives in `src/data/people.json` in the website repo, and the
photos under `public/people/<team>/`. The website renders the grids from that
JSON, sorted by rank then first name. Uploads here commit the processed image
(background already removed/cropped in the browser) plus the updated JSON in a
single commit, which triggers a Vercel redeploy.
"""

import base64
import json
import re

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.config import GITHUB_REPO, GITHUB_BRANCH, PEOPLE_JSON_PATH
from core.github import github_headers, commit_files_to_github
from core.security import require_staff

router = APIRouter()

# people.json has two arrays; the public API uses "staff"/"nco" for the team and
# maps them to the JSON keys here.
TEAM_KEYS = {"staff": "staff", "nco": "ncos"}

# image/<subtype> -> file extension we store the photo under.
IMAGE_EXTS = {"image/webp": "webp", "image/png": "png", "image/jpeg": "jpg"}

PLACEHOLDER_IMAGE = "/people/placeholder.webp"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "person"


def _unique_id(base: str, data: dict) -> str:
    existing = {p.get("id") for arr in data.values() for p in arr}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _image_path(team: str, person_id: str, ext: str) -> str:
    """Repo path for a person's photo (matches the public URL it's served at)."""
    return f"public/people/{team}/{person_id}.{ext}"


async def fetch_people_json(client: httpx.AsyncClient) -> dict:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{PEOPLE_JSON_PATH}"
    resp = await client.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not fetch {PEOPLE_JSON_PATH} from {GITHUB_REPO}@{GITHUB_BRANCH} "
                f"(GitHub returned {resp.status_code}). Make sure the file is pushed to that branch."
            ),
        )
    data = json.loads(base64.b64decode(resp.json()["content"]).decode("utf-8"))
    # Be forgiving if a key is missing.
    data.setdefault("staff", [])
    data.setdefault("ncos", [])
    return data


def people_json_bytes(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _find_person(data: dict, person_id: str):
    """Return (team_param, json_key, entry) for the given id, or (None, None, None)."""
    for team, key in TEAM_KEYS.items():
        for entry in data.get(key, []):
            if entry.get("id") == person_id:
                return team, key, entry
    return None, None, None


@router.get("/people")
async def list_people(idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=60.0) as client:
        return await fetch_people_json(client)


@router.post("/people")
async def add_person(
    team: str = Form(...),
    rank: str = Form(...),
    name: str = Form(...),
    file: UploadFile = File(...),
    idinfo: dict = Depends(require_staff),
):
    key = TEAM_KEYS.get(team)
    if key is None:
        raise HTTPException(status_code=400, detail="team must be 'staff' or 'nco'")
    if not rank.strip() or not name.strip():
        raise HTTPException(status_code=400, detail="rank and name are required")

    ext = IMAGE_EXTS.get(file.content_type)
    if ext is None:
        raise HTTPException(status_code=400, detail="Photo must be a WEBP, PNG or JPEG image")
    image_bytes = await file.read()

    async with httpx.AsyncClient(timeout=120.0) as client:
        data = await fetch_people_json(client)

        person_id = _unique_id(_slugify(name), data)
        image_repo_path = _image_path(team, person_id, ext)

        entry = {
            "id": person_id,
            "rank": rank.strip(),
            "name": name.strip(),
            "image": "/" + image_repo_path.removeprefix("public/"),
        }
        data[key].append(entry)

        await commit_files_to_github(
            client,
            GITHUB_REPO,
            GITHUB_BRANCH,
            f"Add {rank.strip()} {name.strip()} photo",
            files=[
                {"path": PEOPLE_JSON_PATH, "content": people_json_bytes(data)},
                {"path": image_repo_path, "content": image_bytes},
            ],
        )

    return {"status": "success", "id": person_id, "image": entry["image"]}


@router.put("/people/{person_id}")
async def update_person(
    person_id: str,
    rank: str = Form(None),
    name: str = Form(None),
    file: UploadFile = File(None),
    idinfo: dict = Depends(require_staff),
):
    async with httpx.AsyncClient(timeout=120.0) as client:
        data = await fetch_people_json(client)
        team, key, entry = _find_person(data, person_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Person '{person_id}' not found")

        if rank is not None and rank.strip():
            entry["rank"] = rank.strip()
        if name is not None and name.strip():
            entry["name"] = name.strip()

        files = []
        delete_paths = []

        # Optionally replace the photo. Stored as <id>.<ext>; if the new
        # extension differs from the old image, drop the old file.
        if file is not None and file.filename:
            ext = IMAGE_EXTS.get(file.content_type)
            if ext is None:
                raise HTTPException(status_code=400, detail="Photo must be a WEBP, PNG or JPEG image")
            image_repo_path = _image_path(team, person_id, ext)
            new_image = "/" + image_repo_path.removeprefix("public/")
            old_image = entry.get("image", "")
            if old_image and old_image != new_image and old_image != PLACEHOLDER_IMAGE:
                delete_paths.append("public" + old_image)
            entry["image"] = new_image
            files.append({"path": image_repo_path, "content": await file.read()})

        files.append({"path": PEOPLE_JSON_PATH, "content": people_json_bytes(data)})

        await commit_files_to_github(
            client,
            GITHUB_REPO,
            GITHUB_BRANCH,
            f"Update {entry['rank']} {entry['name']}",
            files=files,
            delete_paths=delete_paths or None,
        )

    return {"status": "success", "id": person_id, "image": entry.get("image")}


@router.delete("/people/{person_id}")
async def delete_person(person_id: str, idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=120.0) as client:
        data = await fetch_people_json(client)
        team, key, entry = _find_person(data, person_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Person '{person_id}' not found")

        data[key] = [p for p in data[key] if p.get("id") != person_id]

        # Remove the photo too, unless it's the shared placeholder.
        delete_paths = None
        image = entry.get("image", "")
        if image and image != PLACEHOLDER_IMAGE and image.startswith("/people/"):
            delete_paths = ["public" + image]

        await commit_files_to_github(
            client,
            GITHUB_REPO,
            GITHUB_BRANCH,
            f"Remove {entry['rank']} {entry['name']}",
            files=[{"path": PEOPLE_JSON_PATH, "content": people_json_bytes(data)}],
            delete_paths=delete_paths,
        )

    return {"status": "success", "id": person_id}
