"""Google OAuth: config gating, state CSRF, account link, Gmail provider (mocked HTTP)."""
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import User

client = TestClient(app)


def test_google_login_unconfigured_without_env(monkeypatch):
    from app import config as cfg
    monkeypatch.setattr(cfg.settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(cfg.settings, "GOOGLE_CLIENT_SECRET", "")
    r = client.get("/api/auth/google/login")
    assert r.status_code == 409


def _enable_google(monkeypatch):
    from app import config as cfg
    monkeypatch.setattr(cfg.settings, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(cfg.settings, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(cfg.settings, "GOOGLE_REDIRECT_URI",
                        "http://127.0.0.1:8002/api/auth/google/callback")


def _mock_google(monkeypatch, email="guser@test.example", sub="google-sub-1",
                 refresh="gmail-rt-1"):
    import app.api_google as g

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_post(url, data=None, **k):
        assert "token" in url
        assert data["code"] == "authcode-123"
        return FakeResp({"access_token": "ya29-test",
                         "refresh_token": refresh})

    def fake_get(url, headers=None, **k):
        return FakeResp({"sub": sub, "email": email, "email_verified": True,
                         "name": "G User"})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)


def _run_callback(monkeypatch, email="guser@test.example"):
    _enable_google(monkeypatch)
    _mock_google(monkeypatch, email=email, sub=f"sub-for-{email}")
    login = client.get("/api/auth/google/login")
    assert login.status_code == 200
    import urllib.parse as up
    qs = up.urlparse(login.json()["auth_url"]).query
    params = dict(up.parse_qsl(qs))
    assert "gmail.send" in params["scope"].split(" ") or "gmail.send" in params["scope"]
    # no redirects followed: capture the redirect target
    r = client.get("/api/auth/google/callback",
                   params={"code": "authcode-123", "state": params["state"]},
                   follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    return r.headers["location"]


def test_google_signup_creates_verified_user(monkeypatch):
    loc = _run_callback(monkeypatch, email="gwelcome@test.example")
    assert "/auth/callback?code=" in loc
    code = loc.split("code=")[1]
    tokens = client.post("/api/auth/google/consume", json={"code": code})
    assert tokens.status_code == 200, tokens.text
    me = client.get("/api/auth/me",
                    headers={"Authorization": f"Bearer {tokens.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email_verified"] is True
    # code is single-use
    again = client.post("/api/auth/google/consume", json={"code": code})
    assert again.status_code == 401
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "gwelcome@test.example").first()
        assert u.google_sub == "sub-for-gwelcome@test.example"
        assert u.gmail_refresh_token == "gmail-rt-1"  # server-side only
    finally:
        db.close()


def test_google_links_existing_password_account(monkeypatch):
    client.post("/api/auth/signup",
                json={"email": "linkme@test.example", "password": "TestPass123"})
    loc = _run_callback(monkeypatch, email="linkme@test.example")
    code = loc.split("code=")[1]
    tokens = client.post("/api/auth/google/consume", json={"code": code})
    assert tokens.status_code == 200
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.email == "linkme@test.example").all()
        assert len(users) == 1  # linked, not duplicated
        assert users[0].google_sub == "sub-for-linkme@test.example"
    finally:
        db.close()


def test_google_callback_rejects_bad_state(monkeypatch):
    _enable_google(monkeypatch)
    _mock_google(monkeypatch)
    r = client.get("/api/auth/google/callback",
                   params={"code": "authcode-123", "state": "forged"},
                   follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "google=error" in r.headers["location"]


def test_gmail_provider_uses_stored_token(monkeypatch):
    import app.emailing as em
    import httpx

    class FakeResp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

        @property
        def text(self):
            return ""

    seen = {}

    def fake_post(url, **k):
        seen[url] = k
        if "oauth2" in url:
            return FakeResp({"access_token": "ya29-new"})
        return FakeResp({"id": "gmail-m1", "threadId": "t1"})

    monkeypatch.setattr(httpx, "post", fake_post)
    p = em.GmailApiProvider("stored-refresh-token")
    out = p.send("a@b.com", "Hi", "Body here")
    assert out["ok"] is True and out["provider_message_id"] == "gmail-m1"
    assert any("oauth2" in u for u in seen)

    # unconnected account fails loudly, never silently (gmail mode on)
    from app import config as cfg
    monkeypatch.setattr(cfg.settings, "EMAIL_PROVIDER", "gmail")
    p2 = em.get_provider_for_user(type("U", (), {"gmail_refresh_token": None})())
    out2 = p2.send("a@b.com", "Hi", "Body")
    assert out2["ok"] is False and "Google" in out2["error"]
