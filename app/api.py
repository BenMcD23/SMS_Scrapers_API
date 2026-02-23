import os
import threading
import asyncio
import json
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
MAX_CONCURRENT_SCRAPERS = 2

# ===============================
# GLOBAL SCRAPER STATE
# ===============================

scraper_messages = []
scraper_lock = threading.Lock()

scraper_running = False
scraper_state_lock = threading.Lock()

message_event = asyncio.Event()

current_scraper_user = None

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
    global scraper_running, current_scraper_user  
    db = Session(engine)
    # Create an event to signal the scraper to stop if it takes too long
    stop_event = threading.Event()
    
    def monitor_timeout():
        # Global timeout for the entire scraper
        time.sleep(900)
        if scraper_running:
            stop_event.set()

    # Start the timeout monitor thread
    monitor_thread = threading.Thread(target=monitor_timeout, daemon=True)
    monitor_thread.start()

    try:
        # Pass the stop_event to your scraper
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

        message_event.set()


@app.get("/run-scraper/{name}")
async def start_scraper(name: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), authorization: str = Header(None)):
    global scraper_running, current_scraper_user

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
    
    # Prevent multiple scrapers running
    with scraper_state_lock:
        if scraper_running:
            raise HTTPException(
                status_code=400,
                detail="Scraper already running"
            )
        scraper_running = True

    current_scraper_user = idinfo.get("email")

    # Reset messages
    with scraper_lock:
        scraper_messages.clear()
        scraper_messages.append(
            json.dumps({"type": "status", "value": "running"})
        )

    message_event.set()

    background_tasks.add_task(run_scraper_task, scraper_map[name], user.id)

    return {"status": "started"}

@app.post("/save-credentials")
async def save_credentials(
    data: dict, 
    db: Session = Depends(get_db),
    authorization: str = Header(None) 
):
    print(f"DEBUG: Received Authorization Header: {authorization}")

    # 1. Extract the token from the "Bearer <token>" header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    
    token = authorization.split(" ")[1]

    try:
        # 2. Verify the token with Google
        # This checks that the token is real, not expired, and meant for your app
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
        
        # 3. Get the unique Google ID and Email from the verified token
        google_id = idinfo['sub']
        email = idinfo['email']

    except ValueError:
        # Token was invalid (fake or expired)
        raise HTTPException(status_code=401, detail="Invalid Google Token")

    # 4. Find or create the user in your database
    user = db.query(User).filter(User.google_id == google_id).first()
    
    if not user:
        user = User(google_id=google_id, email=email)
        db.add(user)

    # 5. Update the Bader credentials
    user.role_username = data.get("role_user")
    user.role_password = encrypt_password(data.get("role_pass"))
    user.personal_username = data.get("pers_user")
    user.personal_password = encrypt_password(data.get("pers_pass"))
    
    db.commit()
    
    return {"status": "success", "message": f"Settings saved for {email}"}
# ===============================
# SERVER SENT EVENTS
# ===============================

@app.get("/scraper-stream")
async def scraper_stream():
    async def event_generator():
        last_idx = 0

        while True:
            await asyncio.sleep(0.5)

            with scraper_lock:
                if last_idx < len(scraper_messages):
                    for i in range(last_idx, len(scraper_messages)):
                        msg = scraper_messages[i]
                        yield f"data: {msg}\n\n"

                        # Close stream when done
                        try:
                            parsed = json.loads(msg)
                            if parsed.get("type") == "status" and parsed.get("value") == "done":
                                return
                        except:
                            pass

                    last_idx = len(scraper_messages)

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
            if json.loads(m).get("type") == "log"
        ]
    return {
        "running": scraper_running,
        "started_by": current_scraper_user,   # see below
        "recent_logs": logs[-50:],            # last 50 lines
    }
# ===============================
# DATA ENDPOINTS
# ===============================

@app.get("/events")
async def get_events(db: Session = Depends(get_db)):
    return db.query(Event317).all()


@app.get("/generate-doc/{event_id}/{action}")
async def generate_doc_endpoint(event_id: int, action: str, db: Session = Depends(get_db)):
    # 1. Fetch event from DB
    event = db.query(Event317).filter(Event317.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    try:
        # 2. Call the appropriate generator function
        if action == "ji":
            file_buffer = generate_ji(event)
            filename = f"JI_{event.reference}.docx"
        elif action == "ao":
            file_buffer = generate_ao(event)
            filename = f"AO_{event.reference}.docx"
        else:
            raise HTTPException(status_code=400, detail="Invalid action")

        # 3. Return the file as a stream
        return StreamingResponse(
            file_buffer,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        print(f"Error generating document: {e}")
        raise HTTPException(status_code=500, detail=str(e))