"""Website gallery publishing. GitHub is the FakeGitHub from conftest."""

import io
import json

import pytest
from PIL import Image

from core.config import GITHUB_REPO
from routers.gallery import GALLERY_DIR, GALLERY_JSON_PATH

STAFF = {"Authorization": "Bearer staff"}


def _jpeg(w=2200, h=1100, exif_rotate=False):
    buf = io.BytesIO()
    img = Image.new("RGB", (w, h), "red")
    if exif_rotate:
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90° clockwise" — how phones save portrait shots
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def _seed(github, photos):
    github.files[GITHUB_REPO] = {GALLERY_JSON_PATH: json.dumps(photos).encode()}
    for p in photos:
        github.files[GITHUB_REPO][f"{GALLERY_DIR}/{p['file']}"] = b"webp"


def _stored(github):
    return json.loads(github.files[GITHUB_REPO][GALLERY_JSON_PATH])


SEED = [
    {"cat": "Remembrance", "file": "remembrance-00.webp", "w": 1100, "h": 568, "alt": "Parade"},
    {"cat": "Band", "file": "band-20.webp", "w": 1100, "h": 825, "alt": "Drums"},
]


def _add(api, section, n=1, alts=None, content=None):
    alts = alts if alts is not None else [f"Photo {i}" for i in range(n)]
    return api.post(
        "/gallery",
        headers=STAFF,
        data={"section": section, "alts": alts},
        files=[("files", (f"p{i}.jpg", content or _jpeg(), "image/jpeg")) for i in range(n)],
    )


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [("get", "/gallery"), ("post", "/gallery"), ("delete", "/gallery/x.webp")])
def test_needs_a_token(api, method, path):
    assert getattr(api, method)(path).status_code == 401


@pytest.mark.parametrize("role", ["cadet", "nco", "snco"])
@pytest.mark.parametrize("method,path", [("get", "/gallery"), ("post", "/gallery"), ("delete", "/gallery/x.webp")])
def test_staff_only(api, role, method, path):
    assert getattr(api, method)(path, headers=api.as_(role)).status_code == 403


@pytest.mark.parametrize("role", ["staff", "oc", "owner"])
def test_staff_and_above_can_list(api, github, role):
    _seed(github, SEED)
    res = api.get("/gallery", headers=api.as_(role))
    assert res.status_code == 200 and res.json() == SEED


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_when_the_json_is_missing_explains_why(api, github):
    res = api.get("/gallery", headers=STAFF)
    assert res.status_code == 500 and GALLERY_JSON_PATH in res.json()["detail"]


# ── add ───────────────────────────────────────────────────────────────────────

def test_adding_to_an_existing_section_keeps_sections_together(api, github):
    _seed(github, SEED)
    res = _add(api, "Remembrance", n=2)
    assert res.status_code == 200

    stored = _stored(github)
    # New photos go straight after the last Remembrance one, before Band.
    assert [p["file"] for p in stored] == [
        "remembrance-00.webp", "remembrance-21.webp", "remembrance-22.webp", "band-20.webp",
    ]
    assert len(github.commits) == 1
    assert f"{GALLERY_DIR}/remembrance-21.webp" in github.commits[0]["paths"]


def test_photos_are_resized_to_webp_and_sized(api, github):
    _seed(github, SEED)
    _add(api, "Band")
    entry = _stored(github)[-1]
    assert (entry["w"], entry["h"]) == (1100, 550)
    img = Image.open(io.BytesIO(github.files[GITHUB_REPO][f"{GALLERY_DIR}/{entry['file']}"]))
    assert img.format == "WEBP" and img.size == (1100, 550)


def test_phone_portrait_photos_are_turned_upright(api, github):
    _seed(github, SEED)
    _add(api, "Band", content=_jpeg(2200, 1100, exif_rotate=True))
    entry = _stored(github)[-1]
    assert (entry["w"], entry["h"]) == (550, 1100)


def test_a_new_section_goes_on_the_end(api, github):
    _seed(github, SEED)
    res = _add(api, "  Summer   Camp ")
    assert res.json()["section"] == "Summer Camp"
    last = _stored(github)[-1]
    assert last["cat"] == "Summer Camp" and last["file"] == "summer-camp-21.webp"


def test_section_names_match_existing_ones_ignoring_case(api, github):
    _seed(github, SEED)
    _add(api, "remembrance")
    assert {p["cat"] for p in _stored(github)} == {"Remembrance", "Band"}


def test_non_ascii_section_and_alt_text(api, github):
    _seed(github, SEED)
    res = _add(api, "Café Night", alts=["Siân's award"])
    assert res.status_code == 200
    last = _stored(github)[-1]
    assert last["cat"] == "Café Night" and last["alt"] == "Siân's award"
    assert last["file"] == "cafe-night-21.webp"


def test_first_photo_in_an_empty_gallery(api, github):
    _seed(github, [])
    _add(api, "Band")
    assert _stored(github) == [{"cat": "Band", "file": "band-0.webp", "w": 1100, "h": 550, "alt": "Photo 0"}]


# An empty form field counts as missing, so FastAPI answers 422 before we see it.
@pytest.mark.parametrize("section,code", [("", 422), ("   ", 400), ("x" * 41, 400)])
def test_bad_section_name_is_rejected(api, github, section, code):
    _seed(github, SEED)
    assert _add(api, section).status_code == code
    assert github.commits == []


def test_every_photo_needs_a_description(api, github):
    _seed(github, SEED)
    assert _add(api, "Band", n=2, alts=["One", " "]).status_code == 400
    assert _add(api, "Band", n=2, alts=["One"]).status_code == 400
    assert github.commits == []


def test_a_file_that_isnt_an_image_adds_nothing(api, github):
    _seed(github, SEED)
    res = _add(api, "Band", content=b"%PDF-1.4 not a photo")
    assert res.status_code == 400 and github.commits == []


def test_missing_fields_are_422(api, github):
    assert api.post("/gallery", headers=STAFF, data={"section": "Band"}).status_code == 422


def test_github_failing_leaves_the_gallery_as_it_was(api, github):
    _seed(github, SEED)
    github.fail["tree"] = 500
    assert _add(api, "Band").status_code == 500
    assert _stored(github) == SEED


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_removes_the_entry_and_the_image(api, github):
    _seed(github, SEED)
    res = api.delete("/gallery/band-20.webp", headers=STAFF)
    assert res.status_code == 200
    assert _stored(github) == SEED[:1]
    assert f"{GALLERY_DIR}/band-20.webp" not in github.files[GITHUB_REPO]


def test_delete_unknown_photo_is_404(api, github):
    _seed(github, SEED)
    # Only files listed in the JSON can be deleted, so a crafted name can't
    # remove anything else from the repo.
    assert api.delete("/gallery/..%2Fgallery.json", headers=STAFF).status_code == 404
    assert api.delete("/gallery/nope.webp", headers=STAFF).status_code == 404
    assert github.commits == []
