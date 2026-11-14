"""Phase 7: security + performance regression tests.

Contracts under test (master prompt, Phase 7):
- every non-public API route rejects unauthenticated calls with 401 —
  auth resolves BEFORE request-body/query validation;
- the public surface is a FROZEN allowlist of exactly 17 routes: adding
  a new unauthenticated endpoint fails these tests until it is reviewed
  and consciously added to PUBLIC below;
- admin RBAC: non-admins get 403 "Admin only", self-suspension and bad
  payloads are typed 422s, unknown users 404, and a suspended account
  immediately loses access (401) until reactivated;
- configuration endpoints report booleans/notes only: a sentinel secret
  planted in settings must never appear in any response body;
- the outbound trade search endpoint is rate limited (typed 429) so the
  upstream provider can never be hammered;
- list endpoints hydrate a page of 25 rows with a FIXED number of SQL
  SELECTs (measured baseline: leads=6, companies=10) — an N+1
  regression would need >= 25 + baseline queries;
- .env.example documents every Settings field, and secret fields hold
  placeholders only (never real values).

ALL external APIs are mocked or absent — zero network, zero credits.
"""
import re
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import api_trade
from app import config as cfg
from app import trade_providers
from app.config import Settings
from app.database import SessionLocal, engine
from app.main import app as fastapi_app
from app.models import Company, Contact, Lead, User
from app.security import create_access_token, decode_token

client = TestClient(fastapi_app, follow_redirects=False)

# The complete set of routes reachable WITHOUT a bearer token.
# Frozen by design: changing this list is a security review, not a fix.
PUBLIC = {
    ("GET", "/"),
    ("GET", "/api/auth/google/callback"),
    ("GET", "/api/auth/google/client-status"),
    ("GET", "/api/auth/google/login"),
    ("GET", "/api/auth/verify"),
    ("GET", "/api/health"),
    ("GET", "/api/import/template"),
    ("GET", "/api/sources"),
    ("GET", "/api/verify/gemini-status"),
    ("POST", "/api/auth/google/client"),
    ("POST", "/api/auth/google/consume"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/password-reset/confirm"),
    ("POST", "/api/auth/password-reset/request"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/signup"),
    ("POST", "/api/unsubscribe"),
}

_uid = {"n": 0}


def _api_routes() -> list[APIRoute]:
    return [r for r in fastapi_app.routes if isinstance(r, APIRoute)]


def _method(route: APIRoute) -> str:
    methods = sorted(route.methods - {"HEAD", "OPTIONS"})
    return "GET" if "GET" in methods else methods[0]


def _probe_path(route: APIRoute) -> str:
    """Substitute concrete values for path params ({id} -> 1)."""
    return re.sub(r"\{[^}]+\}", "1", route.path)


def _user() -> dict:
    _uid["n"] += 1
    r = client.post("/api/auth/signup",
                    json={"email": f"p7-{_uid['n']}@example.com",
                          "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t: dict) -> dict:
    return {"Authorization": f"Bearer {t['access_token']}"}


# ---------------------------------------------------------------------------
# 1+2) route auth sweep + frozen public allowlist
# ---------------------------------------------------------------------------

def test_every_non_public_route_rejects_unauthenticated_calls():
    """95/95 protected routes answer 401 without a token.

    Auth runs before body/query validation, so even POSTs without a
    payload and GETs with required query params must answer 401 (never
    422/200). A new route that answers anything else fails here.
    """
    routes = _api_routes()
    failures, checked = [], 0
    for route in routes:
        method = _method(route)
        if (method, route.path) in PUBLIC:
            continue
        resp = client.request(method, _probe_path(route))
        checked += 1
        if resp.status_code != 401:
            failures.append(f"{method} {route.path} -> {resp.status_code}")
    assert checked == len(routes) - len(PUBLIC), (
        f"sweep skipped the wrong number of routes: checked {checked}, "
        f"expected {len(routes) - len(PUBLIC)}")
    assert not failures, (
        "routes reachable without a token (should all be 401):\n"
        + "\n".join(failures))


def test_public_route_allowlist_is_exact_and_frozen():
    """Exactly the 17 reviewed routes are public — no more, no fewer.

    Probing every route unauthenticated and collecting the non-401 ones
    must reproduce PUBLIC bit-for-bit. A newly added public endpoint, or
    a route that silently stopped requiring auth, fails this test.
    """
    observed = set()
    for route in _api_routes():
        method = _method(route)
        if client.request(method, _probe_path(route)).status_code != 401:
            observed.add((method, route.path))
    assert observed == PUBLIC, (
        f"public surface changed — newly public: "
        f"{sorted(observed - PUBLIC)}; no longer public: "
        f"{sorted(PUBLIC - observed)}")


# ---------------------------------------------------------------------------
# 3) admin RBAC
# ---------------------------------------------------------------------------

def test_admin_rbac_suspend_and_reactivate():
    victim = _user()
    admin = _user()
    victim_id = decode_token(victim["access_token"])
    admin_id = decode_token(admin["access_token"])

    # Pin both roles explicitly: the FIRST account of a fresh DB is
    # auto-promoted to admin on signup, so the victim must be demoted
    # for the RBAC assertions to hold in any run order.
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == victim_id).update(
            {"is_admin": False})
        db.query(User).filter(User.id == admin_id).update(
            {"is_admin": True})
        db.commit()
    finally:
        db.close()

    # non-admins are refused on both admin routes
    r = client.get("/api/admin/users", headers=_auth(victim))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Admin only"
    r = client.post(f"/api/admin/users/{victim_id}/suspend",
                    json={"active": False}, headers=_auth(victim))
    assert r.status_code == 403

    # the admin sees the user list (including our victim)
    r = client.get("/api/admin/users", headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert any(u["id"] == victim_id for u in r.json())

    # typed validation error — missing "active"
    r = client.post(f"/api/admin/users/{victim_id}/suspend", json={},
                    headers=_auth(admin))
    assert r.status_code == 422

    # an admin cannot suspend themselves
    r = client.post(f"/api/admin/users/{admin_id}/suspend",
                    json={"active": False}, headers=_auth(admin))
    assert r.status_code == 422
    assert r.json()["detail"] == "Cannot suspend yourself"

    # unknown user id -> 404, not a fake success
    r = client.post("/api/admin/users/99999999/suspend",
                    json={"active": False}, headers=_auth(admin))
    assert r.status_code == 404

    # suspend: the account immediately loses access
    r = client.post(f"/api/admin/users/{victim_id}/suspend",
                    json={"active": False}, headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    r = client.get("/api/leads", headers=_auth(victim))
    assert r.status_code == 401, r.text
    assert "inactive" in r.json()["detail"].lower()

    # reactivate: access is restored, no lingering lockout
    r = client.post(f"/api/admin/users/{victim_id}/suspend",
                    json={"active": True}, headers=_auth(admin))
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True
    r = client.get("/api/leads", headers=_auth(victim))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 4) config endpoints never leak secret values
# ---------------------------------------------------------------------------

def test_config_endpoints_never_echo_secret_values(monkeypatch):
    """A sentinel planted in every secret setting must never be echoed.

    Covers the endpoints that report configuration state: they may say
    configured=true/false, provider names and notes — never a value.
    """
    session = _user()
    uid = decode_token(session["access_token"])

    sentinel = "p7-SENTINEL-SECRET-a1b2c3d4e5f6"
    secret_fields = (
        "SECRET_KEY", "SMTP_PASSWORD", "RESEND_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_CLIENT_SECRET", "UN_COMTRADE_API_KEY",
        "WHATSAPP_ACCESS_TOKEN", "GOOGLE_MAPS_API_KEY",
    )
    for field in secret_fields:
        monkeypatch.setattr(cfg.settings, field, sentinel)
    # Tokens are signed with SECRET_KEY at call time — re-sign so the
    # authenticated probes below still authenticate.
    signed = {"access_token": create_access_token(uid)}
    # Drop the trade-provider singleton so it cannot pin a pre-patch
    # key (monkeypatch restores the original instance afterwards).
    monkeypatch.setattr(trade_providers, "_provider", None)

    probes = [
        ("GET", "/api/health", None),
        ("GET", "/api/sources", None),
        ("GET", "/api/verify/gemini-status", None),
        ("GET", "/api/auth/google/client-status", None),
        ("GET", "/api/data-sources", _auth(signed)),
        ("GET", "/api/whatsapp/config", _auth(signed)),
        ("GET", "/api/gbleads/settings", _auth(signed)),
    ]
    for method, path, headers in probes:
        r = client.request(method, path, headers=headers or {})
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
        assert sentinel not in r.text, f"{path} leaked a secret value"


# ---------------------------------------------------------------------------
# 5) trade endpoints are rate limited
# ---------------------------------------------------------------------------

def test_trade_search_endpoint_is_rate_limited(monkeypatch):
    """GET /api/trade/search answers 429 past RATE_TRADE_PER_MIN.

    The limit is a route dependency, so it fires before the handler —
    the upstream provider is never called again once the window is spent.
    """
    class _FakeTrade:
        """Zero-network stand-in for the UN Comtrade provider."""

        name = "un_comtrade"

        def search(self, **kw):
            return {"rows": [], "truncated": False,
                    "source": {"name": "UN Comtrade",
                               "url": "https://comtradeapi.un.org",
                               "record_type": "aggregated_statistics",
                               "coverage": "Annual merchandise trade (HS)"}}

        def status(self):
            return {"key": "un_comtrade", "name": "UN Comtrade",
                    "status": "active"}

    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: _FakeTrade())

    tok = _user()
    per_min = cfg.settings.RATE_TRADE_PER_MIN
    codes = []
    for _ in range(per_min + 3):
        r = client.get("/api/trade/search",
                       params={"reporter_code": 826,
                               "period_from": "2023", "period_to": "2023"},
                       headers=_auth(tok))
        codes.append(r.status_code)
    assert codes[0] == 200, codes[:5]
    assert 429 in codes, f"expected a 429 within {per_min + 3} calls, got {codes}"


# ---------------------------------------------------------------------------
# 6) N+1 guard on the two heaviest list endpoints
# ---------------------------------------------------------------------------

def test_leads_and_companies_page_queries_are_batched():
    """25 rows must cost a FIXED number of SELECTs, not 25 + baseline.

    Measured baseline (SQLite, 25 rows): /api/leads = 6 SELECTs,
    /api/companies = 10. Bounds below leave headroom for additive
    queries while still failing a per-row hydration regression
    (which would need >= 25 + baseline).
    """
    tok = _user()
    uid = decode_token(tok["access_token"])

    db = SessionLocal()
    try:
        for i in range(25):
            c = Company(user_id=uid, name=f"P7 N1 Co {i}",
                        country="India", category="jewellery",
                        lifecycle="active")
            db.add(c)
            db.flush()
            db.add(Contact(user_id=uid, company_id=c.id, kind="EMAIL",
                           value=f"p7n1-{i}@example.com",
                           normalized=f"p7n1-{i}@example.com",
                           source_url="https://example.com/contact"))
            db.add(Lead(user_id=uid, company_id=c.id, status="NEW", score=50))
        db.commit()
    finally:
        db.close()

    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context,
               executemany=None):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        statements.clear()
        r = client.get("/api/leads", params={"page_size": 25},
                       headers=_auth(tok))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 25, body["total"]
        assert len(body["items"]) == 25
        lead_selects = len(statements)

        statements.clear()
        r = client.get("/api/companies", params={"page_size": 25},
                       headers=_auth(tok))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 25, body["total"]
        assert len(body["items"]) == 25
        company_selects = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert lead_selects <= 15, (
        f"/api/leads used {lead_selects} SELECTs for 25 rows "
        "(baseline 6) — hydration looks per-row (N+1)")
    assert company_selects <= 20, (
        f"/api/companies used {company_selects} SELECTs for 25 rows "
        "(baseline 10) — hydration looks per-row (N+1)")


# ---------------------------------------------------------------------------
# 7) .env.example completeness
# ---------------------------------------------------------------------------

def test_env_example_documents_every_setting_with_placeholders():
    """Every Settings field is documented in .env.example, and secret
    fields hold placeholders only — a real key committed to the example
    file fails here."""
    env_path = Path(__file__).resolve().parents[2] / ".env.example"
    assert env_path.is_file(), ".env.example is missing from the repo root"

    documented: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        documented[key.strip()] = value.strip()

    fields, keys = set(Settings.model_fields), set(documented)
    assert fields - keys == set(), (
        f"settings missing from .env.example: {sorted(fields - keys)}")
    assert keys - fields == set(), (
        f".env.example entries with no matching setting: "
        f"{sorted(keys - fields)}")

    allowed = {"", "change-me-to-a-long-random-string"}
    for key in ("SECRET_KEY", "SMTP_PASSWORD", "RESEND_API_KEY",
                "GEMINI_API_KEY", "GOOGLE_CLIENT_SECRET",
                "UN_COMTRADE_API_KEY", "WHATSAPP_ACCESS_TOKEN",
                "GOOGLE_MAPS_API_KEY"):
        assert documented[key] in allowed, (
            f".env.example {key} must be a placeholder, "
            f"got {documented[key]!r}")
