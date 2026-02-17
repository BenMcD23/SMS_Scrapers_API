import os
import threading
import asyncio
from typing import AsyncGenerator
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, Form
from fastapi.responses import FileResponse, StreamingResponse
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
    allow_origins=["http://localhost:3000"], # Your Next.js URL
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared state for the "old way" status
scraper_messages = []
scraper_lock = threading.Lock()
# NEW: Event to notify SSE when new messages arrive
message_event = asyncio.Event()

def get_db():
    db = Session(engine)
    try: yield db
    finally: db.close()

# --- SCRAPER LOGIC ---

def run_scraper_task(scraper_func):
    try:
        # We wrap your existing scraper
        scraper_func(scraper_messages, scraper_lock)
        with scraper_lock:
            scraper_messages.append("DONE") # Signal for the frontend
    except Exception as e:
        with scraper_lock:
            scraper_messages.append(f"Error: {str(e)}")
    finally:
        # Trigger the event to push the last message
        message_event.set()

@app.get("/run-scraper/{name}")
async def start_scraper(name: str, background_tasks: BackgroundTasks):
    scraper_map = {
        "cadet-quali": quali_scraper,
        "cadet-event": cadet_event_scraper,
        "317-event": event_317_scraper,
    }
    if name not in scraper_map:
        raise HTTPException(status_code=404)
    
    with scraper_lock:
        scraper_messages.clear()
        message_event.clear()

    background_tasks.add_task(run_scraper_task, scraper_map[name])
    return {"status": "started"}

# --- THE BETTER WAY: SERVER-SENT EVENTS (SSE) ---

@app.get("/scraper-stream")
async def scraper_stream() -> StreamingResponse:
    """
    Next.js will connect to this. 
    It pushes messages as they happen.
    """
    async def event_generator():
        last_idx = 0
        while True:
            # Wait until a message is added or check periodically
            await asyncio.sleep(0.5) 
            
            with scraper_lock:
                if last_idx < len(scraper_messages):
                    for i in range(last_idx, len(scraper_messages)):
                        msg = scraper_messages[i]
                        yield f"data: {msg}\n\n"
                        if msg == "DONE":
                            return # Close connection
                    last_idx = len(scraper_messages)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# --- DATA & DOCS ---

@app.get("/events")
async def get_events(db: Session = Depends(get_db)):
    return db.query(Event317).all()

@app.post("/generate-doc")
async def generate_doc(event_id: int, action: str, db: Session = Depends(get_db)):
    event = db.query(Event317).filter(Event317.id == event_id).first()
    if not event: raise HTTPException(status_code=404)
    
    # Logic to return file
    # (Assuming generate_ji returns a file path or BytesIO)
    # return FileResponse(...)
    pass