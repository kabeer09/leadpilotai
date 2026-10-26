"""LeadPilot Public Trial — FastAPI entrypoint."""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings, startup_warnings
from app import api_auth, api_health, api_crawls, api_crm, api_email, api_analytics, api_admin, api_google, api_whatsapp, api_import, api_research, api_enrich, api_otp
# Import research models so tables are created
from app import models_research  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="LeadPilot Public Trial", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_auth.router)
app.include_router(api_health.router)
app.include_router(api_crawls.router)
app.include_router(api_crm.router)
app.include_router(api_email.router)
app.include_router(api_analytics.router)
app.include_router(api_admin.router)
app.include_router(api_google.router)
app.include_router(api_whatsapp.router)
app.include_router(api_import.router)
app.include_router(api_research.router)
app.include_router(api_enrich.router)
app.include_router(api_otp.router)

# Serve frontend static files if available (for combined deployment)
import os as _os
_static_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "static")
if _os.path.isdir(_static_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")


@app.on_event("startup")
def _startup():
    # Ensure research tables exist (additive, safe)
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    for w in startup_warnings():
        logger.warning(f"CONFIG: {w}")
    # A fresh process owns no crawl threads: any CRAWLING row is stale
    # (crash/restart mid-run). Saved companies are intact (incremental
    # saves); FAILED unlocks Retry with an idempotent resume.
    try:
        from app.database import SessionLocal
        from app.models import CrawlJob
        from datetime import datetime
        db = SessionLocal()
        try:
            stale = db.query(CrawlJob).filter(
                CrawlJob.status == "CRAWLING").all()
            for job in stale:
                job.status = "FAILED"
                job.finished_at = datetime.utcnow()
                job.errors = ((job.errors or "") + "\nbackend restarted mid-run").strip()[:2000]
            if stale:
                db.commit()
                logger.warning(f"Recovered {len(stale)} stale crawl(s) as FAILED")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Stale-crawl recovery skipped: {e}")


@app.get("/")
def root():
    return {"name": "LeadPilot Public Trial", "docs": "/docs", "health": "/api/health"}
