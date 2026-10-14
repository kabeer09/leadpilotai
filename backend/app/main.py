"""LeadPilot Public Trial — FastAPI entrypoint."""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings, startup_warnings
from app import api_auth, api_health, api_crawls, api_crm, api_email, api_analytics, api_admin

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


@app.on_event("startup")
def _startup():
    for w in startup_warnings():
        logger.warning(f"CONFIG: {w}")


@app.get("/")
def root():
    return {"name": "LeadPilot Public Trial", "docs": "/docs", "health": "/api/health"}
