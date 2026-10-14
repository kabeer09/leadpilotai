"""Liveness + config-status (no secret values exposed)."""
from fastapi import APIRouter
from sqlalchemy import text
from app.database import SessionLocal
from app.config import settings, startup_warnings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    db_ok = False
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unreachable",
        "email_provider": settings.EMAIL_PROVIDER,
        "provider_configured": (
            True if settings.EMAIL_PROVIDER == "dryrun"
            else bool((settings.SMTP_HOST and settings.SMTP_FROM)
                      or (settings.RESEND_API_KEY and settings.RESEND_FROM))),
        "warnings": startup_warnings(),
    }
