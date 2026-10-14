"""Phase 1 + 7: auth, hashing, tenant isolation, rate limits."""
import time
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import User

client = TestClient(app)
_n = [0]


def _email(prefix="u"):
    _n[0] += 1
    return f"{prefix}{_n[0]}_{int(time.time()*1000)}@test.example"


def _signup(email=None, password="TestPass123"):
    email = email or _email()
    r = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return email, r.json()


def test_signup_login_me_logout_refresh():
    email, tokens = _signup()
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == email
    # refresh rotation
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    # old refresh token dead
    r2 = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r2.status_code == 401
    # logout revokes
    new = r.json()
    lo = client.post("/api/auth/logout", json={"refresh_token": new["refresh_token"]},
                     headers={"Authorization": f"Bearer {new['access_token']}"})
    assert lo.status_code == 200
    r3 = client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r3.status_code == 401


def test_password_hashing_and_invalid_login():
    email, _ = _signup()
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        assert u.password_hash != "TestPass123" and len(u.password_hash) > 30
    finally:
        db.close()
    bad = client.post("/api/auth/login", json={"email": email, "password": "WrongPass9"})
    assert bad.status_code == 401
    bad2 = client.post("/api/auth/login", json={"email": "nobody@test.example", "password": "WrongPass9"})
    assert bad2.status_code == 401


def test_protected_endpoints_need_token():
    assert client.get("/api/auth/me").status_code in (401, 403)
    assert client.get("/api/companies").status_code in (401, 403, 404)


def test_password_reset_flow():
    email, _ = _signup()
    r = client.post("/api/auth/password-reset/request", json={"email": email})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        assert u.reset_token
        token = u.reset_token
    finally:
        db.close()
    r2 = client.post("/api/auth/password-reset/confirm",
                     json={"token": token, "new_password": "NewPass456"})
    assert r2.status_code == 200
    ok = client.post("/api/auth/login", json={"email": email, "password": "NewPass456"})
    assert ok.status_code == 200


def test_signup_rate_limited():
    blocked = 0
    for _ in range(8):
        r = client.post("/api/auth/signup",
                        json={"email": _email("rl"), "password": "TestPass123"})
        if r.status_code == 429:
            blocked += 1
    assert blocked >= 1
