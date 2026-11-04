"""Staff and NCO photos on the website. GitHub is the FakeGitHub from conftest."""

import io
import json

import pytest
from PIL import Image

from core.config import GITHUB_REPO
from routers.website_people import NCO_RANKS, PEOPLE_JSON_PATH, PHOTO_DIRS, PLACEHOLDER, STAFF_RANKS

STAFF = {"Authorization": "Bearer staff"}
STAFF_DIR, NCO_DIR = PHOTO_DIRS["staff"], PHOTO_DIRS["ncos"]

SEED = {
    "staff": [
        {"rank": "CI", "name": "Ben McDonald", "image": "CI_McDonald.webp"},
        {"rank": "Flt Lt", "name": "Simon Doherty", "image": "Flt_Lt_Doherty.webp"},
        {"rank": "CI", "sortAs": "Wg Cdr (Ret'd)", "name": "Roy Catterall (Wg Cdr Ret'd)", "image": "CI_Catterall.webp"},
        {"rank": "CI", "name": "Graham Boxall", "image": PLACEHOLDER},
    ],
    "ncos": [
        {"rank": "Cpl", "name": "H Boxall", "image": "Cdt Boxall.webp"},
        {"rank": "FS", "name": "M Beverley", "image": "FS Beverley.webp"},
        # Two Tyrells: names are unique, surnames needn't be.
        {"rank": "Sgt", "name": "E Tyrell", "image": "Sgt Tyrell.webp"},
        {"rank": "Sgt", "name": "T Tyrell", "image": "Cpl Tyrell.webp"},
    ],
}


def _jpeg(w=1200, h=1600, exif_rotate=False):
    buf = io.BytesIO()
    img = Image.new("RGB", (w, h), "blue")
    if exif_rotate:
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90° clockwise" — how phones save portrait shots
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def _seed(github, people=None):
    people = json.loads(json.dumps(SEED if people is None else people))
    github.files[GITHUB_REPO] = {PEOPLE_JSON_PATH: json.dumps(people).encode()}
    for group, folder in PHOTO_DIRS.items():
        for p in people.get(group, []):
            github.files[GITHUB_REPO][f"{folder}/{p['image']}"] = b"webp"
    github.files[GITHUB_REPO][f"{STAFF_DIR}/{PLACEHOLDER}"] = b"placeholder"


def _stored(github):
    return json.loads(github.files[GITHUB_REPO][PEOPLE_JSON_PATH])


def _repo(github):
    return github.files[GITHUB_REPO]


def _add(api, group, rank, name, photo=True, content=None, sort_as=None, headers=STAFF):
    data = {"rank": rank, "name": name}
    if sort_as is not None:
        data["sort_as"] = sort_as
    files = {"file": ("me.jpg", content or _jpeg(), "image/jpeg")} if photo else None
    return api.post(f"/website-people/{group}", headers=headers, data=data, files=files)


def _edit(api, group, current, rank, name, photo=False, content=None, sort_as=None):
    """Save the edit form, which always sends rank and name."""
    data = {"rank": rank, "name": name}
    if sort_as is not None:
        data["sort_as"] = sort_as
    files = {"file": ("me.jpg", content or _jpeg(), "image/jpeg")} if photo else None
    return api.patch(f"/website-people/{group}/{current}", headers=STAFF, data=data, files=files)


ROUTES = [
    ("get", "/website-people"),
    ("post", "/website-people/staff"),
    ("patch", "/website-people/staff/Ben McDonald"),
    ("delete", "/website-people/staff/Ben McDonald"),
]


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", ROUTES)
def test_needs_a_token(api, method, path):
    assert getattr(api, method)(path).status_code == 401


@pytest.mark.parametrize("role", ["cadet", "nco", "snco"])
@pytest.mark.parametrize("method,path", ROUTES)
def test_staff_only(api, github, role, method, path):
    _seed(github)
    assert getattr(api, method)(path, headers=api.as_(role)).status_code == 403
    assert github.commits == []


@pytest.mark.parametrize("role", ["staff", "oc", "owner"])
def test_staff_and_above_can_list_and_add(api, github, role):
    _seed(github)
    assert api.get("/website-people", headers=api.as_(role)).status_code == 200
    assert _add(api, "staff", "CI", f"New {role}", headers=api.as_(role)).status_code == 200


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_is_sorted_the_way_the_website_shows_it(api, github):
    _seed(github)
    body = api.get("/website-people", headers=STAFF).json()
    # Catterall's sortAs puts him under the OC, above the other CIs.
    assert [p["name"] for p in body["staff"]] == [
        "Simon Doherty", "Roy Catterall (Wg Cdr Ret'd)", "Ben McDonald", "Graham Boxall",
    ]
    assert [p["name"] for p in body["ncos"]] == ["M Beverley", "E Tyrell", "T Tyrell", "H Boxall"]
    assert body["ranks"] == {"staff": STAFF_RANKS, "ncos": NCO_RANKS}


def test_a_group_missing_from_the_json_lists_as_empty(api, github):
    _seed(github, {"staff": SEED["staff"]})
    assert api.get("/website-people", headers=STAFF).json()["ncos"] == []


def test_list_when_the_json_is_missing_explains_why(api, github):
    res = api.get("/website-people", headers=STAFF)
    assert res.status_code == 500 and PEOPLE_JSON_PATH in res.json()["detail"]


def test_github_timing_out_is_a_502_not_a_crash(api, http):
    import httpx

    def boom(request):
        raise httpx.ConnectTimeout("GitHub timed out")

    http.handler = boom
    res = api.get("/website-people", headers=STAFF)
    assert res.status_code == 502 and "GitHub" in res.json()["detail"]


def test_github_timing_out_mid_commit_is_a_502_and_changes_nothing(api, github, http):
    import httpx

    _seed(github)

    def flaky(request):
        if request.url.path.endswith("/git/trees"):
            raise httpx.ReadTimeout("GitHub timed out")
        return github(request)

    http.handler = flaky
    assert _add(api, "ncos", "Cpl", "S Okon").status_code == 502
    assert _stored(github) == SEED


# ── add ───────────────────────────────────────────────────────────────────────

def test_adding_an_nco_commits_the_photo_and_a_sorted_list(api, github):
    _seed(github)
    res = _add(api, "ncos", "Sgt", "C Mack")
    assert res.status_code == 200
    assert res.json() == {"rank": "Sgt", "name": "C Mack", "image": "sgt-c-mack.webp"}

    stored = _stored(github)
    assert [p["name"] for p in stored["ncos"]] == ["M Beverley", "C Mack", "E Tyrell", "T Tyrell", "H Boxall"]
    # The other group is written back untouched.
    assert stored["staff"] == SEED["staff"]
    assert len(github.commits) == 1
    assert github.commits[0]["message"] == "Add Sgt C Mack to the website"
    assert set(github.commits[0]["paths"]) == {PEOPLE_JSON_PATH, f"{NCO_DIR}/sgt-c-mack.webp"}


def test_photos_are_shrunk_to_600_wide_webp(api, github):
    _seed(github)
    _add(api, "staff", "Fg Off", "Jonathon Barker", content=_jpeg(1800, 2400))
    img = Image.open(io.BytesIO(_repo(github)[f"{STAFF_DIR}/fg-off-jonathon-barker.webp"]))
    assert img.format == "WEBP" and img.size == (600, 800)


def test_phone_portrait_photos_are_turned_upright(api, github):
    _seed(github)
    # Stored landscape with an EXIF "rotate" flag, as phones do: upright it's portrait.
    _add(api, "staff", "CI", "Sophie Stone", content=_jpeg(1600, 1200, exif_rotate=True))
    img = Image.open(io.BytesIO(_repo(github)[f"{STAFF_DIR}/ci-sophie-stone.webp"]))
    assert img.size == (600, 800)


def test_staff_without_a_photo_get_the_placeholder(api, github):
    _seed(github)
    res = _add(api, "staff", "CI", "Jacob Gill", photo=False)
    assert res.json()["image"] == PLACEHOLDER
    assert github.commits[0]["paths"] == [PEOPLE_JSON_PATH]


def test_an_empty_file_part_counts_as_no_photo(api, github):
    # What a browser sends for a file input left empty.
    _seed(github)
    res = api.post("/website-people/staff", headers=STAFF, data={"rank": "CI", "name": "Jacob Gill"},
                   files={"file": ("", b"", "application/octet-stream")})
    assert res.status_code == 200 and res.json()["image"] == PLACEHOLDER


def test_an_nco_needs_a_photo(api, github):
    # The placeholder only exists in staff_pics, so it would be a broken image.
    _seed(github)
    res = _add(api, "ncos", "Cpl", "S Okon", photo=False)
    assert res.status_code == 400 and github.commits == []


def test_same_rank_and_name_photo_gets_a_number(api, github):
    _seed(github, {"staff": [{"rank": "CI", "name": "Jo Smith", "image": "ci-jo-smith.webp"}], "ncos": []})
    # Different name, same slug: "Jo Smith" and "Jo  Smith!" both give ci-jo-smith.
    res = _add(api, "staff", "CI", "Jo Smith!")
    assert res.json()["image"] == "ci-jo-smith-2.webp"
    assert _repo(github)[f"{STAFF_DIR}/ci-jo-smith.webp"] == b"webp"


def test_non_ascii_names_keep_their_spelling(api, github):
    _seed(github)
    res = _add(api, "ncos", "Cpl", "S Ó Briain")
    assert res.json()["image"] == "cpl-s-o-briain.webp"
    assert find_name(_stored(github)["ncos"], "S Ó Briain")
    # Written as UTF-8, not \\u escapes, so the website repo diff is readable.
    assert "Ó".encode() in _repo(github)[PEOPLE_JSON_PATH]


def find_name(entries, name):
    return any(p["name"] == name for p in entries)


def test_name_whitespace_is_tidied(api, github):
    _seed(github)
    assert _add(api, "staff", "CI", "  Sophie   Stone ").json()["name"] == "Sophie Stone"


def test_a_name_already_on_the_website_is_409(api, github):
    _seed(github)
    # Case-insensitively, or the site would show two of them.
    assert _add(api, "ncos", "Sgt", "e tyrell").status_code == 409
    assert github.commits == []


def test_the_same_name_can_be_staff_and_an_nco(api, github):
    # The groups are separate pages; an NCO can share a name with a staff member.
    _seed(github)
    assert _add(api, "ncos", "Cpl", "Ben McDonald").status_code == 200


@pytest.mark.parametrize("group,rank", [("ncos", "Flt Lt"), ("staff", "CWO"), ("staff", "ci"), ("staff", "")])
def test_a_rank_the_website_doesnt_list_is_rejected(api, github, group, rank):
    _seed(github)
    assert _add(api, group, rank, "Someone").status_code in (400, 422)
    assert github.commits == []


@pytest.mark.parametrize("name,code", [("", 422), ("   ", 400), ("x" * 61, 400), ("A/B", 400)])
def test_bad_names_are_rejected(api, github, name, code):
    _seed(github)
    assert _add(api, "staff", "CI", name).status_code == code
    assert github.commits == []


def test_an_unknown_group_is_422(api, github):
    _seed(github)
    assert _add(api, "cadets", "Cpl", "Someone").status_code == 422


def test_staff_can_be_listed_under_another_rank(api, github):
    _seed(github)
    res = _add(api, "staff", "CI", "Old Hand", sort_as="Wg Cdr (Ret'd)")
    assert res.json()["sortAs"] == "Wg Cdr (Ret'd)"


@pytest.mark.parametrize("group,rank,sort_as", [("staff", "CI", "Air Marshal"), ("ncos", "Cpl", "FS")])
def test_bad_list_under_rank_is_rejected(api, github, group, rank, sort_as):
    _seed(github)
    assert _add(api, group, rank, "Someone", sort_as=sort_as).status_code == 400
    assert github.commits == []


def test_a_file_that_isnt_an_image_adds_nothing(api, github):
    _seed(github)
    res = _add(api, "ncos", "Cpl", "S Okon", content=b"%PDF-1.4 not a photo")
    assert res.status_code == 400 and github.commits == []


def test_github_failing_leaves_the_website_as_it_was(api, github):
    _seed(github)
    github.fail["tree"] = 500
    assert _add(api, "ncos", "Cpl", "S Okon").status_code == 500
    assert _stored(github) == SEED
    assert f"{NCO_DIR}/cpl-s-okon.webp" not in _repo(github)


# ── edit ──────────────────────────────────────────────────────────────────────

def test_list_under_is_staff_only_on_edit_too(api, github):
    _seed(github)
    assert _edit(api, "ncos", "H Boxall", "Cpl", "H Boxall", sort_as="FS").status_code == 400
    assert github.commits == []


def test_a_promotion_changes_the_rank_and_keeps_the_photo(api, github):
    _seed(github)
    res = _edit(api, "ncos", "H Boxall", "Sgt", "H Boxall")
    assert res.status_code == 200
    entry = next(p for p in _stored(github)["ncos"] if p["name"] == "H Boxall")
    assert entry == {"rank": "Sgt", "name": "H Boxall", "image": "Cdt Boxall.webp"}
    assert github.commits[0]["deleted"] == []


def test_a_new_photo_replaces_the_old_one(api, github):
    _seed(github)
    res = _edit(api, "ncos", "M Beverley", "FS", "M Beverley", photo=True)
    assert res.json()["image"] == "fs-m-beverley.webp"
    assert f"{NCO_DIR}/fs-m-beverley.webp" in _repo(github)
    assert f"{NCO_DIR}/FS Beverley.webp" not in _repo(github)


def test_replacing_a_photo_under_the_same_file_name_keeps_it(api, github):
    _seed(github, {"staff": [], "ncos": [{"rank": "Cpl", "name": "S Okon", "image": "cpl-s-okon.webp"}]})
    _edit(api, "ncos", "S Okon", "Cpl", "S Okon", photo=True)
    # Written over, not written then deleted in the same commit.
    assert github.commits[0]["deleted"] == []
    assert Image.open(io.BytesIO(_repo(github)[f"{NCO_DIR}/cpl-s-okon.webp"])).format == "WEBP"


def test_a_photo_for_someone_on_the_placeholder_keeps_the_placeholder(api, github):
    _seed(github)
    _edit(api, "staff", "Graham Boxall", "CI", "Graham Boxall", photo=True)
    assert f"{STAFF_DIR}/{PLACEHOLDER}" in _repo(github)
    assert github.commits[0]["deleted"] == []


def test_a_photo_another_person_still_uses_is_kept(api, github):
    shared = {"staff": [
        {"rank": "CI", "name": "A One", "image": "shared.webp"},
        {"rank": "CI", "name": "B Two", "image": "shared.webp"},
    ], "ncos": []}
    _seed(github, shared)
    _edit(api, "staff", "A One", "CI", "A One", photo=True)
    assert f"{STAFF_DIR}/shared.webp" in _repo(github)


def test_rename(api, github):
    _seed(github)
    res = _edit(api, "staff", "Ben McDonald", "CI", "Benjamin McDonald")
    assert res.json()["name"] == "Benjamin McDonald"
    assert not find_name(_stored(github)["staff"], "Ben McDonald")


def test_changing_only_the_case_of_a_name_isnt_a_clash(api, github):
    _seed(github)
    assert _edit(api, "ncos", "E Tyrell", "Sgt", "E TYRELL").status_code == 200


def test_renaming_onto_someone_else_is_409(api, github):
    _seed(github)
    assert _edit(api, "ncos", "E Tyrell", "Sgt", "t tyrell").status_code == 409
    assert github.commits == []


def test_saving_without_list_under_clears_it(api, github):
    # An empty form field arrives as "not sent", so the form leaves it out to clear it.
    _seed(github)
    res = _edit(api, "staff", "Roy Catterall (Wg Cdr Ret'd)", "CI", "Roy Catterall (Wg Cdr Ret'd)")
    assert "sortAs" not in res.json()
    assert "sortAs" not in next(p for p in _stored(github)["staff"] if p["name"].startswith("Roy"))


def test_saving_with_list_under_keeps_it(api, github):
    _seed(github)
    res = _edit(api, "staff", "Roy Catterall (Wg Cdr Ret'd)", "CI", "Roy Catterall (Wg Cdr Ret'd)", sort_as="Wg Cdr (Ret'd)")
    assert res.json()["sortAs"] == "Wg Cdr (Ret'd)"


def test_rank_and_name_are_required(api, github):
    _seed(github)
    res = api.patch("/website-people/ncos/H Boxall", headers=STAFF, data={"rank": "Sgt"})
    assert res.status_code == 422 and github.commits == []


def test_names_are_matched_ignoring_case(api, github):
    _seed(github)
    assert _edit(api, "ncos", "m beverley", "CWO", "M Beverley").status_code == 200


@pytest.mark.parametrize("rank,name", [("Sqn Ldr", "H Boxall"), ("Cpl", "  "), ("Cpl", "A/B"), ("Cpl", "x" * 61)])
def test_bad_edits_are_rejected(api, github, rank, name):
    _seed(github)
    assert _edit(api, "ncos", "H Boxall", rank, name).status_code == 400
    assert github.commits == []


def test_editing_someone_not_on_the_website_is_404(api, github):
    _seed(github)
    assert _edit(api, "ncos", "Nobody", "Cpl", "Nobody").status_code == 404
    # Staff and NCOs are separate: a staff name isn't found among the NCOs.
    assert _edit(api, "ncos", "Ben McDonald", "Cpl", "Ben McDonald").status_code == 404
    assert github.commits == []


def test_a_bad_new_photo_changes_nothing(api, github):
    _seed(github)
    res = _edit(api, "ncos", "H Boxall", "Sgt", "H Boxall", photo=True, content=b"not an image")
    assert res.status_code == 400 and github.commits == []


# ── remove ────────────────────────────────────────────────────────────────────

def test_remove_takes_the_entry_and_the_photo(api, github):
    _seed(github)
    res = api.delete("/website-people/ncos/H Boxall", headers=STAFF)
    assert res.status_code == 200
    assert not find_name(_stored(github)["ncos"], "H Boxall")
    assert github.commits[0]["deleted"] == [f"{NCO_DIR}/Cdt Boxall.webp"]
    assert github.commits[0]["message"] == "Remove Cpl H Boxall from the website"


def test_removing_someone_on_the_placeholder_keeps_the_placeholder(api, github):
    _seed(github)
    api.delete("/website-people/staff/Graham Boxall", headers=STAFF)
    assert github.commits[0]["deleted"] == []
    assert f"{STAFF_DIR}/{PLACEHOLDER}" in _repo(github)


def test_removing_the_last_person_leaves_an_empty_list(api, github):
    _seed(github, {"staff": [], "ncos": [{"rank": "Cpl", "name": "S Okon", "image": "a.webp"}]})
    api.delete("/website-people/ncos/S Okon", headers=STAFF)
    assert _stored(github)["ncos"] == []


def test_remove_unknown_person_is_404(api, github):
    _seed(github)
    # Only people listed in the JSON can be removed, so a crafted name can't
    # delete anything else from the repo.
    assert api.delete("/website-people/ncos/..%2Fpeople.json", headers=STAFF).status_code == 404
    assert api.delete("/website-people/ncos/Nobody", headers=STAFF).status_code == 404
    assert github.commits == []


def test_github_failing_on_remove_keeps_them(api, github):
    _seed(github)
    github.fail["update_ref"] = 500
    assert api.delete("/website-people/ncos/H Boxall", headers=STAFF).status_code == 500
    assert _stored(github) == SEED


# ── the website itself ────────────────────────────────────────────────────────

def test_rank_lists_match_the_website(tmp_path):
    """The ranks are pinned to the website's people.js. When the website repo is
    checked out beside this one (it is in a cloud session and on the dev
    machine), check they still agree; CI doesn't have it and skips."""
    from pathlib import Path

    people_js = Path(__file__).resolve().parents[3] / "cadet-website" / "src" / "data" / "people.js"
    if not people_js.exists():
        pytest.skip("cadet-website isn't checked out beside this repo")
    text = people_js.read_text()
    for rank in STAFF_RANKS + NCO_RANKS:
        assert f'"{rank}"' in text, rank
