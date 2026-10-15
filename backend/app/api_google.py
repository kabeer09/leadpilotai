"""Sign in with Google (login + Gmail send access in one consent).

Flow: frontend -> GET /api/auth/google/login -> Google consent ->
GET /api/auth/google/callback -> one-time code -> frontend
POST /api/auth/google/consume -> our JWT pair. Refresh tokens stay in the
DB and are never sent to the browser.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import httpx
from app.database import get_db
from app.models import User, OAuthState
from app.schemas import TokenOut
from app.security import hash_password, create_access_token, create_refresh_token
from app.deps import get_current_user, audit
from app.config import settings

router = APIRouter(prefix="/api/auth/google", tags=["google-auth"])

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = ["openid", "email", "profile",
          "https://www.googleapis.com/auth/gmail.send"]


def google_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _secret() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


@router.get("/login")
def google_login(db: Session = Depends(get_db)):
    """Return the Google consent URL (frontend redirects the user there)."""
    if not google_configured():
        raise HTTPException(status_code=409, detail=(
            "Google login is not configured — set GOOGLE_CLIENT_ID/SECRET, "
            "see docs/providers.md"))
    raw, digest = _secret()
    db.add(OAuthState(kind="state", value_hash=digest,
                      expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # refresh token for Gmail sending
        "prompt": "consent",
        "state": raw,
    }
    return {"auth_url": f"{AUTH_URL}?{urlencode(params)}"}


def _exchange_code(code: str) -> dict:
    resp = httpx.post(TOKEN_URL, data={
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "code": code, "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code"}, timeout=20.0)
    resp.raise_for_status()
    return resp.json()


def _userinfo(access_token: str) -> dict:
    resp = httpx.get(USERINFO_URL,
                     headers={"Authorization": f"Bearer {access_token}"},
                     timeout=15.0)
    resp.raise_for_status()
    return resp.json()


@router.get("/callback")
def google_callback(code: str | None = None, state: str | None = None,
                    error: str | None = None,
                    db: Session = Depends(get_db)):
    """Google redirects here. Validates state, links/creates the account,
    then redirects to the frontend with a single-use login code."""
    frontend = settings.FRONTEND_URL.rstrip("/")
    if error or not code or not state:
        return RedirectResponse(f"{frontend}/login?google=error")
    row = db.query(OAuthState).filter(
        OAuthState.kind == "state",
        OAuthState.value_hash == hashlib.sha256(state.encode()).hexdigest()).first()
    if not row or row.expires_at < datetime.utcnow():
        return RedirectResponse(f"{frontend}/login?google=error&reason=state")
    db.delete(row)
    try:
        tokens = _exchange_code(code)
        info = _userinfo(tokens["access_token"])
    except Exception:
        return RedirectResponse(f"{frontend}/login?google=error&reason=token")
    email = (info.get("email") or "").lower().strip()
    sub = str(info.get("sub") or "")
    if not email or not sub or info.get("email_verified") is False:
        return RedirectResponse(f"{frontend}/login?google=error&reason=profile")
    user = db.query(User).filter(User.google_sub == sub).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()
        if user:
            if user.google_sub and user.google_sub != sub:
                return RedirectResponse(f"{frontend}/login?google=error&reason=conflict")
            user.google_sub = sub  # link password account to Google
        else:
            user = User(email=email, name=(info.get("name") or "")[:255] or None,
                        password_hash="google:" + secrets.token_hex(16),
                        is_admin=(db.query(User).count() == 0),
                        google_sub=sub)
            db.add(user)
            db.flush()
    user.email_verified = True
    if tokens.get("refresh_token"):
        user.gmail_refresh_token = tokens["refresh_token"]
    raw, digest = _secret()
    db.add(OAuthState(kind="login_code", value_hash=digest, user_id=user.id,
                      expires_at=datetime.utcnow() + timedelta(minutes=5)))
    audit(db, user.id, "google_login", "user", user.id)
    db.commit()
    return RedirectResponse(f"{frontend}/auth/callback?code={raw}")


@router.post("/consume", response_model=TokenOut)
def google_consume(payload: dict, db: Session = Depends(get_db)):
    """Exchange the one-time login code for our JWT pair."""
    code = (payload.get("code") or "")
    row = db.query(OAuthState).filter(
        OAuthState.kind == "login_code",
        OAuthState.value_hash == hashlib.sha256(code.encode()).hexdigest()).first()
    if not row or row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired login code")
    user = db.query(User).filter(User.id == row.user_id).first()
    db.delete(row)
    if not user or not user.is_active:
        db.commit()
        raise HTTPException(status_code=401, detail="Account inactive")
    raw, digest = create_refresh_token()
    from app.models import RefreshToken
    from datetime import timedelta as _td
    db.add(RefreshToken(user_id=user.id, token_hash=digest,
                        expires_at=datetime.utcnow() + _td(
                            days=settings.REFRESH_TOKEN_EXPIRE_DAYS)))
    db.commit()
    return TokenOut(access_token=create_access_token(user.id), refresh_token=raw)


@router.get("/gmail/status")
def gmail_status(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Does this account have Gmail send access? (No secrets exposed.)"""
    db.refresh(user)
    return {"connected": bool(user.gmail_refresh_token),
            "provider_mode": settings.EMAIL_PROVIDER}
