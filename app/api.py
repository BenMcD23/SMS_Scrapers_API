import os
import threading
import asyncio
import json
import time
import base64
import httpx

from typing import AsyncGenerator
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, Header
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database.create_db import init_db
from database.database import engine
from database.models import Event317, User
from scripts.ji_ao_generator import generate_ji, generate_ao
from scripts.scraper_calls import *

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

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
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
async def start_scraper(name: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), authorization: str = Header(None)):
    global scraper_running, current_scraper_user, current_scraper_name

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        token = authorization.split(" ")[1]
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        google_id = idinfo['sub']
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Token")

    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Save settings first.")

    scraper_map = {
        "cadet-quali": quali_scraper,
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


@app.post("/save-credentials")
async def save_credentials(
    data: dict, 
    db: Session = Depends(get_db),
    authorization: str = Header(None) 
):
    print(f"DEBUG: Received Authorization Header: {authorization}")

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    
    token = authorization.split(" ")[1]

    try:
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        google_id = idinfo['sub']
        email = idinfo['email']
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google Token")

    user = db.query(User).filter(User.google_id == google_id).first()
    
    if not user:
        user = User(google_id=google_id, email=email)
        db.add(user)

    user.role_username = data.get("role_user")
    user.role_password = encrypt_password(data.get("role_pass"))
    user.personal_username = data.get("pers_user")
    user.personal_password = encrypt_password(data.get("pers_pass"))
    
    db.commit()
    
    return {"status": "success", "message": f"Settings saved for {email}"}


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