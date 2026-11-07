"""GB lead -> Companies CRM sync: identity, fill-only, evidence sources,
idempotency, tenant isolation, endpoint backfill, e2e search mirror.

Honesty contracts under test:
- every Contact row carries the source_url the value was published on;
- fill-only: an existing Company's crawled facts are never overwritten;
- re-sync never duplicates company/contact/lead rows;
- user B can never sync user A's campaigns or leads;
- all Places responses are mocked — zero network, zero credits.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import User, Company, Contact, Lead
from app.models_gbleads import SearchCampaign, BusinessLead, LeadEvidence
from app import gbleads_jobs
from app import config as cfg
from app.gb_to_companies import sync_lead_to_company
from app.gb_enricher import enrich_lead

client = TestClient(app)


# ---------------------------------------------------------------------------
# helpers (mirrored from test_gbleads.py — tests stay self-contained)
# ---------------------------------------------------------------------------

def _user(email):
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _uid(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email.lower()).first().id
    finally:
        db.close()


@pytest.fixture(autouse=True)
def gb_env(monkeypatch):
    """Deterministic, offline environment for every GB test."""
    monkeypatch.setattr(cfg.settings, "GOOGLE_MAPS_API_KEY", "test-key-not-real")
    monkeypatch.setattr(cfg.settings, "GB_REQUEST_DELAY", 0.0)
    monkeypatch.setattr(cfg.settings, "GB_DAILY_API_CALLS", 500)
    yield


def _place(pid, name, city=None, country=None, website=None, phone=None,
           rating=None, reviews=None, category=None, maps_url=None):
    return {
        "place_id": pid, "name": name, "address": None, "city": city,
        "state": None, "country": country, "latitude": None, "longitude": None,
        "website": website, "phone": phone, "primary_type": category,
        "business_category": category, "types": [],
        "google_maps_url": maps_url, "rating": rating,
        "review_count": reviews, "business_status": None, "description": None,
    }


class FakePlaces:
    """In-memory Places provider — zero network, zero cost."""

    name = "fake_places"

    def __init__(self, results=None, fail=None, details=None):
        self.results = results or []
        self.fail = fail
        self.details_result = details
        self.calls = 0
        self.queries = []

    def search(self, query, max_results=20, context=None):
        self.queries.append(query)
        self.calls += 1
        if self.fail:
            raise self.fail
        return [dict(r) for r in self.results[:max_results]]

    def details(self, place_id):
        self.calls += 1
        return self.details_result


def _wait_search(campaign_id, timeout=15.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = SessionLocal()
        try:
            status = db.query(SearchCampaign.status).filter(
                SearchCampaign.id == campaign_id).scalar()
        finally:
            db.close()
        if status not in ("PENDING", "RUNNING"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"search {campaign_id} did not finish in {timeout}s")


def _seed_lead(user_id, campaign_id=None, **kw):
    db = SessionLocal()
    try:
        lead = BusinessLead(
            user_id=user_id, campaign_id=campaign_id,
            company_name=kw.get("company_name", "Seeded Co"),
            place_id=kw.get("place_id"),
            source_provider=kw.get("source_provider", "google_places"),
            website=kw.get("website"),
            website_domain=kw.get("website_domain"),
            email=kw.get("email"), phone=kw.get("phone"),
            country=kw.get("country"), city=kw.get("city"),
            address=kw.get("address"),
            business_category=kw.get("business_category"),
            business_description=kw.get("business_description"),
            google_maps_url=kw.get("google_maps_url"),
            business_type="Unknown", relevance_score=50,
            relevance_label="Potential Match",
            enrichment_status=kw.get("enrichment_status", "NOT_ENRICHED"),
            lead_status="NEW",
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead.id
    finally:
        db.close()


def _add_evidence(lead_id, user_id, field, value, source_url,
                  method="text_regex", confidence=0.9):
    db = SessionLocal()
    try:
        db.add(LeadEvidence(user_id=user_id, lead_id=lead_id,
                            field_name=field, field_value=value,
                            source_url=source_url,
                            extraction_method=method, confidence=confidence))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. sync creates company + contacts with evidence source URLs
# ---------------------------------------------------------------------------

def test_sync_creates_company_contacts_and_lead_row():
    _user("gb2c-basic@test.example")
    uid = _uid("gb2c-basic@test.example")
    lid = _seed_lead(
        uid, company_name="Ilford Kitchenware House",
        website="https://ilfordkitchen.example",
        website_domain="ilfordkitchen.example",
        email="sales@ilfordkitchen.example", phone="020 8555 1234",
        country="United Kingdom", city="Ilford",
        address="12 High Road, Ilford IG1 1AA",
        business_category="Kitchenware store",
        business_description="Kitchenware and cookware retailer",
        google_maps_url="https://www.openstreetmap.org/node/12345")
    _add_evidence(lid, uid, "email", "sales@ilfordkitchen.example",
                  "https://ilfordkitchen.example/contact")
    _add_evidence(lid, uid, "phone", "020 8555 1234",
                  "https://ilfordkitchen.example/contact")

    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        company = sync_lead_to_company(db, lead)
        db.commit()

        assert company is not None
        assert company.name == "Ilford Kitchenware House"
        assert company.website == "https://ilfordkitchen.example"
        assert company.city == "Ilford"
        assert company.country == "United Kingdom"
        assert company.address == "12 High Road, Ilford IG1 1AA"
        assert company.category == "Kitchenware store"
        assert company.product_summary == "Kitchenware and cookware retailer"
        assert company.profile_url == "https://www.openstreetmap.org/node/12345"
        assert company.lifecycle == "active"

        contacts = db.query(Contact).filter(
            Contact.company_id == company.id).all()
        emails = [c for c in contacts if c.kind == "EMAIL"]
        phones = [c for c in contacts if c.kind == "PHONE"]
        assert len(emails) == 1 and len(phones) == 1
        # evidence rows own the exact source page
        assert emails[0].source_url == "https://ilfordkitchen.example/contact"
        assert phones[0].source_url == "https://ilfordkitchen.example/contact"
        assert emails[0].is_primary is True
        # UK number resolves to strict E.164
        assert phones[0].normalized.startswith("+44")

        # pipeline Lead row exists for the CRM funnel
        lead_row = db.query(Lead).filter(Lead.company_id == company.id).first()
        assert lead_row is not None and lead_row.status == "NEW"
        assert lead_row.score > 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. idempotent re-sync: no duplicate companies/contacts/leads
# ---------------------------------------------------------------------------

def test_sync_is_idempotent():
    _user("gb2c-idem@test.example")
    uid = _uid("gb2c-idem@test.example")
    lid = _seed_lead(uid, company_name="Repeat Sync Ltd",
                     website="https://repeat.example",
                     email="hello@repeat.example", phone="+44 20 7946 0102",
                     country="United Kingdom",
                     google_maps_url="https://maps.example/place/repeat")
    _add_evidence(lid, uid, "email", "hello@repeat.example",
                  "https://repeat.example/about")

    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        sync_lead_to_company(db, lead)
        db.commit()
        sync_lead_to_company(db, lead)
        db.commit()
        sync_lead_to_company(db, lead)
        db.commit()

        companies = db.query(Company).filter(
            Company.user_id == uid, Company.name == "Repeat Sync Ltd").all()
        assert len(companies) == 1
        cid = companies[0].id
        assert db.query(Contact).filter(Contact.company_id == cid).count() == 2
        assert db.query(Lead).filter(Lead.company_id == cid).count() == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. fill-only: existing crawled facts are never overwritten
# ---------------------------------------------------------------------------

def test_sync_fill_only_never_overwrites():
    _user("gb2c-fill@test.example")
    uid = _uid("gb2c-fill@test.example")
    lid = _seed_lead(
        uid, company_name="Fill Only Co",
        website="https://new-crawl.example",
        city="Ilford", country="United Kingdom",
        business_category="New Category",
        google_maps_url="https://maps.example/place/fillonly")
    db = SessionLocal()
    try:
        # pre-existing row (e.g. from the fair crawler) with its own facts
        existing = Company(
            user_id=uid, name="Fill Only Co",
            website="https://old-site.example",
            product_summary="Original summary",
            category="Old Category", city="London",
            profile_url="https://maps.example/place/fillonly")
        db.add(existing)
        db.commit()

        lead = db.query(BusinessLead).get(lid)
        company = sync_lead_to_company(db, lead)
        db.commit()

        assert company.id == existing.id  # same row, not a second one
        db.refresh(existing)
        assert existing.website == "https://old-site.example"      # kept
        assert existing.product_summary == "Original summary"      # kept
        assert existing.category == "Old Category"                 # kept
        assert existing.city == "London"                           # kept
        # missing facts get completed
        assert existing.country == "United Kingdom"                # filled
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. endpoint backfills a whole campaign into Companies
# ---------------------------------------------------------------------------

def test_endpoint_backfills_campaign_into_companies():
    t = _user("gb2c-endpoint@test.example")
    uid = _uid("gb2c-endpoint@test.example")
    db = SessionLocal()
    try:
        c = SearchCampaign(user_id=uid, campaign_name="Backfill me",
                           keywords=json.dumps(["kitchenware"]),
                           countries=json.dumps(["United Kingdom"]),
                           cities=json.dumps(["Ilford"]), status="COMPLETED")
        db.add(c)
        db.commit()
        db.refresh(c)
        cid = c.id
    finally:
        db.close()

    l1 = _seed_lead(uid, cid, company_name="Backfill One Ltd",
                    website="https://one.example", email="a@one.example",
                    phone="+44 20 7946 0101", country="United Kingdom")
    l2 = _seed_lead(uid, cid, company_name="Backfill Two Ltd",
                    website="https://two.example", phone="+44 20 7946 0102",
                    country="United Kingdom")
    _add_evidence(l1, uid, "email", "a@one.example", "https://one.example/")

    r = client.post("/api/gbleads/save-to-companies",
                    json={"campaign_id": cid}, headers=_auth(t))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 2 and body["synced"] == 2
    assert body["errors"] == []

    # the Companies list now carries the details
    r = client.get("/api/companies?search=Backfill", headers=_auth(t))
    assert r.status_code == 200
    items = {i["name"]: i for i in r.json()["items"]}
    assert "Backfill One Ltd" in items and "Backfill Two Ltd" in items
    one = items["Backfill One Ltd"]
    assert one["emails"] == ["a@one.example"]
    assert one["phones"] and one["phones"][0].startswith("+44")
    assert one["website"] == "https://one.example"

    # second run: still 2 synced, still 2 companies (idempotent)
    r = client.post("/api/gbleads/save-to-companies",
                    json={"campaign_id": cid}, headers=_auth(t))
    assert r.json()["synced"] == 2
    r = client.get("/api/companies?search=Backfill", headers=_auth(t))
    assert r.json()["total"] == 2


# ---------------------------------------------------------------------------
# 5. tenant isolation: user B can't touch user A's data
# ---------------------------------------------------------------------------

def test_endpoint_tenant_isolation():
    ta = _user("gb2c-iso-a@test.example")
    tb = _user("gb2c-iso-b@test.example")
    uid_a = _uid("gb2c-iso-a@test.example")

    db = SessionLocal()
    try:
        c = SearchCampaign(user_id=uid_a, campaign_name="A campaign",
                           keywords=json.dumps(["kitchenware"]),
                           countries=json.dumps(["United Kingdom"]),
                           status="COMPLETED")
        db.add(c)
        db.commit()
        db.refresh(c)
        cid_a = c.id
    finally:
        db.close()
    lead_a = _seed_lead(uid_a, cid_a, company_name="A Owned Co",
                        website="https://a-owned.example")

    # user B guessing user A's campaign id -> 404
    r = client.post("/api/gbleads/save-to-companies",
                    json={"campaign_id": cid_a}, headers=_auth(tb))
    assert r.status_code == 404

    # user B guessing user A's lead id -> 404
    r = client.post("/api/gbleads/save-to-companies",
                    json={"lead_ids": [lead_a]}, headers=_auth(tb))
    assert r.status_code == 404

    # and nothing leaked into B's Companies list
    r = client.get("/api/companies", headers=_auth(tb))
    assert r.json()["total"] == 0

    # user A still owns the row (and syncing works for the owner)
    r = client.post("/api/gbleads/save-to-companies",
                    json={"lead_ids": [lead_a]}, headers=_auth(ta))
    assert r.status_code == 200 and r.json()["synced"] == 1


# ---------------------------------------------------------------------------
# 6. enrichment: contacts crawled from the website land in Companies too
# ---------------------------------------------------------------------------

def test_enrichment_syncs_site_contacts_into_companies(monkeypatch):
    # fake .example hosts don't resolve — bypass DNS while testing extraction
    monkeypatch.setattr("app.gb_enricher.validate_url", lambda u: (u, None))
    _user("gb2c-enrich@test.example")
    uid = _uid("gb2c-enrich@test.example")
    lid = _seed_lead(uid, company_name="Enriched Sync Ltd",
                     website="https://enriched.example",
                     website_domain="enriched.example",
                     country="United Kingdom", city="Ilford")
    html = ("<html><head><title>Enriched Sync Ltd</title>"
            '<meta name="description" content="Kitchenware specialists in Ilford.">'
            "</head><body>"
            "<h1>Enriched Sync Ltd</h1>"
            "<p>Kitchenware specialists.</p>"
            '<a href="mailto:sales@enriched.example">Email us</a>'
            '<a href="tel:+442079460000">Call us</a>'
            "</body></html>")

    def fetcher(url):
        if url.endswith("/robots.txt"):
            return (404, "", url)
        return (200, html, url)

    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        res = enrich_lead(lead, db, uid, fetcher=fetcher, provider=None,
                          campaign=None)
        assert res["emails"] >= 1 and res["phones"] >= 1

        company = db.query(Company).filter(
            Company.user_id == uid,
            Company.name == "Enriched Sync Ltd").first()
        assert company is not None
        assert company.product_summary  # site description completed the row

        contacts = db.query(Contact).filter(
            Contact.company_id == company.id).all()
        emails = [c for c in contacts if c.kind == "EMAIL"]
        phones = [c for c in contacts if c.kind == "PHONE"]
        assert [e.value for e in emails] == ["sales@enriched.example"]
        assert emails[0].source_url.startswith("https://enriched.example")
        assert phones and phones[0].normalized.startswith("+44")
        assert phones[0].source_url.startswith("https://enriched.example")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 7. e2e: a live search mirrors its leads into Companies automatically
# ---------------------------------------------------------------------------

def test_search_automaticly_mirrors_leads_into_companies(monkeypatch):
    t = _user("gb2c-e2e@test.example")
    fake = FakePlaces(results=[_place(
        "p1", "Ilford Kitchenware Stores", city="Ilford",
        country="United Kingdom", website="https://ilford-kitchen.example",
        phone="+44 20 7946 0999", category="Kitchenware store",
        maps_url="https://www.openstreetmap.org/node/777")])
    monkeypatch.setattr(gbleads_jobs, "get_places_provider", lambda: fake)

    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Kitchenware Ilford",
        "keywords": ["kitchenware"],
        "countries": ["United Kingdom"], "cities": ["Ilford"],
        "max_results": 10, "enrich": False, "start": True,
    }, headers=_auth(t))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert _wait_search(cid) == "COMPLETED"

    r = client.get("/api/companies?search=Ilford Kitchenware",
                   headers=_auth(t))
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["name"] == "Ilford Kitchenware Stores"
    assert item["website"] == "https://ilford-kitchen.example"
    assert item["city"] == "Ilford"
    assert item["country"] == "United Kingdom"
    assert item["category"] == "Kitchenware store"
    assert item["phones"] and item["phones"][0].startswith("+44")
    assert item["contact_complete"] is True
