"""Email OTP verification — hassle-free signup/login."""
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.schemas import OTPRequest, OTPVerify
from app.config import settings
from app.security import hash_password, create_access_token, create_refresh_token, hash_token
from app.deps import rate_limit, audit
from app.database import SessionLocal

router = APIRouter(prefix="/api/auth", tags=["otp"])

OTP_EXPIRY_MINUTES = 10


def _send_otp_email(email: str, otp: str):
    """Send OTP via SMTP or just log in dryrun mode."""
    if settings.EMAIL_PROVIDER == "dryrun":
        print("OTP for %s: %s" % (email, otp), flush=True)
        return
    if settings.EMAIL_PROVIDER == "smtp" and settings.SMTP_HOST:
        msg = MIMEText("Your LeadPilot verification code is: %s\n\nThis code expires in %d minutes." % (otp, OTP_EXPIRY_MINUTES))
        msg["Subject"] = "LeadPilot Verification Code"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = email
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return
    # Fallback: log OTP
    print("OTP for %s: %s" % (email, otp), flush=True)


@router.post("/otp/send")
def send_otp(payload: OTPRequest, db: Session = Depends(get_db)):
    """Send a 6-digit OTP to the email address."""
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()

    # Generate OTP
    otp = "%06d" % secrets.randbelow(1000000)
    expires = datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)

    if user:
        user.otp_code = otp
        user.otp_expires = expires
    else:
        # Create a pending user (will be completed after OTP verify)
        user = User(
            email=email,
            password_hash="",  # will be set on verify
            otp_code=otp,
            otp_expires=expires,
            is_active=False,  # inactive until verified
        )
        db.add(user)

    db.commit()
    _send_otp_email(email, otp)
    audit(db, user.id if user.id else 0, "otp_send", "user", None, email)
    db.commit()

    return {"message": "OTP sent to %s" % email, "email": email}


@router.post("/otp/verify")
def verify_otp(payload: OTPVerify, db: Session = Depends(get_db)):
    """Verify OTP and activate the account. Returns tokens."""
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()

    if not user or not user.otp_code:
        raise HTTPException(status_code=400, detail="No OTP sent to this email")

    if user.otp_code != payload.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    if user.otp_expires and user.otp_expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="OTP expired")

    # Activate user
    user.email_verified = True
    user.is_active = True
    user.otp_code = None
    user.otp_expires = None

    # If no password set (new user), set a default one
    if not user.password_hash:
        user.password_hash = hash_password(secrets.token_urlsafe(16))
        user.verification_token = secrets.token_urlsafe(32)

    # First user becomes admin
    if db.query(User).filter(User.is_admin == True).count() == 0:
        user.is_admin = True

    db.commit()
    audit(db, user.id, "otp_verify", "user", user.id, email)
    db.commit()

    # Issue tokens
    from app.security import create_access_token, create_refresh_token, hash_token
    from app.models import RefreshToken
    raw, digest = create_refresh_token()
    db.add(RefreshToken(
        user_id=user.id, token_hash=digest,
        expires_at=datetime.utcnow() + __import__("datetime").timedelta(days=7)))
    db.commit()

    return {
        "access_token": create_access_token(user.id),
        "refresh_token": raw,
        "token_type": "bearer",
        "email_verified": True,
    }
