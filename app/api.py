import os
import threading
import asyncio
import json
from typing import AsyncGenerator
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database.create_db import init_db
from database.database import engine
from database.models import Event317
from scripts.ji_ao_generator import generate_ji, generate_ao
from scripts.scraper_calls import *

init_db()

app = FastAPI()

# Allow Next.js to talk to FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://sms.317atc.co.uk"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# GLOBAL SCRAPER STATE
# ===============================

scraper_messages = []
scraper_lock = threading.Lock()

scraper_running = False
scraper_state_lock = threading.Lock()

message_event = asyncio.Event()

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

def run_scraper_task(scraper_func):
    global scraper_running

    try:
        scraper_func(scraper_messages, scraper_lock)

        with scraper_lock:
            scraper_messages.append(
                json.dumps({"type": "status", "value": "done"})
            )

    except Exception as e:
        with scraper_lock:
            scraper_messages.append(
                json.dumps({"type": "error", "value": str(e)})
            )

    finally:
        with scraper_state_lock:
            scraper_running = False

        message_event.set()


@app.get("/run-scraper/{name}")
async def start_scraper(name: str, background_tasks: BackgroundTasks):
    global scraper_running

    scraper_map = {
        "cadet-quali": quali_scraper,
        "cadet-event": cadet_event_scraper,
        "317-event": event_317_scraper,
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

    # Reset messages
    with scraper_lock:
        scraper_messages.clear()
        scraper_messages.append(
            json.dumps({"type": "status", "value": "running"})
        )

    message_event.set()

    background_tasks.add_task(run_scraper_task, scraper_map[name])

    return {"status": "started"}

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
        media_type="text/event-stream"
    )

# ===============================
# DATA ENDPOINTS
# ===============================

@app.get("/events")
async def get_events(db: Session = Depends(get_db)):
    return db.query(Event317).all()


@app.post("/generate-doc")
async def generate_doc(event_id: int, action: str, db: Session = Depends(get_db)):
    event = db.query(Event317).filter(Event317.id == event_id).first()

    if not event:
        raise HTTPException(status_code=404)

    # Your file return logic here
    pass