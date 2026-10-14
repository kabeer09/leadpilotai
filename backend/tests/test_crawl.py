"""Phase 2+7: SSRF blocks, extraction honesty, tenant isolation of crawls."""
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Company
from app.ssrf import validate_url
from app.crawler import extract_page

client = TestClient(app)


def _user(email="crawl@test.example"):
    r = client.post("/api/auth/signup", json={"email": email, "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_ssrf_blocks_private_and_metadata():
    for bad in ["http://localhost:8000/x", "http://127.0.0.1/", "http://169.254.169.254/",
                "http://10.0.0.5/", "http://192.168.1.1/", "ftp://example.com/",
                "http://user:pass@example.com/"]:
        _, err = validate_url(bad)
        assert err, bad
    _, err = validate_url("https://example.com/page")
    assert err is None


def test_crawl_rejects_blocked_url():
    tokens = _user("crawl1@test.example")
    r = client.post("/api/crawls", json={"url": "http://127.0.0.1:9/"},
                    headers=_auth(tokens))
    assert r.status_code == 422


def test_crawl_requires_auth():
    r = client.post("/api/crawls", json={"url": "https://example.com/"})
    assert r.status_code in (401, 403)


def test_multi_email_extraction_with_source():
    html = """<html><head><title>Acme Gifts</title>
        <meta name='description' content='Wholesale gifts'></head>
        <body>Mail us at sales@acme.example or support@acme.example.
        Call +1-555-0100. Find us on WhatsApp +1-555-0199 today.</body></html>"""
    filler = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 12
    html = html.replace("Call +1-555-0100.", "Call +1-555-0100. " + filler)
    data = extract_page("https://acme.example/contact", html)
    assert "sales@acme.example" in data["emails"]
    assert "support@acme.example" in data["emails"]
    assert "+15550199" in data["whatsapp"]  # explicitly marked
    assert "+15550100" in data["phones"]
    assert "+15550100" not in data["whatsapp"]  # ordinary number not labeled WhatsApp


def test_tenant_isolation_between_users():
    a = _user("isoA@test.example")
    b = _user("isoB@test.example")
    # A creates a crawl for a blocked-safe URL? Use example.com (allowed by SSRF,
    # fetch may fail offline — job still created and tenant-scoped).
    ra = client.post("/api/crawls", json={"url": "https://example.com/"},
                     headers=_auth(a))
    assert ra.status_code == 201
    job_id = ra.json()["id"]
    # B cannot see A's job
    rb = client.get(f"/api/crawls/{job_id}", headers=_auth(b))
    assert rb.status_code == 404
    # B's list is empty of A's rows
    lst = client.get("/api/crawls", headers=_auth(b)).json()
    assert all(j["id"] != job_id for j in lst)
    # A can cancel own job
    rc = client.post(f"/api/crawls/{job_id}/cancel", headers=_auth(a))
    assert rc.status_code == 200


def test_companies_endpoint_tenant_scoped():
    from app.models import Company as _C
    a = _user("coA@test.example")
    b = _user("coB@test.example")
    # seed one company under A's account via crawl-save path
    db = SessionLocal()
    try:
        from app.models import User as _U
        uid_a = db.query(_U).filter(_U.email == "coa@test.example").first().id
        db.add(_C(user_id=uid_a, name="A Private Co"))
        db.commit()
    finally:
        db.close()
    ra = client.get("/api/companies", headers=_auth(a)).json()
    assert any(c["name"] == "A Private Co" for c in ra["items"])
    rb = client.get("/api/companies", headers=_auth(b)).json()
    assert all(c["name"] != "A Private Co" for c in rb["items"])
    # B cannot fetch A's company directly either
    cid = next(c["id"] for c in ra["items"] if c["name"] == "A Private Co")
    assert client.get(f"/api/companies/{cid}", headers=_auth(b)).status_code == 404
    db = SessionLocal()
    try:
        db.query(_C).filter(_C.name == "A Private Co").delete()
        db.commit()
    finally:
        db.close()
