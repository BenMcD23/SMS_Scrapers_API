"""Things that publish to GitHub or call out to the web: newsletters, the
monthly programme, the mileage lookup and the claim forms, plus the owner-only
backup endpoints. Every outbound call goes to a fake (see conftest)."""

import io
import json
import re
import zipfile

import httpx
import pytest

import routers.backups as backups
import routers.programme as programme
from core.config import GITHUB_REPO, NEWSLETTER_JSON_PATH, NEWSLETTER_REPO
from database.models import User, UserProfile

STAFF = {"Authorization": "Bearer staff"}
OWNER = {"Authorization": "Bearer owner"}
PDF = b"%PDF-1.4 newsletter"


def _seed_newsletters(github, issues):
    entries = [{"id": f"issue-{i}", "issue": i, "title": f"Issue {i}"} for i in issues]
    github.files[NEWSLETTER_REPO] = {NEWSLETTER_JSON_PATH: json.dumps(entries).encode()}
    for i in issues:
        github.files[NEWSLETTER_REPO][f"317_newsletter/public/newsletters/issue-{i}.pdf"] = PDF + str(i).encode()
    github.files[GITHUB_REPO] = {f"public/newsletters/issue-{i}.pdf": b"%PDF old" for i in issues}


def _stored(github):
    return json.loads(github.files[NEWSLETTER_REPO][NEWSLETTER_JSON_PATH])


# ── access ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/newsletters"), ("post", "/upload-newsletter"), ("put", "/newsletters/1"),
    ("delete", "/newsletters/1"), ("post", "/update-programme"),
    ("post", "/form-generators/calculate-mileage"), ("post", "/form-generators/f1771e"),
    ("post", "/form-generators/htd"),
])
def test_staff_only(api, method, path):
    call = getattr(api, method)
    res = call(path, headers=api.as_("nco")) if method in ("get", "delete") else call(path, headers=api.as_("nco"))
    assert res.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/backups"), ("post", "/backups/run"), ("get", "/backups/x/preview"), ("post", "/backups/x/restore"),
])
def test_backups_are_owner_only(api, method, path):
    assert getattr(api, method)(path, headers=STAFF).status_code == 403


# ── newsletters ───────────────────────────────────────────────────────────────

def test_list_newsletters_newest_first(api, github):
    _seed_newsletters(github, [1, 3, 2])
    assert [n["issue"] for n in api.get("/newsletters", headers=STAFF).json()] == [3, 2, 1]


def test_list_when_the_json_is_missing_explains_why(api, github):
    res = api.get("/newsletters", headers=STAFF)
    assert res.status_code == 500 and "Make sure the file is pushed" in res.json()["detail"]


def _upload(api, issue, content=PDF, mime="application/pdf"):
    return api.post("/upload-newsletter", headers=STAFF,
                    files={"file": ("n.pdf", content, mime)},
                    data={"title": "Summer", "date": "2026-07", "issue": str(issue), "description": "Camp news"})


def test_upload_new_current_issue_mirrors_to_the_website(api, github):
    _seed_newsletters(github, [1, 2])
    res = _upload(api, 3)
    assert res.json() == {"status": "success", "id": "issue-3", "filename": "issue-3.pdf"}

    stored = _stored(github)
    assert [n["issue"] for n in stored] == [3, 2, 1]  # written highest first
    assert stored[0]["pdfPath"] == "/newsletters/issue-3.pdf" and stored[0]["coverColor"] == "#1F2E4A"
    assert github.files[NEWSLETTER_REPO]["317_newsletter/public/newsletters/issue-3.pdf"] == PDF

    site = github.files[GITHUB_REPO]
    assert set(site) == {"public/newsletters/issue-3.pdf", "src/data/currentNewsletter.json"}
    assert json.loads(site["src/data/currentNewsletter.json"])["issue"] == 3
    assert [c["repo"] for c in github.commits] == [NEWSLETTER_REPO, GITHUB_REPO]


def test_uploading_an_older_issue_keeps_the_current_one_on_the_site(api, github):
    _seed_newsletters(github, [5])
    _upload(api, 4)
    site = github.files[GITHUB_REPO]
    assert json.loads(site["src/data/currentNewsletter.json"])["issue"] == 5
    # The current PDF is re-fetched from the newsletter repo, not the upload.
    assert site["public/newsletters/issue-5.pdf"] == PDF + b"5"


def test_upload_rejects_duplicates_and_non_pdfs(api, github):
    _seed_newsletters(github, [1])
    assert _upload(api, 1).status_code == 409
    assert _upload(api, 2, mime="image/png").status_code == 400
    assert github.commits == []


def test_upload_needs_every_field(api, github):
    res = api.post("/upload-newsletter", headers=STAFF, files={"file": ("n.pdf", PDF, "application/pdf")},
                   data={"title": "x"})
    assert res.status_code == 422


def test_edit_metadata_only_and_replace_pdf(api, github):
    _seed_newsletters(github, [1, 2])
    assert api.put("/newsletters/1", headers=STAFF, data={"title": "Renamed"}).json()["status"] == "success"
    stored = {n["issue"]: n for n in _stored(github)}
    assert stored[1]["title"] == "Renamed" and stored[2]["title"] == "Issue 2"

    api.put("/newsletters/2", headers=STAFF, data={"cover_color": "#000"},
            files={"file": ("new.pdf", b"%PDF new", "application/pdf")})
    assert github.files[NEWSLETTER_REPO]["317_newsletter/public/newsletters/issue-2.pdf"] == b"%PDF new"
    assert github.files[GITHUB_REPO]["public/newsletters/issue-2.pdf"] == b"%PDF new"


def test_edit_errors(api, github):
    _seed_newsletters(github, [1])
    assert api.put("/newsletters/9", headers=STAFF, data={"title": "x"}).status_code == 404
    res = api.put("/newsletters/1", headers=STAFF, files={"file": ("x.png", b"png", "image/png")})
    assert res.status_code == 400


def test_delete_current_promotes_the_next(api, github):
    _seed_newsletters(github, [1, 2])
    assert api.delete("/newsletters/2", headers=STAFF).status_code == 200
    assert [n["issue"] for n in _stored(github)] == [1]
    assert "317_newsletter/public/newsletters/issue-2.pdf" not in github.files[NEWSLETTER_REPO]
    assert json.loads(github.files[GITHUB_REPO]["src/data/currentNewsletter.json"])["issue"] == 1
    assert api.delete("/newsletters/2", headers=STAFF).status_code == 404


def test_deleting_the_last_issue_leaves_the_website_alone(api, github):
    _seed_newsletters(github, [1])
    api.delete("/newsletters/1", headers=STAFF)
    assert [c["repo"] for c in github.commits] == [NEWSLETTER_REPO]


def test_missing_current_pdf_is_a_clear_error(api, github):
    _seed_newsletters(github, [5])
    del github.files[NEWSLETTER_REPO]["317_newsletter/public/newsletters/issue-5.pdf"]
    res = _upload(api, 4)
    assert res.status_code == 500 and "issue-5.pdf" in res.json()["detail"]


@pytest.mark.parametrize("step", ["ref", "base", "blob", "tree", "commit", "update_ref"])
def test_any_failed_github_step_is_reported(api, github, step):
    _seed_newsletters(github, [1])
    github.fail[step] = 422
    res = api.put("/newsletters/1", headers=STAFF, data={"title": "x"})
    assert res.status_code == 500
    assert github.commits == []


# ── programme ─────────────────────────────────────────────────────────────────

class FakePage:
    def save(self, buf, format):
        buf.write(b"RIFFwebp")


def _programme_web(github, script=None, pdf=PDF):
    jsx = b'<a href="/programme/08_26_programme.pdf">'
    github.files[GITHUB_REPO] = {
        "src/pages/programme.jsx": jsx,
        "public/programme/08_26_programme.pdf": b"%PDF old",
        "public/programme/notes.txt": b"keep me",
    }

    def handler(request):
        host = request.url.host
        if host == "script.google.com":
            return script or httpx.Response(200, json={"downloadUrl": "https://drive.example/file.pdf"})
        if host == "drive.example":
            return httpx.Response(200, content=pdf)
        return github(request)

    return handler


def test_programme_update_commits_pdf_images_and_link(api, http, github, monkeypatch):
    monkeypatch.setattr(programme, "convert_from_bytes", lambda b, dpi, fmt: [FakePage(), FakePage()])
    http.handler = _programme_web(github)
    res = api.post("/update-programme?month=9&year=2026", headers=STAFF)
    assert res.status_code == 200, res.text
    assert res.json()["pdf"] == "09_26_programme.pdf" and res.json()["pages_converted"] == 2

    site = github.files[GITHUB_REPO]
    assert site["src/pages/programme.jsx"] == b'<a href="/programme/09_26_programme.pdf">'
    assert site["public/programme/09_26_programme.pdf"] == PDF
    assert "public/programme/08_26_programme.pdf" not in site
    assert site["public/programme/notes.txt"] == b"keep me"
    assert site["src/assets/programme/programme.webp"] == b"RIFFwebp"
    # The Apps Script was asked for the right month.
    script_call = next(c for c in http.calls if c.url.host == "script.google.com")
    assert script_call.url.params["month"] == "9" and script_call.url.params["year"] == "2026"


def test_programme_single_page_reuses_it_for_rooms(api, http, github, monkeypatch):
    monkeypatch.setattr(programme, "convert_from_bytes", lambda b, dpi, fmt: [FakePage()])
    http.handler = _programme_web(github)
    assert api.post("/update-programme?month=1&year=2027", headers=STAFF).status_code == 200
    assert github.files[GITHUB_REPO]["src/assets/programme/rooms.webp"] == b"RIFFwebp"


@pytest.mark.parametrize("script,pdf,pages,detail", [
    (httpx.Response(500), PDF, 1, "Apps Script"),
    (httpx.Response(200, json={"error": "No file for month"}), PDF, 1, "No file for month"),
    (None, b"<html>login</html>", 1, "download PDF"),
    (None, PDF, 0, "no pages"),
])
def test_programme_failures(api, http, github, monkeypatch, script, pdf, pages, detail):
    monkeypatch.setattr(programme, "convert_from_bytes", lambda b, dpi, fmt: [FakePage()] * pages)
    http.handler = _programme_web(github, script=script, pdf=pdf)
    res = api.post("/update-programme?month=1&year=2027", headers=STAFF)
    assert res.status_code in (500, 502)
    assert detail in res.json()["detail"]
    assert github.commits == []


def test_programme_conversion_error(api, http, github, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("poppler missing")

    monkeypatch.setattr(programme, "convert_from_bytes", boom)
    http.handler = _programme_web(github)
    res = api.post("/update-programme", headers=STAFF)
    assert res.status_code == 500 and "poppler missing" in res.json()["detail"]


def test_programme_missing_jsx(api, http, github, monkeypatch):
    monkeypatch.setattr(programme, "convert_from_bytes", lambda b, dpi, fmt: [FakePage()])
    http.handler = _programme_web(github)
    del github.files[GITHUB_REPO]["src/pages/programme.jsx"]
    assert api.post("/update-programme", headers=STAFF).status_code == 500


# ── mileage ───────────────────────────────────────────────────────────────────

def _maps(geocode=None, route=None):
    def handler(request):
        if request.url.host == "nominatim.openstreetmap.org":
            q = request.url.params["q"]
            hits = geocode(q) if geocode else [{"lon": "-1.5", "lat": "53.8"}]
            return httpx.Response(200, json=hits)
        return httpx.Response(200, json=route or {"code": "Ok", "routes": [{"distance": 16093.44}]})
    return handler


def test_mileage(api, http):
    http.handler = _maps()
    res = api.post("/form-generators/calculate-mileage", headers=STAFF,
                   json={"from_address": "Leeds", "to_address": "York"})
    assert res.json() == {"miles": 10.0}
    assert all(c.headers["user-agent"] == "317-SMS-Site/1.0" for c in http.calls if "nominatim" in str(c.url))


def test_mileage_unknown_address_and_no_route(api, http):
    http.handler = _maps(geocode=lambda q: [] if q == "Atlantis" else [{"lon": "0", "lat": "0"}])
    res = api.post("/form-generators/calculate-mileage", headers=STAFF,
                   json={"from_address": "Atlantis", "to_address": "York"})
    assert res.status_code == 422 and "Atlantis" in res.json()["detail"]

    http.handler = _maps(route={"code": "NoRoute", "routes": []})
    res = api.post("/form-generators/calculate-mileage", headers=STAFF,
                   json={"from_address": "Leeds", "to_address": "Paris"})
    assert res.status_code == 422


# ── claim forms ───────────────────────────────────────────────────────────────

JOURNEY = {"dateOfJourney": "2026-03-04", "timeOfDeparture": "18:00", "timeOfArrival": "18:30",
           "from": "Home\nStreet", "to": "Sqn HQ", "natureOfActivity": "Parade", "nameRankNo": "Sgt S Staff",
           "gbtHotelRef": "", "miscExpenses": "", "numberOfPassengers": "0", "method": "Car",
           "mileageClaimed": "10"}


def _doc_text(content: bytes) -> str:
    """All the text in a .docx, nested tables and text boxes included."""
    xml = zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml").decode()
    return re.sub(r"<[^>]+>", "", xml)


def test_f1771e_uses_the_profile(api, db):
    api.get("/settings/user-profile", headers=STAFF)
    user = db.query(User).one()
    db.add(UserProfile(user_id=user.id, rank="Sgt", surname="Staff Member", jpa_number="12 34", car_reg="AB12"))
    db.commit()
    res = api.post("/form-generators/f1771e", headers=STAFF, json={"journeys": [JOURNEY, {**JOURNEY,
                                                                                          "dateOfJourney": "04/03"}]})
    assert res.status_code == 200
    assert "-F1771-STAFF-MEMBER-1234-OSP.docx" in res.headers["content-disposition"]
    text = _doc_text(res.content)
    assert "04/03/26" in text and "Home, Street" in text


def test_f1771e_without_a_profile(api):
    res = api.post("/form-generators/f1771e", headers=STAFF, json={"journeys": []})
    assert res.status_code == 200
    assert "-F1771-UNKNOWN-UNKNOWN-OSP.docx" in res.headers["content-disposition"]


def test_f1771e_accented_surname_downloads(api, db):
    api.get("/settings/user-profile", headers=STAFF)
    db.add(UserProfile(user_id=db.query(User).one().id, surname="Müller"))
    db.commit()
    res = api.post("/form-generators/f1771e", headers=STAFF, json={"journeys": []})
    assert res.status_code == 200 and "filename*=UTF-8''" in res.headers["content-disposition"]


def test_f1771e_validation(api):
    bad = {**JOURNEY}
    del bad["from"]
    assert api.post("/form-generators/f1771e", headers=STAFF, json={"journeys": [bad]}).status_code == 422


def test_htd_fills_the_form(api):
    res = api.post("/form-generators/htd", headers=STAFF, json={
        "rank": "Sgt", "initials": "S", "surname": "o'brien", "service_number": "30-12-34-56-789",
        "bank_last3": "1a2b3c4", "distance": 10, "date": "01/04/2026",
        "months": [{"label": "01/26", "journeys": 8}, {"label": "02/26", "journeys": 6}]})
    assert res.status_code == 200
    assert 'filename="HTD_O\'BRIEN.docx"' in res.headers["content-disposition"]
    text = _doc_text(res.content)
    # 10 miles x 25p + 7% = 2.68 a journey; 8 and 6 journeys = 21.44 + 16.08.
    assert "O'BRIEN" in text and "21.44" in text and "16.08" in text and "37.52" in text
    # Template placeholders are all filled.
    assert not re.search(r"\{\{?\s*\w+\s*\}", text), "unfilled placeholder left in the form"


def test_htd_with_nothing_filled_in(api):
    res = api.post("/form-generators/htd", headers=STAFF, json={})
    assert res.status_code == 200 and 'filename="HTD_UNKNOWN.docx"' in res.headers["content-disposition"]


def test_htd_accented_surname_downloads(api):
    res = api.post("/form-generators/htd", headers=STAFF, json={"surname": "Ñúñez"})
    assert res.status_code == 200


def test_htd_more_than_six_months_is_capped_not_an_error(api):
    months = [{"label": f"{m:02d}/26", "journeys": 1} for m in range(1, 9)]
    assert api.post("/form-generators/htd", headers=STAFF, json={"months": months}).status_code == 200


# ── backups ───────────────────────────────────────────────────────────────────

def test_backups_pass_through_and_wrap_failures(api, monkeypatch):
    monkeypatch.setattr(backups.db_backup, "list_backups", lambda: [{"id": "f1"}])
    monkeypatch.setattr(backups.db_backup, "run_db_backup", lambda: {"ok": True})
    monkeypatch.setattr(backups.db_backup, "preview_backup", lambda fid: {"file": fid})
    monkeypatch.setattr(backups.db_backup, "restore_backup", lambda fid: {"restored": fid})
    body = api.get("/backups", headers=OWNER).json()
    assert body["backups"] == [{"id": "f1"}] and "retention" in body
    assert api.post("/backups/run", headers=OWNER).json() == {"ok": True}
    assert api.get("/backups/f1/preview", headers=OWNER).json() == {"file": "f1"}
    assert api.post("/backups/f1/restore", headers=OWNER).json() == {"restored": "f1"}

    def boom(*a):
        raise RuntimeError("Drive quota")

    for name in ("list_backups", "run_db_backup", "preview_backup", "restore_backup"):
        monkeypatch.setattr(backups.db_backup, name, boom)
    assert api.get("/backups", headers=OWNER).status_code == 502
    for method, path in (("post", "/backups/run"), ("get", "/backups/f1/preview"), ("post", "/backups/f1/restore")):
        res = getattr(api, method)(path, headers=OWNER)
        assert res.status_code == 500 and "Drive quota" in res.json()["detail"]
