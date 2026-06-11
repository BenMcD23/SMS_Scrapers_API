"""App entrypoint — wires up middleware, background jobs, and the routers.

Endpoint logic lives in routers/, shared helpers in core/.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database.create_db import init_db
from database.database import SessionLocal
from database.models import StoresOrder

from core.security import require_user
from routers import (
    assessments, badges, cadets, events, form_generators,
    newsletters, portal, programme, scrapers, settings, stats, stores,
)


def _cleanup_old_completed_orders():
    cutoff = datetime.now() - timedelta(days=182)
    db = SessionLocal()
    try:
        orders = (
            db.query(StoresOrder)
            .filter(StoresOrder.completed == True, StoresOrder.created_at < cutoff)
            .all()
        )
        for order in orders:
            db.delete(order)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()



@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = BackgroundScheduler()
    scheduler.add_job(_cleanup_old_completed_orders, "interval", hours=24)
    # scheduler.add_job(_cleanup_old_completed_assessments, "interval", hours=24)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

# Allow the Next.js frontends to talk to us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://sms.317atc.co.uk", "https://317-sms-site.vercel.app"],
    allow_origin_regex=r"https://317-sms-site.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    allow_credentials=True,
)


@app.get("/health")
def health_check(idinfo: dict = Depends(require_user)):
    return {"ok": True, "email": idinfo["email"]}


# portal must come before cadets so /cadets/me isn't swallowed by /cadets/{cin}
app.include_router(portal.router)
app.include_router(cadets.router)
app.include_router(scrapers.router)
app.include_router(settings.router)
app.include_router(form_generators.router)
app.include_router(events.router)
app.include_router(programme.router)
app.include_router(newsletters.router)
app.include_router(assessments.router)
app.include_router(stats.router)
app.include_router(stores.router)
app.include_router(badges.router)
