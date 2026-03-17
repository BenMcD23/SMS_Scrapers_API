import os
import threading
import asyncio
import json
import time
import base64
import httpx
import io
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import partial

from typing import AsyncGenerator
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, Header, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy.orm import Session
from sqlalchemy import or_

from database.create_db import init_db
from database.database import engine
from database.models import Event317, AllEvent, CadetEvent, User, BaderCredentials, UserSignature, AssessmentSheet, Cadet, CadetQualification, StatsSnapshot

from scripts.ji_ao_generator import generate_ji, generate_ao
from scripts.scraper_calls import *

from assessment_builders.leadership import generate_leadership_pdf, process_assessment_data

from utils.crypto import encrypt_password

from google.oauth2 import id_token, service_account
from google.auth.transport import requests
from googleapiclient.discovery import build as google_build

from pydantic import BaseModel, EmailStr
from typing import Optional

from functools import partial

class UploadQualificationsRequest(BaseModel):
    assessment_ids: list[int]

class CadetPatch(BaseModel):
    email: Optional[str] = None

class AssessorNamePatch(BaseModel):
    assessor_name: str


init_db()

app = FastAPI()

# Allow Next.js to talk to FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://sms.317atc.co.uk", "https://317-sms-site.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    allow_credentials=True,
)

GOOGLE_CLIENT_ID = "490734276503-9s44s89sdhgct8ismqnsm7s1d4v6e4uv.apps.googleusercontent.com"

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "BenMcD23/cadet-website"
GITHUB_BRANCH = "master"
PROGRAMME_APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbyqQbEdYxu53ARNzfcxdcm9cRieRVBC3cJ_TtdGVbpPQaMfpzD3XkreSmNSnJaHe1pM/exec"

# ===============================
# GLOBAL SCRAPER STATE
# ===============================

scraper_messages = []
scraper_lock = threading.Lock()

scraper_running = False
scraper_state_lock = threading.Lock()

current_scraper_user = None
current_scraper_name = None

# ===============================
# DATABASE
# ===============================

def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()

def get_or_create_user(db: Session, google_id: str, email: str) -> User:
    """
    Fetch the User row by google_id, creating one if it doesn't exist yet.
    This means users no longer need to save credentials before doing anything.
    """
    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        user = User(google_id=google_id, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

STAFF_GROUP = "staff@317atc.co.uk"
NCO_GROUP = "NCOs@317atc.co.uk"
_SA_EMAIL = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
_SA_PRIVATE_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", "").replace("\\n", "\n").strip('"')
_IMPERSONATE_EMAIL = os.getenv("GOOGLE_IMPERSONATE_EMAIL", "ci.mcdonald@317atc.co.uk")

_role_cache: dict = {}
_role_cache_lock = threading.Lock()

def _fetch_user_role(email: str) -> str | None:
    if not _SA_EMAIL or not _SA_PRIVATE_KEY:
        return None
    try:
        creds = service_account.Credentials.from_service_account_info(
            {
                "type": "service_account",
                "client_email": _SA_EMAIL,
                "private_key": _SA_PRIVATE_KEY,
                "token_uri": "https://oauth2.googleapis.com/token",
                "private_key_id": "",
                "client_id": "",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            },
            scopes=["https://www.googleapis.com/auth/admin.directory.group.member.readonly"],
        ).with_subject(_IMPERSONATE_EMAIL)
        admin = google_build("admin", "directory_v1", credentials=creds, cache_discovery=False)
        for group, role in [(STAFF_GROUP, "staff"), (NCO_GROUP, "nco")]:
            try:
                admin.members().get(groupKey=group, memberKey=email).execute()
                return role
            except Exception:
                continue
        return None
    except Exception as e:
        print(f"[_fetch_user_role] error: {e}")
        return None

def get_user_role(email: str) -> str | None:
    with _role_cache_lock:
        cached = _role_cache.get(email)
        if cached and time.time() < cached[1]:
            return cached[0]
    role = _fetch_user_role(email)
    with _role_cache_lock:
        _role_cache[email] = (role, time.time() + 300)
    return role

def verify_token(authorization: str) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        token = authorization.split(" ")[1]
        return id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Token")

def verify_token_staff_only(authorization: str) -> dict:
    idinfo = verify_token(authorization)
    if get_user_role(idinfo["email"]) != "staff":
        raise HTTPException(status_code=403, detail="Staff access required")
    return idinfo

# ===============================
# HEALTH CHECK
# ===============================

@app.get("/health")
def health_check(authorization: str = Header(default=None)):
    idinfo = verify_token(authorization)
    return {"ok": True, "email": idinfo["email"]}

# ===============================
# SCRAPER LOGIC
# ===============================

def run_scraper_task(scraper_func, user_id: int):
    global scraper_running, current_scraper_user, current_scraper_name
    db = Session(engine)
    stop_event = threading.Event()
    
    def monitor_timeout():
        time.sleep(900)
        if scraper_running:
            stop_event.set()

    monitor_thread = threading.Thread(target=monitor_timeout, daemon=True)
    monitor_thread.start()

    try:
        scraper_func(scraper_messages, scraper_lock, user_id, db, stop_event)

        with scraper_lock:
            if stop_event.is_set():
                scraper_messages.append(json.dumps({"type": "error", "value": "Scraper timed out."}))
            else:
                # Auto-save a stats snapshot after the cadet-quali scraper succeeds
                if current_scraper_name == "cadet-quali":
                    try:
                        stats = compute_stats(db)
                        snapshot = StatsSnapshot(captured_at=datetime.now(), data=stats)
                        db.add(snapshot)
                        db.commit()
                    except Exception as snap_err:
                        print(f"[stats snapshot] failed: {snap_err}")
                scraper_messages.append(json.dumps({"type": "status", "value": "done"}))
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(json.dumps({"type": "error", "value": f"Crash: {str(e)}"}))
    finally:
        db.close()
        with scraper_state_lock:
            scraper_running = False
        current_scraper_user = None
        current_scraper_name = None

@app.get("/run-scraper/{name}")
async def start_scraper(
    name: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    global scraper_running, current_scraper_user, current_scraper_name

    idinfo = verify_token_staff_only(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])

    # Scraper specifically needs bader credentials to be set
    if not user.bader_credentials:
        raise HTTPException(
            status_code=400,
            detail="Bader credentials not saved. Please go to Settings first.",
        )

    scraper_map = {
        "cadet-quali": info_and_quali_scraper,
        "cadet-event": cadet_event_scraper,
        "317-event": event_317_scraper,
        "medical": medical_scraper,
    }

    if name not in scraper_map:
        raise HTTPException(status_code=404, detail="Scraper not found")

    with scraper_state_lock:
        if scraper_running:
            raise HTTPException(status_code=400, detail="Scraper already running")
        scraper_running = True

    current_scraper_user = idinfo.get("email")
    current_scraper_name = name

    with scraper_lock:
        scraper_messages.clear()
        scraper_messages.append(
            json.dumps({
                "type": "status",
                "value": "running",
                "started_by": current_scraper_user,
                "scraper_name": current_scraper_name,
            })
        )

    background_tasks.add_task(run_scraper_task, scraper_map[name], user.id)
    return {"status": "started"}


# ===============================
# SETTINGS
# ===============================

@app.post("/save-credentials")
async def save_credentials(
    data: dict,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    idinfo = verify_token_staff_only(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])

    # Upsert BaderCredentials
    creds = user.bader_credentials
    if not creds:
        creds = BaderCredentials(user_id=user.id)
        db.add(creds)

    creds.role_username = data.get("role_user")
    creds.role_password = encrypt_password(data.get("role_pass"))
    creds.personal_username = data.get("pers_user")
    creds.personal_password = encrypt_password(data.get("pers_pass"))

    db.commit()
    return {"status": "success", "message": f"Settings saved for {user.email}"}

@app.post("/save-signature")
async def save_signature(
    file: UploadFile = File(...),
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    idinfo = verify_token(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])

    if file.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(status_code=400, detail="Only PNG or JPEG images are accepted")

    image_bytes = await file.read()
    if len(image_bytes) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Signature image must be under 2 MB")

    # Upsert UserSignature
    sig = user.signature
    if not sig:
        sig = UserSignature(user_id=user.id)
        db.add(sig)

    sig.image_data = image_bytes
    sig.mime_type = file.content_type

    db.commit()
    return {"status": "success", "message": "Signature saved"}


@app.get("/get-signature")
async def get_signature(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    idinfo = verify_token(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])

    if not user.signature:
        raise HTTPException(status_code=404, detail="No signature saved")

    return StreamingResponse(
        io.BytesIO(user.signature.image_data),
        media_type=user.signature.mime_type,
        headers={"Cache-Control": "no-cache"},
    )


@app.delete("/delete-signature")
async def delete_signature(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    idinfo = verify_token(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])

    if not user.signature:
        raise HTTPException(status_code=404, detail="No signature to delete")

    db.delete(user.signature)
    db.commit()
    return {"status": "success", "message": "Signature deleted"}


@app.get("/settings/assessor-name")
async def get_assessor_name(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    idinfo = verify_token(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])
    return {"assessor_name": user.assessor_name or ""}

@app.patch("/settings/assessor-name")
async def update_assessor_name(
    data: AssessorNamePatch,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    idinfo = verify_token(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])
    user.assessor_name = data.assessor_name.strip()
    db.commit()
    return {"status": "success", "assessor_name": user.assessor_name}

# ===============================
# SERVER SENT EVENTS
# ===============================

def safe_parse(m: str) -> dict | None:
    try:
        return json.loads(m) if m else None
    except json.JSONDecodeError:
        return None

@app.get("/scraper-stream")
async def scraper_stream(
    authorization: str = Header(None),
    token: str = Query(None),  # fallback for EventSource
):
    # Accept token from either header or query param
    auth = authorization or (f"Bearer {token}" if token else None)
    verify_token(auth)

    async def event_generator():
        # Immediately replay current state to any tab that connects late
        with scraper_lock:
            if scraper_running and current_scraper_user:
                catchup = json.dumps({
                    "type": "status",
                    "value": "running",
                    "started_by": current_scraper_user,
                    "scraper_name": current_scraper_name,
                })
                yield f"data: {catchup}\n\n"

            # Also replay any log messages already generated
            last_idx = len(scraper_messages)
            for msg in scraper_messages:
                parsed = safe_parse(msg)
                if parsed and parsed.get("type") == "log":
                    yield f"data: {msg}\n\n"

                try:
                    parsed = json.loads(msg)
                    # Only replay log lines, not the status events (already sent above)
                    if parsed.get("type") == "log":
                        yield f"data: {msg}\n\n"
                except:
                    pass

        #  Never close the stream — just keep polling forever
        while True:
            await asyncio.sleep(0.5)

            with scraper_lock:
                current_len = len(scraper_messages)

            if last_idx < current_len:
                with scraper_lock:
                    new_messages = scraper_messages[last_idx:]

                for msg in new_messages:
                    yield f"data: {msg}\n\n"

                last_idx = current_len

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/scraper-status")
async def scraper_status(authorization: str = Header(None)):
    verify_token(authorization)
    with scraper_lock:
        logs = [
            json.loads(m).get("value", m)
            for m in scraper_messages
            if safe_parse(m) and safe_parse(m).get("type") == "log"
        ]
    return {
        "running": scraper_running,
        "started_by": current_scraper_user,
        "scraper_name": current_scraper_name,
        "recent_logs": logs[-50:],
    }


# ===============================
# DATA ENDPOINTS
# ===============================

@app.get("/events")
async def get_events(db: Session = Depends(get_db)):
    return db.query(Event317).all()


@app.get("/cadet-events")
async def get_cadet_events(
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)
    events = db.query(AllEvent).all()
    return [
        {
            "id": e.id,
            "title": e.title,
            "cadet_count": len(e.cadet_events),
            "cadets": [
                {
                    "cin":        ce.cadet.cin,
                    "first_name": ce.cadet.first_name,
                    "last_name":  ce.cadet.last_name,
                    "rank":       ce.cadet.rank,
                    "flight":     ce.cadet.flight,
                }
                for ce in e.cadet_events
                if ce.cadet
            ],
        }
        for e in events
    ]


@app.get("/generate-doc/{event_id}/{action}")
async def generate_doc_endpoint(event_id: int, action: str, db: Session = Depends(get_db)):
    event = db.query(Event317).filter(Event317.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    try:
        if action == "ji":
            file_buffer = generate_ji(event)
            filename = f"JI_{event.reference}.docx"
        elif action == "ao":
            file_buffer = generate_ao(event)
            filename = f"AO_{event.reference}.docx"
        else:
            raise HTTPException(status_code=400, detail="Invalid action")

        return StreamingResponse(
            file_buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        print(f"Error generating document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===============================
# PROGRAMME ENDPOINTS
# ===============================

import io
from pdf2image import convert_from_bytes
async def push_to_github(client: httpx.AsyncClient, repo_path: str, file_bytes: bytes, commit_message: str):
    github_headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_path}"
    encoded = base64.b64encode(file_bytes).decode("utf-8")

    get_resp = await client.get(api_url, headers=github_headers, params={"ref": GITHUB_BRANCH})
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

    payload = {"message": commit_message, "content": encoded, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha

    put_resp = await client.put(api_url, headers=github_headers, json=payload, timeout=120.0)
    if put_resp.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail=f"GitHub push failed for {repo_path}: {put_resp.text}")


async def delete_old_programme_pdfs(client: httpx.AsyncClient, exclude_filename: str):
    github_headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    folder_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/public/programme"
    resp = await client.get(folder_url, headers=github_headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code != 200:
        return

    files = resp.json()
    if not isinstance(files, list):
        return

    for f in files:
        name = f.get("name", "")
        if name.endswith("_programme.pdf") and name != exclude_filename:
            delete_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/public/programme/{name}"
            await client.request(
                "DELETE",
                delete_url,
                headers=github_headers,
                content=json.dumps({
                    "message": f"Remove old programme PDF {name}",
                    "sha": f["sha"],
                    "branch": GITHUB_BRANCH,
                }),
                timeout=60.0,
            )


@app.post("/update-programme")
async def update_programme(
    month: int = None,
    year: int = None,
    authorization: str = Header(None)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        token = authorization.split(" ")[1]
        id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Token")

    now = datetime.now()
    month = month or now.month
    year = year or now.year
    month_str = str(month).zfill(2)
    short_year = str(year)[-2:]
    pdf_filename = f"{month_str}_{short_year}_programme.pdf"

    # Fetch PDF URL from Apps Script
    async with httpx.AsyncClient() as client:
        script_url = f"{PROGRAMME_APPS_SCRIPT_URL}?month={month}&year={year}"
        script_resp = await client.get(script_url, timeout=60, follow_redirects=True)

        if script_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to reach Apps Script")

        data = script_resp.json()
        if "error" in data:
            raise HTTPException(status_code=502, detail=f"Apps Script error: {data['error']}")

        download_url = data["downloadUrl"]

        # Download the actual PDF
        pdf_resp = await client.get(download_url, timeout=30, follow_redirects=True)
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

    # Update Programme.jsx to point at new PDF filename
    import re
    jsx_path = "src/pages/programme.jsx"
    github_headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        jsx_api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{jsx_path}"
        jsx_get = await client.get(jsx_api_url, headers=github_headers, params={"ref": GITHUB_BRANCH})
        if jsx_get.status_code != 200:
            raise HTTPException(status_code=500, detail="Could not fetch Programme.jsx from GitHub")

        jsx_data = jsx_get.json()
        jsx_content = base64.b64decode(jsx_data["content"]).decode("utf-8")
        jsx_sha = jsx_data["sha"]

        updated_jsx = re.sub(
            r'/programme/\d{2}_\d{2}_programme\.pdf',
            f'/programme/{pdf_filename}',
            jsx_content
        )

        jsx_encoded = base64.b64encode(updated_jsx.encode("utf-8")).decode("utf-8")
        jsx_put = await client.put(jsx_api_url, headers=github_headers, json={
            "message": f"Update programme PDF link to {pdf_filename}",
            "content": jsx_encoded,
            "branch": GITHUB_BRANCH,
            "sha": jsx_sha,
        })
        if jsx_put.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"Failed to update Programme.jsx: {jsx_put.text}")

        # Delete old programme PDFs before pushing new one
        await delete_old_programme_pdfs(client, exclude_filename=pdf_filename)

        # Push all 3 files
        await push_to_github(client, "src/assets/programme/programme.webp", page1_bytes, f"Update programme page 1 ({pdf_filename})")
        await push_to_github(client, "src/assets/programme/rooms.webp",     page2_bytes, f"Update programme page 2 ({pdf_filename})")
        await push_to_github(client, f"public/programme/{pdf_filename}",    pdf_bytes,   f"Add programme PDF {pdf_filename}")

    return {
        "status": "success",
        "message": f"Programme updated for {month_str}/{short_year}",
        "pdf": pdf_filename,
        "pages_converted": len(pages),
    }

# ===============================
# ASSESSMENT ENDPOINTS
# ===============================

@app.get("/cadets/search")
async def search_cadets(
    q: str = "",
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)

    if not q or len(q.strip()) < 2:
        return []

    search = f"%{q.strip()}%"
    cadets = db.query(Cadet).filter(
        or_(
            Cadet.first_name.ilike(search),
            Cadet.last_name.ilike(search),
            (Cadet.first_name + " " + Cadet.last_name).ilike(search),
        )
    ).order_by(Cadet.last_name, Cadet.first_name).limit(10).all()

    return [
        {
            "cin":        c.cin,
            "first_name": c.first_name,
            "last_name":  c.last_name,
            "rank":       c.rank,
            "flight":     c.flight,
        }
        for c in cadets
    ]


# ── Leadership assessment endpoint ────────────────────

@app.post("/assessments/leadership/add-assessment")
async def generate_leadership_assessment(
    data: dict,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    idinfo = verify_token(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])

    # Resolve cadet by CIN (preferred) or fall back to name search
    cadet_cin = data.get("cadet_cin")
    if cadet_cin:
        cadet = db.query(Cadet).filter(Cadet.cin == int(cadet_cin)).first()
        if not cadet:
            raise HTTPException(status_code=404, detail=f"Cadet with CIN {cadet_cin} not found.")
    else:
        cadet_name = data.get("cadet_name", "").strip()
        if not cadet_name:
            raise HTTPException(status_code=400, detail="cadet_cin or cadet_name is required.")
        cadet = db.query(Cadet).filter(
            (Cadet.first_name + " " + Cadet.last_name).ilike(cadet_name)
        ).first()
        if not cadet:
            raise HTTPException(status_code=404, detail=f"Cadet '{cadet_name}' not found.")

    processed = process_assessment_data(data)

    if user.assessor_name:
        processed["assessor_name"] = user.assessor_name

    pdf_bytes  = generate_leadership_pdf(processed)

    sheet = AssessmentSheet(
        assessment_type="Blue Leadership",
        fields={
            "scores":           processed["scores"],
            "total_score":      processed["total_score"],
            "passed":           processed["passed"],
            "exercise_no":      processed["exercise_no"],
            "exercise_name":    processed["exercise_name"],
            "assessor_name":    processed["assessor_name"],
            "date":             processed["date"],
            "debriefing_notes": processed["debriefing_notes"],
        },
        pdf_data=pdf_bytes,
        uploaded=False,
        pdf_mime_type="application/pdf",
        created_at=datetime.utcnow(),
        cadet_id=cadet.cin,
        assessor_id=user.id,
    )
    db.add(sheet)
    db.commit()
    db.refresh(sheet)

    return {"status": "success", "assessment_id": sheet.id}



# How many passed assessments are needed per type to unlock upload
UPLOAD_THRESHOLDS: dict[str, int] = {
    "Blue Leadership": 2,
    # everything else defaults to 1
}

def required_passes(assessment_type: str) -> int:
    return UPLOAD_THRESHOLDS.get(assessment_type, 1)


@app.get("/assessments/overview")
async def assessments_overview(
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)

    # Load all assessment sheets, joining cadet info
    sheets = (
        db.query(AssessmentSheet)
        .join(Cadet, AssessmentSheet.cadet_id == Cadet.cin)
        .order_by(Cadet.last_name, Cadet.first_name, AssessmentSheet.created_at)
        .all()
    )

    # Group by cadet, then by assessment_type
    from collections import defaultdict

    cadet_map: dict[int, dict] = {}

    for sheet in sheets:
        cadet = sheet.cadet
        cin = cadet.cin

        if cin not in cadet_map:
            cadet_map[cin] = {
                "cin":        cadet.cin,
                "first_name": cadet.first_name,
                "last_name":  cadet.last_name,
                "rank":       cadet.rank,
                "flight":     cadet.flight,
                "type_map":   defaultdict(list),
            }

        cadet_map[cin]["type_map"][sheet.assessment_type].append(sheet)

    result = []
    for cin, data in cadet_map.items():
        groups = []
        for atype, type_sheets in data["type_map"].items():
            assessments = [
                {
                    "id":              s.id,
                    "assessment_type": s.assessment_type,
                    "created_at":      s.created_at.isoformat() if s.created_at else None,
                    "passed":          s.fields.get("passed")        if s.fields else None,
                    "total_score":     s.fields.get("total_score")   if s.fields else None,
                    "exercise_name":   s.fields.get("exercise_name") if s.fields else None,
                    "assessor_name":   s.fields.get("assessor_name") if s.fields else None,
                }
                for s in type_sheets
            ]

            passed_count = sum(1 for a in assessments if a["passed"] is True)
            required     = required_passes(atype)
            can_upload   = passed_count >= required

            # Check if qualification already uploaded
            uploaded = any(s.uploaded for s in type_sheets)


            groups.append({
                "assessment_type":    atype,
                "assessments":        assessments,
                "passed_count":       passed_count,
                "required_to_upload": required,
                "can_upload":         can_upload,
                "uploaded":           uploaded,
            })

        result.append({
            "cin":        data["cin"],
            "first_name": data["first_name"],
            "last_name":  data["last_name"],
            "rank":       data["rank"],
            "flight":     data["flight"],
            "groups":     groups,
        })

    return result


@app.post("/assessments/{cin}/{assessment_type}/upload-qualification")
async def upload_qualification(
    cin: int,
    assessment_type: str,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)

    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet {cin} not found.")

    sheets = [
        s for s in cadet.assessment_sheets
        if s.assessment_type == assessment_type
    ]

    if not sheets:
        raise HTTPException(status_code=404, detail=f"No {assessment_type} assessments found for cadet {cin}.")

    passed_count = sum(
        1 for s in sheets
        if s.fields and s.fields.get("passed") is True
    )
    required = required_passes(assessment_type)

    if passed_count < required:
        raise HTTPException(
            status_code=400,
            detail=f"Cadet needs {required} passed {assessment_type} assessment(s) to upload qualification (has {passed_count}).",
        )

    for sheet in sheets:
        sheet.uploaded = True

    db.commit()

    return {
        "status":  "success",
        "message": f"{assessment_type.title()} qualification marked as uploaded for cadet {cin}.",
    }

@app.get("/assessments/{assessment_id}/pdf")
async def get_assessment_pdf(
    assessment_id: int,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)

    sheet = db.query(AssessmentSheet).filter(AssessmentSheet.id == assessment_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    if not sheet.pdf_data:
        raise HTTPException(status_code=404, detail="No PDF stored for this assessment.")

    return StreamingResponse(
        io.BytesIO(sheet.pdf_data),
        media_type=sheet.pdf_mime_type or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="assessment_{assessment_id}.pdf"',
            "Cache-Control": "no-cache",
        },
    )

@app.delete("/assessments/{assessment_id}")
async def delete_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)

    sheet = db.query(AssessmentSheet).filter(AssessmentSheet.id == assessment_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    db.delete(sheet)
    db.commit()

    return {"status": "success", "message": f"Assessment {assessment_id} deleted."}

# ===============================
# CADET OVERVIEW ENDPOINTS
# Add these into your main FastAPI app (main.py)
# ===============================

@app.get("/cadets")
async def list_cadets(
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)
    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    return [
        {
            "cin":        c.cin,
            "first_name": c.first_name,
            "last_name":  c.last_name,
            "rank":       c.rank,
            "flight":     c.flight,
        }
        for c in cadets
    ]

@app.get("/cadets/{cin}")
async def get_cadet(
    cin: int,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)

    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    qualifications = [
        {
            "id": q.id,
            "qualification_name": q.qual_type.replace("_", " ").title(),
            "achieved_date": q.date_achieved.isoformat() if q.date_achieved else None,
            "expires_date": q.date_expires.isoformat() if q.date_expires else None,
            "status": q.status,
        }
        for q in cadet.qualifications
    ]

    events = [
        {
            "id": e.id,
            "event_name": e.event.title if e.event else f"Event {e.event_id}",
            "event_date": None,
            "attended": True,
        }
        for e in cadet.cadet_events
    ]

    assessments = [
        {
            "id": a.id,
            "assessment_type": a.assessment_type,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "passed": a.fields.get("passed") if a.fields else None,
            "total_score": a.fields.get("total_score") if a.fields else None,
            "exercise_name": a.fields.get("exercise_name") if a.fields else None,
            "assessor_name": a.fields.get("assessor_name") if a.fields else None,
        }
        for a in cadet.assessment_sheets
    ]

    return {
        "cin": cadet.cin,
        "first_name": cadet.first_name,
        "last_name": cadet.last_name,
        "email": cadet.email,
        "date_of_birth": cadet.date_of_birth.isoformat() if cadet.date_of_birth else None,
        "rank": cadet.rank,
        "flight": cadet.flight,
        "qualifications": qualifications,
        "events": events,
        "assessments": assessments,
    }


@app.patch("/cadets/{cin}")
async def patch_cadet(
    cin: int,
    data: CadetPatch,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)

    cadet = db.query(Cadet).filter(Cadet.cin == cin).first()
    if not cadet:
        raise HTTPException(status_code=404, detail=f"Cadet with CIN {cin} not found.")

    # Only update fields that were explicitly provided
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(cadet, field, value)

    db.commit()
    db.refresh(cadet)

    return {"status": "success", "message": f"Cadet {cin} updated.", "updated_fields": list(update_data.keys())}



# ── In the scraper_map dict inside start_scraper, add: ───────────────────────
#   "upload-qualifications": upload_qualifications_scraper,
# But this scraper needs extra args (assessment_ids), so we use functools.partial.

@app.post("/assessments/upload-to-bader")
async def upload_qualifications_to_bader(
    data: UploadQualificationsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    global scraper_running, current_scraper_user, current_scraper_name

    idinfo = verify_token_staff_only(authorization)
    user = get_or_create_user(db, idinfo["sub"], idinfo["email"])

    if not user.bader_credentials:
        raise HTTPException(
            status_code=400,
            detail="Bader credentials not saved. Please go to Settings first.",
        )

    if not data.assessment_ids:
        raise HTTPException(status_code=400, detail="No assessment IDs provided.")

    # ── Validate all assessment IDs exist and have PDFs before starting ───────
    sheets = (
        db.query(AssessmentSheet)
        .filter(AssessmentSheet.id.in_(data.assessment_ids))
        .all()
    )
    found_ids = {s.id for s in sheets}
    missing = set(data.assessment_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Assessment ID(s) not found: {sorted(missing)}",
        )

    with scraper_state_lock:
        if scraper_running:
            raise HTTPException(status_code=400, detail="A scraper is already running.")
        scraper_running = True

    current_scraper_user = idinfo.get("email")
    current_scraper_name = "upload-qualifications"

    with scraper_lock:
        scraper_messages.clear()
        scraper_messages.append(
            json.dumps({
                "type":         "status",
                "value":        "running",
                "started_by":   current_scraper_user,
                "scraper_name": current_scraper_name,
            })
        )

    # Bind the assessment_ids into the scraper function signature
    bound_scraper = partial(upload_qualifications_scraper, assessment_ids=data.assessment_ids)
    background_tasks.add_task(run_scraper_task, bound_scraper, user.id)

    return {"status": "started", "assessment_ids": data.assessment_ids}


# ===============================
# STATS
# ===============================

BADGE_TYPES = [
    "duke_of_edinburgh", "first_aid", "leadership", "cyber", "radio",
    "road_marching", "space", "music", "flying_badge", "fieldcraft",
    "shooting", "swimming_proficiency",
]

# Maps raw SMS qual names → (badge_category, level)
QUAL_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    # Duke of Edinburgh
    "Blue Pre-Duke of Edinburgh Award":     ("duke_of_edinburgh", "Blue"),
    "Bronze Duke of Edinburgh Award":       ("duke_of_edinburgh", "Bronze"),
    "Silver Duke of Edinburgh Award":       ("duke_of_edinburgh", "Silver"),
    # First Aid
    "St John Youth First Aid":              ("first_aid", "Blue"),
    "St John Essential First Aid":          ("first_aid", "Blue"),
    "St John Activity First Aid":           ("first_aid", "Bronze"),
    # "AED Operator":                         ("first_aid", "Bronze"),
    "Cadet First Aid Instructor Award":     ("first_aid", "Gold"),
    "St John Activity First Aid Assessor":  ("first_aid", "Gold"),
    # Leadership
    "Blue Air Cadet Foundation Leadership":   ("leadership", "Blue"),
    "Bronze Air Cadet Foundation Leadership": ("leadership", "Bronze"),
    "Bronze Leadership":                      ("leadership", "Bronze"),
    "Silver Air Cadet Foundation Leadership": ("leadership", "Silver"),
    "Silver Leadership":                      ("leadership", "Silver"),
    "Gold Leadership":                        ("leadership", "Gold"),
    # Cyber
    # "OpenLearn - Introduction to cyber security: stay safe online": ("cyber", "Blue"),
    "RAFAC Bronze Cyber Course":              ("cyber", "Bronze"),
    "Cyber - Bronze Award":                   ("cyber", "Bronze"),
    "CyberFirst Adventurer":                  ("cyber", "Bronze"),
    "Cyber - Silver Award":                   ("cyber", "Silver"),
    # Radio
    "Radio - Basic Operator (Blue)":          ("radio", "Blue"),
    "Radio - Operator (Bronze)":              ("radio", "Bronze"),
    "Radio - Advanced Voice Procedure (Silver)": ("radio", "Silver"),
    # Road Marching
    "Blue Road Marching":                     ("road_marching", "Blue"),
    "Bronze Road Marching":                   ("road_marching", "Bronze"),
    # Space
    "Blue Space Studies":                     ("space", "Blue"),
    # "OU Applications of Space Technology (Blue)": ("space", "Blue"),
    "Bronze Space Studies":                   ("space", "Bronze"),
    # Music
    "Musician (Blue) - Drum":                 ("music", "Blue"),
    "Musician (Blue) - Lyre":                 ("music", "Blue"),
    "Wing Musician (Bronze) - Drums":         ("music", "Bronze"),
    "Wing Musician (Bronze) - Lyre":          ("music", "Bronze"),
    "Regional Musician (Silver) - Drums":     ("music", "Silver"),
    "Regional Musician (Silver) - Lyre":      ("music", "Silver"),
    "National Musician (Gold) - Lyre":        ("music", "Gold"),
    # Flying Badge
    "PTT Blue":                               ("flying_badge", "Blue"),
    "Blue ATP Ground School":                 ("flying_badge", "Blue"),
    "Aviation FAM PTT":                       ("flying_badge", "Blue"),
    "RAFAC Aviation Training Package Blue Training Badge":   ("flying_badge", "Blue"),
    "Bronze ATP Ground School":               ("flying_badge", "Bronze"),
    "RAFAC Aviation Training Package Bronze Training Badge": ("flying_badge", "Bronze"),
    # Fieldcraft
    "Blue Fieldcraft Skills":                 ("fieldcraft", "Blue"),
    "Bronze Fieldcraft Skills":               ("fieldcraft", "Bronze"),
    # Shooting
    "Blue Shot (Air Rifle)":                  ("shooting", "Blue"),
    "Blue Shot (L98A2)":                      ("shooting", "Blue"),
    "Blue Shot (Small Bore)":                 ("shooting", "Blue"),
    "Blue Shot (Target Rifle)":               ("shooting", "Blue"),
    "Bronze Shot (Air Rifle)":                ("shooting", "Bronze"),
    "Bronze Shot (L98A2)":                    ("shooting", "Bronze"),
    "Bronze Shot (Small Bore)":               ("shooting", "Bronze"),
    "Bronze Shot (Target Rifle)":             ("shooting", "Bronze"),
    "Silver Shot (Air Rifle)":                ("shooting", "Silver"),
    "Gold Shot (Air Rifle)":                  ("shooting", "Gold"),
    # Swimming
    "Basic Swimming Competence":              ("swimming_proficiency", "Basic"),
    "Intermediate Swimming Competence":       ("swimming_proficiency", "Intermediate"),
}

# Higher index = higher level; used to pick the best level per cadet per badge
LEVEL_RANK = {
    "None": 0, "Blue": 1, "Basic": 1, "Bronze": 2, "Intermediate": 2,
    "Silver": 3, "Advanced": 3, "Gold": 4, "Nijmegen": 5,
}

def compute_stats(db: Session) -> dict:
    from datetime import date as date_type
    cadets = db.query(Cadet).all()
    total = len(cadets)

    # Flight breakdown
    flight_counts: dict = {}
    for c in cadets:
        flight = c.flight or "Unknown"
        flight_counts[flight] = flight_counts.get(flight, 0) + 1

    # Age breakdown
    age_counts: dict = {}
    today = date_type.today()
    for c in cadets:
        if c.date_of_birth:
            dob = c.date_of_birth
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            age_counts[str(age)] = age_counts.get(str(age), 0) + 1

    # Rank breakdown
    rank_counts: dict = {}
    for c in cadets:
        rank = c.rank or "Unknown"
        rank_counts[rank] = rank_counts.get(rank, 0) + 1

    # Badge breakdown — map raw qual names → (category, level), pick highest per cadet
    all_quals = db.query(CadetQualification).all()
    # best_level[(cadet_id, category)] = highest level string achieved
    best_level: dict = {}
    for q in all_quals:
        mapping = QUAL_CATEGORY_MAP.get(q.qual_type)
        if not mapping:
            continue
        category, level = mapping
        key = (q.cadet_id, category)
        current = best_level.get(key, "None")
        if LEVEL_RANK.get(level, 0) > LEVEL_RANK.get(current, 0):
            best_level[key] = level

    badges: dict = {}
    for badge in BADGE_TYPES:
        level_counts: dict = {}
        for c in cadets:
            level = best_level.get((c.cin, badge), "None")
            level_counts[level] = level_counts.get(level, 0) + 1
        badges[badge] = level_counts

    return {
        "total_cadets": total,
        "by_flight": flight_counts,
        "by_age": age_counts,
        "by_rank": rank_counts,
        "badges": badges,
    }


@app.get("/stats/current")
async def get_current_stats(
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)
    return compute_stats(db)


@app.get("/stats/history")
async def get_stats_history(
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token(authorization)
    one_year_ago = datetime.now() - timedelta(days=365)
    snapshots = (
        db.query(StatsSnapshot)
        .filter(StatsSnapshot.captured_at >= one_year_ago)
        .order_by(StatsSnapshot.captured_at.asc())
        .all()
    )
    return [{"date": s.captured_at.isoformat(), "data": s.data} for s in snapshots]


@app.post("/stats/snapshot")
async def create_stats_snapshot(
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    verify_token_staff_only(authorization)
    stats = compute_stats(db)
    snapshot = StatsSnapshot(captured_at=datetime.now(), data=stats)
    db.add(snapshot)
    db.commit()
    return {"status": "ok", "captured_at": snapshot.captured_at.isoformat()}