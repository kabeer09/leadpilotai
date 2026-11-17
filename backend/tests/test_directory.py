"""Public directory API tests — landing-page free search + conversion gate.

Contracts under test:
- /meta and /search are public (no auth) but guest payloads NEVER contain
  emails, phones or websites — they stay locked behind sign-in (the
  free-for-limited-time conversion gate);
- every result carries fair attribution + the fair-participation
  disclaimer; only fair-attributed rows of the published operator account
  are visible — other tenants' rows are excluded (search) / 404 (reveal);
- invalid requests (422) never burn the free daily quota; the cap answers
  429 with a signup CTA;
- reveal requires an account (401 for guests) and returns per-row evidence
  (source_url + verification) plus an audit event;
- zero network: everything runs against the isolated test DB.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import User, Company, Contact, Source, AuditEvent
import app.api_directory as directory

client = TestClient(app)

_uid_n = {"n": 0}


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_day_hits():
    """The per-IP daily counter is module state — isolate it per test."""
    directory._day_hits.clear()
    yield
    directory._day_hits.clear()


def _uniq(prefix="dir"):
    _uid_n["n"] += 1
    return f"{prefix}-{_uid_n['n']}@example.com"


def _user(email):
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _uid_of(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).one().id
    finally:
        db.close()


def _publish(monkeypatch, email=None):
    """Create the operator account and point the module at it."""
    email = email or _uniq("pub")
    _user(email)
    uid = _uid_of(email)
    monkeypatch.setattr(directory, "PUBLISHED_USER_ID", uid)
    return uid


def _mk_source(user_id, url, name="Test Fair"):
    db = SessionLocal()
    try:
        s = Source(user_id=user_id, url=url, name=name, status="COMPLETED")
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id
    finally:
        db.close()


def _mk_company(user_id, name, **kw):
    db = SessionLocal()
    try:
        c = Company(user_id=user_id, name=name, **kw)
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id
    finally:
        db.close()


def _mk_contact(user_id, company_id, kind="EMAIL", value="a@example.com",
                source_url="https://example.com/contact"):
    db = SessionLocal()
    try:
        c = Contact(user_id=user_id, company_id=company_id, kind=kind,
                    value=value, normalized=value.lower(),
                    source_url=source_url)
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


AMB_URL = "https://ambiente.messefrankfurt.com/frankfurt/en/"
AMB_PROFILE = "https://ambiente.messefrankfurt.com/frankfurt/en/exhibitor/aurora.html"


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------

def test_meta_public_totals_and_shape(monkeypatch):
    uid = _publish(monkeypatch)
    sid = _mk_source(uid, AMB_URL, "Ambiente Frankfurt")
    cid = _mk_company(
        uid, "Aurora Ceramic Works", source_id=sid, country="Germany",
        city="Frankfurt", product_summary="ceramic tableware",
        profile_url=AMB_PROFILE)
    _mk_contact(uid, cid, "EMAIL", "sales@aurora.example")
    _mk_contact(uid, cid, "PHONE", "+4915112345678")
    # published but NOT fair-attributed (google/research row) → excluded
    _mk_company(uid, "Random Google Row", source_id=None,
                profile_url="https://example.com/about")

    r = client.get("/api/directory/meta")   # no auth header
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["record_type"] == "fair_directory_listing"
    assert "NOT current buying/selling activity" in d["disclaimer"]
    assert "locked" in d["locked_note"].lower()
    assert d["total"] == 1
    assert d["with_email"] == 1
    assert d["with_phone"] == 1
    assert any(f["id"] == "ambiente" and f["count"] == 1
               for f in d["by_fair"])
    assert any(f["id"] == "ambiente" for f in d["fairs"])
    assert "Germany" in d["countries"]
    assert d["free_per_day"] == directory.FREE_SEARCHES_PER_DAY
    assert d["updated_at"]


# ---------------------------------------------------------------------------
# guest search — the free, locked tool
# ---------------------------------------------------------------------------

def test_guest_search_locks_contacts_and_attributes_fair(monkeypatch):
    uid = _publish(monkeypatch)
    sid = _mk_source(uid, AMB_URL, "Ambiente Frankfurt")
    cid = _mk_company(
        uid, "Aurora Ceramic Works", source_id=sid, country="Germany",
        city="Frankfurt", category="Housewares",
        product_summary="ceramic tableware and gifts",
        hall="4.0", booth="B12", website="https://aurora.example",
        profile_url=AMB_PROFILE)
    _mk_contact(uid, cid, "EMAIL", "sales@aurora-ceramic.example",
                source_url=AMB_PROFILE)
    _mk_contact(uid, cid, "PHONE", "+4915112345678")

    r = client.get("/api/directory/search", params={"q": "ceramic"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["record_type"] == "fair_directory_listing"
    assert "NOT current buying/selling activity" in d["disclaimer"]
    assert d["source"]["record_type"] == "fair_directory_listing"
    assert d["total"] == 1
    item = d["items"][0]
    assert item["name"] == "Aurora Ceramic Works"
    assert item["fair"] == "Ambiente Frankfurt"
    assert item["source_url"] == AMB_PROFILE
    assert item["hall"] == "4.0" and item["booth"] == "B12"
    assert item["has_email"] is True and item["has_phone"] is True
    # locked for guests — the conversion gate:
    assert item["emails"] == [] and item["phones"] == []
    assert item["website"] is None
    assert d["free"]["per_day"] == directory.FREE_SEARCHES_PER_DAY
    assert d["free"]["remaining"] == d["free"]["per_day"] - 1
    # the locked values must not appear ANYWHERE in the guest payload
    assert "sales@aurora-ceramic.example" not in r.text
    assert "+4915112345678" not in r.text
    assert "https://aurora.example" not in r.text


def test_guest_search_excludes_other_tenants(monkeypatch):
    _publish(monkeypatch)
    other = _uniq("other")
    _user(other)
    other_uid = _uid_of(other)
    sid = _mk_source(other_uid, AMB_URL, "Ambiente Frankfurt")
    _mk_company(other_uid, "Foreign Ceramic Co", source_id=sid,
                product_summary="ceramic")

    r = client.get("/api/directory/search", params={"q": "ceramic"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 0
    assert all(i["name"] != "Foreign Ceramic Co" for i in d["items"])


def test_search_validation_never_burns_quota(monkeypatch):
    _publish(monkeypatch)
    # 1-character query
    r = client.get("/api/directory/search", params={"q": "a"})
    assert r.status_code == 422
    assert "2+ characters" in r.json()["detail"]
    # no query and no filter at all
    r = client.get("/api/directory/search")
    assert r.status_code == 422
    # unknown fair filter
    r = client.get("/api/directory/search",
                   params={"q": "ceramic", "fair": "nope"})
    assert r.status_code == 422
    assert "Unknown fair" in r.json()["detail"]
    # none of the rejected requests consumed the free daily allowance
    assert directory._day_hits == {}


def test_search_fair_and_country_filters(monkeypatch):
    uid = _publish(monkeypatch)
    sid = _mk_source(uid, AMB_URL, "Ambiente Frankfurt")
    _mk_company(uid, "Aurora Ceramic Works", source_id=sid, country="Germany",
                product_summary="ceramic tableware", profile_url=AMB_PROFILE)

    r = client.get("/api/directory/search",
                   params={"q": "ceramic", "fair": "ambiente"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1

    # country filter alone (no q) is a valid search
    r = client.get("/api/directory/search", params={"country": "germany"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1

    # wrong country → zero hits, still a legitimate search
    r = client.get("/api/directory/search", params={"country": "france"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_daily_free_limit_429_with_signup_cta(monkeypatch):
    uid = _publish(monkeypatch)
    sid = _mk_source(uid, AMB_URL, "Ambiente Frankfurt")
    _mk_company(uid, "Aurora Ceramic Works", source_id=sid,
                product_summary="ceramic", profile_url=AMB_PROFILE)
    monkeypatch.setattr(directory, "FREE_SEARCHES_PER_DAY", 2)

    r1 = client.get("/api/directory/search", params={"q": "ceramic"})
    r2 = client.get("/api/directory/search", params={"q": "ceramic"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["free"]["remaining"] == 1
    assert r2.json()["free"]["remaining"] == 0

    r3 = client.get("/api/directory/search", params={"q": "ceramic"})
    assert r3.status_code == 429
    detail = r3.json()["detail"]
    assert "limited time" in detail
    assert "account" in detail


# ---------------------------------------------------------------------------
# reveal — signed-in conversion
# ---------------------------------------------------------------------------

def test_reveal_requires_sign_in(monkeypatch):
    uid = _publish(monkeypatch)
    cid = _mk_company(uid, "Aurora Ceramic Works",
                      website="https://aurora.example")
    r = client.get(f"/api/directory/company/{cid}")
    assert r.status_code == 401


def test_reveal_signed_in_returns_evidence_and_audits(monkeypatch):
    uid = _publish(monkeypatch)
    cid = _mk_company(
        uid, "Aurora Ceramic Works", website="https://aurora.example",
        country="Germany", product_summary="ceramic tableware",
        profile_url=AMB_PROFILE)
    _mk_contact(uid, cid, "EMAIL", "sales@aurora-ceramic.example",
                source_url=AMB_PROFILE)

    viewer_email = _uniq("viewer")
    viewer = _user(viewer_email)
    r = client.get(f"/api/directory/company/{cid}", headers=_auth(viewer))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["record_type"] == "fair_directory_listing"
    assert "NOT current buying/selling activity" in d["disclaimer"]
    assert d["company"]["website"] == "https://aurora.example"
    assert d["company"]["fair"] == "Ambiente Frankfurt"
    assert d["company"]["source_url"] == AMB_PROFILE
    assert len(d["contacts"]) == 1
    ct = d["contacts"][0]
    assert ct["value"] == "sales@aurora-ceramic.example"
    assert ct["source_url"] == AMB_PROFILE
    assert ct["verification"] in ("scraped", "verified", "invalid")
    assert "evidence_note" in d

    viewer_uid = _uid_of(viewer_email)
    db = SessionLocal()
    try:
        ev = (db.query(AuditEvent)
              .filter(AuditEvent.user_id == viewer_uid,
                      AuditEvent.action == "directory_reveal",
                      AuditEvent.entity_id == cid)
              .first())
        assert ev is not None, "reveal must be audited"
    finally:
        db.close()


def test_reveal_never_exposes_other_tenants(monkeypatch):
    _publish(monkeypatch)
    other = _uniq("tenant")
    _user(other)
    other_uid = _uid_of(other)
    cid = _mk_company(other_uid, "Secret Other Co",
                      website="https://secret.example")

    viewer = _user(_uniq("viewer2"))
    r = client.get(f"/api/directory/company/{cid}", headers=_auth(viewer))
    assert r.status_code == 404

    # guests are refused before any lookup happens
    assert client.get(f"/api/directory/company/{cid}").status_code == 401
