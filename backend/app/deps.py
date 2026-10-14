"""Auth deps (tenant-scoped), in-memory sliding-window rate limiter, audit helper."""
import time
from collections import defaultdict
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.security import decode_token
from app.config import settings

_bearer = HTTPBearer(auto_error=False)

# ip -> {key: [timestamps]}
_hits: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))


def rate_limit(key: str, per_minute: int):
    """Dependency factory: max N requests/min per client IP per key."""
    def _check(request: Request):
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = _hits[ip][key]
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= per_minute:
            raise HTTPException(status_code=429, detail="Too many requests, slow down")
        window.append(now)
    return _check


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = decode_token(creds.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Account inactive or deleted")
    return user


def get_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def audit(db: Session, user_id: int, action: str,
          entity: str | None = None, entity_id: int | None = None,
          detail: str | None = None) -> None:
    from app.models import AuditEvent
    db.add(AuditEvent(user_id=user_id, action=action, entity=entity,
                      entity_id=entity_id, detail=(detail or "")[:2000]))
