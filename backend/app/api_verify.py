"""Email verification API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Contact
from app.deps import get_current_user, audit
from app.email_verify import verify_email_syntax, batch_verify_emails

router = APIRouter(prefix="/api", tags=["verify"])


@router.post("/verify/email")
def verify_single(payload: dict, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Verify a single email address (syntax + MX)."""
    email = (payload.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=422, detail="email required")
    result = verify_email_syntax(email)
    audit(db, user.id, "email_verify", "email", None, email)
    db.commit()
    return result


@router.post("/verify/batch")
def verify_batch(payload: dict, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Verify a batch of emails (syntax + MX)."""
    emails = payload.get("emails", [])
    if not emails:
        # Verify all emails in user's contacts
        contacts = db.query(Contact).filter(
            Contact.user_id == user.id, Contact.kind == "EMAIL").all()
        emails = [c.value for c in contacts]
    if not emails:
        raise HTTPException(status_code=404, detail="No emails to verify")
    results = batch_verify_emails(emails)
    # Update contact verification status
    for r in results:
        if r["status"] == "DOMAIN_CHECKED":
            contacts = db.query(Contact).filter(
                Contact.user_id == user.id, Contact.kind == "EMAIL",
                Contact.value == r["email"]).all()
            for c in contacts:
                c.verification = "domain_checked"
        elif r["status"] == "INVALID":
            contacts = db.query(Contact).filter(
                Contact.user_id == user.id, Contact.kind == "EMAIL",
                Contact.value == r["email"]).all()
            for c in contacts:
                c.verification = "invalid"
    db.commit()
    audit(db, user.id, "email_verify_batch", "verify", None,
          "%d verified" % len(results))
    db.commit()
    return {"results": results, "total": len(results)}


@router.get("/verify/gemini-status")
def gemini_status():
    """Check if Gemini API is configured."""
    from app.config import settings
    key = settings.GEMINI_API_KEY
    return {"configured": bool(key), "model": "gemini-3.5-flash" if key else None}
