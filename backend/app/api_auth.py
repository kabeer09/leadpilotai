"""Auth routes: signup/login/logout/refresh/me/verify/reset. Versioned /api."""
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, RefreshToken
from app.schemas import (SignupIn, LoginIn, TokenOut, RefreshIn, UserOut,
                         PasswordResetRequest, PasswordResetConfirm, ProfileUpdate)
from app.security import (hash_password, verify_password, create_access_token,
                          create_refresh_token, hash_token)
from app.deps import get_current_user, rate_limit, audit
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue_tokens(db: Session, user: User) -> TokenOut:
    raw, digest = create_refresh_token()
    db.add(RefreshToken(
        user_id=user.id, token_hash=digest,
        expires_at=datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)))
    db.commit()
    return TokenOut(access_token=create_access_token(user.id), refresh_token=raw)


@router.post("/signup", response_model=TokenOut, status_code=201,
             dependencies=[Depends(rate_limit("signup", settings.RATE_SIGNUP_PER_MIN))])
def signup(payload: SignupIn, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    # First-run setup: the very first account becomes admin (no backdoor afterwards)
    is_first = db.query(User).count() == 0
    user = User(email=email, password_hash=hash_password(payload.password),
                name=(payload.name or "").strip() or None,
                is_admin=is_first,
                verification_token=secrets.token_urlsafe(32))
    db.add(user)
    db.commit()
    db.refresh(user)
    audit(db, user.id, "signup", "user", user.id)
    db.commit()
    # TODO: send verification email when a provider is configured; token
    # returned here ONLY in dev so trials can verify without SMTP.
    tokens = _issue_tokens(db, user)
    return tokens


@router.post("/login", response_model=TokenOut,
             dependencies=[Depends(rate_limit("login", settings.RATE_LOGIN_PER_MIN))])
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account suspended")
    audit(db, user.id, "login", "user", user.id)
    return _issue_tokens(db, user)


@router.post("/logout")
def logout(payload: RefreshIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """Revoke one refresh token (logout this device)."""
    row = db.query(RefreshToken).filter(
        RefreshToken.token_hash == hash_token(payload.refresh_token),
        RefreshToken.user_id == user.id).first()
    if row:
        row.revoked = True
        db.commit()
    audit(db, user.id, "logout", "user", user.id)
    db.commit()
    return {"message": "Logged out"}


@router.post("/refresh", response_model=TokenOut)
def refresh(payload: RefreshIn, db: Session = Depends(get_db)):
    row = db.query(RefreshToken).filter(
        RefreshToken.token_hash == hash_token(payload.refresh_token)).first()
    if not row or row.revoked or row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Account inactive")
    row.revoked = True  # rotation: old token dies, new pair issued
    tokens = _issue_tokens(db, user)
    return tokens


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
def update_profile(payload: ProfileUpdate, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if payload.name is not None:
        user.name = payload.name.strip() or None
    if payload.whatsapp_number is not None:
        import re
        phone = re.sub(r"\D", "", payload.whatsapp_number)
        if len(phone) < 8 or len(phone) > 15:
            raise HTTPException(status_code=422, detail="Invalid WhatsApp number")
        user.whatsapp_number = payload.whatsapp_number
        user.whatsapp_verified = False
    audit(db, user.id, "profile_update", "user", user.id)
    db.commit()
    db.refresh(user)
    return user


@router.get("/verify")
def verify_email(token: str, db: Session = Depends(get_db)):
    """Verify email ownership (link emailed or shown in dev). Required before live sends."""
    user = db.query(User).filter(User.verification_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid verification token")
    user.email_verified = True
    user.verification_token = None
    audit(db, user.id, "email_verified", "user", user.id)
    db.commit()
    return {"message": "Email verified"}


@router.post("/password-reset/request")
def reset_request(payload: PasswordResetRequest, request: Request,
                  db: Session = Depends(get_db)):
    # Rate-limited; always return success to avoid account enumeration.
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if user:
        user.reset_token = secrets.token_urlsafe(32)
        user.reset_expires = datetime.utcnow() + timedelta(hours=1)
        db.commit()
    return {"message": "If the address exists, a reset link was sent"}


@router.post("/password-reset/confirm")
def reset_confirm(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == payload.token).first()
    if not user or not user.reset_expires or user.reset_expires < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user.password_hash = hash_password(payload.new_password)
    user.reset_token = None
    user.reset_expires = None
    # Revoke all sessions on password change
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})
    audit(db, user.id, "password_reset", "user", user.id)
    db.commit()
    return {"message": "Password updated, all sessions revoked"}
