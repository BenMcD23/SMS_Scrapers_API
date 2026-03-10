import os
import threading
import asyncio
import json
import time
import base64
import httpx
import io
from dotenv import load_dotenv
from datetime import datetime

from typing import AsyncGenerator
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, Header, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy.orm import Session
from sqlalchemy import or_

from database.create_db import init_db
from database.database import engine
from database.models import Event317, User, BaderCredentials, UserSignature, AssessmentSheet, Cadet

from scripts.ji_ao_generator import generate_ji, generate_ao
from scripts.scraper_calls import *

from assessment_builders.leadership import generate_leadership_pdf, process_assessment_data

from utils.crypto import encrypt_password

from google.oauth2 import id_token
from google.auth.transport import requests

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

def verify_token(authorization: str) -> dict:
    """Verify Bearer token and return idinfo dict. Raises 401 on failure."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        token = authorization.split(" ")[1]
        return id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Token")

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

    idinfo = verify_token(authorization)
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
    idinfo = verify_token(authorization)
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


# ===============================
# SIGNATURE ENDPOINTS
# ===============================

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

# ===============================
# SERVER SENT EVENTS
# ===============================

def safe_parse(m: str) -> dict | None:
    try:
        return json.loads(m) if m else None
    except json.JSONDecodeError:
        return None

@app.get("/scraper-stream")
async def scraper_stream():
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
async def scraper_status():
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
    pdf_bytes  = generate_leadership_pdf(processed)

    sheet = AssessmentSheet(
        assessment_type="leadership",
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
    "leadership": 2,
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
            # You can track this however you like — here we check a field in the sheet fields
            # e.g. fields["qualification_uploaded"] = True set by the upload endpoint
            uploaded = any(
                s.fields.get("qualification_uploaded") is True
                for s in type_sheets
                if s.fields
            )

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

    # Mark all sheets for this cadet+type as uploaded
    for sheet in sheets:
        if sheet.fields is None:
            sheet.fields = {}
        sheet.fields = {**sheet.fields, "qualification_uploaded": True}

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

from pydantic import BaseModel, EmailStr
from typing import Optional


class CadetPatch(BaseModel):
    email: Optional[str] = None

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