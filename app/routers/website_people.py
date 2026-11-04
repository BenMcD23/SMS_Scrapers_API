"""Staff and NCO photos on the public website (Our Staff, Our NCOs pages).

The list is `src/data/people.json` in the cadet website repo: `{"staff": [...],
"ncos": [...]}`, each entry `{rank, name, image}` plus an optional `sortAs`
(a retired officer serving as a CI sits under the OC). The photos sit in
`src/assets/staff_pics/` and `src/assets/nco_pics/`, and the site sorts
everyone by rank then name (`src/data/people.js`), so the order in the file
doesn't matter; it's kept sorted the same way so the diffs read well.

A person is addressed by their name within a group: the site shows nobody by
anything else, and two people with one name would be indistinguishable there.
"""

import io
import json
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError

from core.config import GITHUB_BRANCH, GITHUB_REPO
from core.github import commit_files_to_github, fetch_repo_json
from core.security import require_staff
from routers.gallery import slug

router = APIRouter()

PEOPLE_JSON_PATH = "src/data/people.json"
PHOTO_DIRS = {"staff": "src/assets/staff_pics", "ncos": "src/assets/nco_pics"}
# The website's stand-in for someone with no photo yet. It only exists in
# staff_pics, so an NCO always needs a real photo.
PLACEHOLDER = "Placeholder.webp"

# Pinned to STAFF_RANKS and NCO_RANKS in the website's src/data/people.js: a
# rank the site doesn't list sorts to the bottom (staff) or isn't shown at all
# (NCOs, whose page has one section per rank). Change them together.
STAFF_RANKS = ["Sqn Ldr", "Flt Lt", "Wg Cdr (Ret'd)", "Fg Off", "Plt Off", "WO", "FS", "Sgt", "Cpl", "CI"]
NCO_RANKS = ["CWO", "FS", "Sgt", "Cpl"]
RANKS = {"staff": STAFF_RANKS, "ncos": NCO_RANKS}

# Matches the existing photos: 600px wide, portrait.
PHOTO_WIDTH = 600
MAX_NAME_LEN = 60

Group = Literal["staff", "ncos"]


def people_json_bytes(people: dict) -> bytes:
    return (json.dumps(people, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def sort_group(group: str, entries: list[dict]) -> list[dict]:
    order = RANKS[group]

    def key(p):
        rank = p.get("sortAs") or p["rank"]
        return (order.index(rank) if rank in order else len(order), p["name"].casefold())

    return sorted(entries, key=key)


def to_portrait_webp(data: bytes) -> bytes:
    """Phone photo → upright webp, PHOTO_WIDTH wide. Raises 400 if not an image."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        # Width is what the cards size by; a very tall photo is still capped so
        # one odd upload can't be megabytes.
        img.thumbnail((PHOTO_WIDTH, PHOTO_WIDTH * 2))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise HTTPException(status_code=400, detail="That file isn't an image we can read (use JPEG, PNG or WebP).")
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue()


def clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"Name must be 1–{MAX_NAME_LEN} characters.")
    # People are addressed by name in the URL, so a slash would make them unreachable.
    if "/" in name:
        raise HTTPException(status_code=400, detail="Name can't contain a slash.")
    return name


def check_rank(group: str, rank: str, field: str = "Rank") -> str:
    if rank not in RANKS[group]:
        raise HTTPException(status_code=400, detail=f"{field} must be one of: {', '.join(RANKS[group])}.")
    return rank


def find(entries: list[dict], name: str) -> dict | None:
    return next((p for p in entries if p["name"].casefold() == name.casefold()), None)


def photo_name(rank: str, name: str, taken: set[str]) -> str:
    """`sgt-c-mack.webp`, with a number on the end if someone else already has it."""
    base = f"{slug(rank)}-{slug(name)}"
    candidate, n = f"{base}.webp", 2
    while candidate in taken:
        candidate, n = f"{base}-{n}.webp", n + 1
    return candidate


async def fetch_people(client: httpx.AsyncClient) -> dict:
    people = await fetch_repo_json(client, GITHUB_REPO, GITHUB_BRANCH, PEOPLE_JSON_PATH)
    return {"staff": people.get("staff", []), "ncos": people.get("ncos", [])}


def unused_photos(people: dict, group: str, candidates: list[str]) -> list[str]:
    """Of `candidates`, the photos nobody in `group` uses any more (never the placeholder)."""
    used = {p.get("image") for p in people[group]}
    return [f"{PHOTO_DIRS[group]}/{c}" for c in dict.fromkeys(candidates) if c and c != PLACEHOLDER and c not in used]


async def read_photo(file: UploadFile | None) -> bytes | None:
    # An empty file part is what a browser sends for "no file chosen".
    if file is None or not file.filename:
        return None
    data = await file.read()
    if not data:
        return None
    # Pillow is CPU-bound: off the event loop, or every other request waits.
    return await run_in_threadpool(to_portrait_webp, data)


@router.get("/website-people")
async def list_people(idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=60.0) as client:
        people = await fetch_people(client)
    return {
        "staff": sort_group("staff", people["staff"]),
        "ncos": sort_group("ncos", people["ncos"]),
        "ranks": RANKS,
    }


async def commit(client: httpx.AsyncClient, people: dict, group: str, message: str, photo: tuple[str, bytes] | None,
                 delete: list[str]):
    people[group] = sort_group(group, people[group])
    files = [{"path": PEOPLE_JSON_PATH, "content": people_json_bytes(people)}]
    if photo:
        files.append({"path": f"{PHOTO_DIRS[group]}/{photo[0]}", "content": photo[1]})
    await commit_files_to_github(client, GITHUB_REPO, GITHUB_BRANCH, message, files=files, delete_paths=delete)


def apply_sort_as(group: str, entry: dict, sort_as: str | None):
    """`sortAs` is a staff-only override; an empty value clears it."""
    sort_as = (sort_as or "").strip()
    if not sort_as:
        entry.pop("sortAs", None)
        return
    if group != "staff":
        raise HTTPException(status_code=400, detail="Only staff can be listed under a different rank.")
    entry["sortAs"] = check_rank(group, sort_as, "List under")


@router.post("/website-people/{group}")
async def add_person(
    group: Group,
    rank: str = Form(...),
    name: str = Form(...),
    sort_as: str | None = Form(None),
    file: UploadFile | None = File(None),
    idinfo: dict = Depends(require_staff),
):
    name = clean_name(name)
    check_rank(group, rank)
    entry = {"rank": rank, "name": name}
    apply_sort_as(group, entry, sort_as)
    # Convert before touching GitHub, so a bad file changes nothing.
    webp = await read_photo(file)
    if webp is None and group == "ncos":
        raise HTTPException(status_code=400, detail="Add a photo: the NCO page has no placeholder.")

    async with httpx.AsyncClient(timeout=120.0) as client:
        people = await fetch_people(client)
        if find(people[group], name):
            raise HTTPException(status_code=409, detail=f"{name} is already on the website.")
        photo = None
        if webp is None:
            entry["image"] = PLACEHOLDER
        else:
            fname = photo_name(rank, name, {p.get("image") for p in people[group]})
            entry["image"], photo = fname, (fname, webp)
        people[group].append(entry)
        await commit(client, people, group, f"Add {rank} {name} to the website", photo, [])
    return entry


@router.patch("/website-people/{group}/{current}")
async def edit_person(
    group: Group,
    current: str,
    rank: str = Form(...),
    name: str = Form(...),
    sort_as: str | None = Form(None),
    file: UploadFile | None = File(None),
    idinfo: dict = Depends(require_staff),
):
    """Save the edit form: rank (a promotion), name and list-under rank as sent,
    and a new photo if one came. The whole form is sent each time because an
    empty form field arrives as "not sent", so leaving `sort_as` out is how it
    gets cleared."""
    check_rank(group, rank)
    name = clean_name(name)
    webp = await read_photo(file)

    async with httpx.AsyncClient(timeout=120.0) as client:
        people = await fetch_people(client)
        entry = find(people[group], current)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{current} isn't on the website.")
        clash = find(people[group], name)
        if clash is not None and clash is not entry:
            raise HTTPException(status_code=409, detail=f"{name} is already on the website.")
        entry["rank"], entry["name"] = rank, name
        apply_sort_as(group, entry, sort_as)

        old_image, photo = entry.get("image"), None
        if webp is not None:
            taken = {p.get("image") for p in people[group] if p is not entry}
            fname = photo_name(rank, name, taken)
            entry["image"], photo = fname, (fname, webp)
        # The old photo goes only once nobody uses it, and never when the new
        # one was written over it under the same name.
        delete = [d for d in unused_photos(people, group, [old_image]) if not photo or not d.endswith("/" + photo[0])]
        await commit(client, people, group, f"Update {rank} {name} on the website", photo, delete)
    return entry


@router.delete("/website-people/{group}/{name}")
async def remove_person(group: Group, name: str, idinfo: dict = Depends(require_staff)):
    async with httpx.AsyncClient(timeout=120.0) as client:
        people = await fetch_people(client)
        entry = find(people[group], name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{name} isn't on the website.")
        people[group] = [p for p in people[group] if p is not entry]
        # Only photos named in the JSON can be deleted, so a crafted name
        # can't remove anything else from the repo.
        delete = unused_photos(people, group, [entry.get("image")])
        await commit(client, people, group, f"Remove {entry['rank']} {entry['name']} from the website", None, delete)
    return {"status": "success", "name": entry["name"]}
