"""Admin (first account) + own verification token (dev aid) + abuse reports."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.models import User
from app.deps import get_admin, get_current_user, rate_limit, audit
from app.config import settings

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/auth/verification-status")
def verification_status(db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Lets a logged-in user fetch their OWN pending verification token.

    Only returned while EMAIL_PROVIDER=dryrun (no mailer to deliver it).
    Never exposes another user's token.
    """
    if user.email_verified:
        return {"verified": True, "token": None}
    token = user.verification_token if settings.EMAIL_PROVIDER == "dryrun" else None
    return {"verified": False, "token": token,
            "hint": "dryrun has no mailer — use this token with GET /api/auth/verify?token=…"}


@router.get("/admin/users")
def admin_users(db: Session = Depends(get_db), admin: User = Depends(get_admin)):
    rows = db.query(User).order_by(User.id).limit(200).all()
    return [{"id": u.id, "email": u.email, "is_active": u.is_active,
             "is_admin": u.is_admin, "email_verified": u.email_verified,
             "created_at": u.created_at} for u in rows]


class SuspendIn(BaseModel):
    active: bool


@router.post("/admin/users/{user_id}/suspend")
def admin_suspend(user_id: int, payload: SuspendIn,
                  db: Session = Depends(get_db), admin: User = Depends(get_admin)):
    if user_id == admin.id:
        raise HTTPException(status_code=422, detail="Cannot suspend yourself")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    u.is_active = payload.active
    audit(db, admin.id, "suspend" if not payload.active else "unsuspend",
          "user", user_id)
    db.commit()
    return {"id": u.id, "is_active": u.is_active}


class AbuseReport(BaseModel):
    message: str


@router.post("/abuse-report",
             dependencies=[Depends(rate_limit("abuse", settings.RATE_SENSITIVE_PER_MIN))])
def abuse_report(payload: AbuseReport, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Abuse reports land in the audit log for admin review."""
    audit(db, user.id, "abuse_report", detail=payload.message[:1000])
    db.commit()
    return {"message": "Report received — an administrator will review it"}
