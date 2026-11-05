"""Google Business Lead Finder: query build, parsing, scoring, dedup,
enrichment evidence, campaigns, export, quota errors, job retries, isolation.

Honesty contracts under test:
- all Google responses are mocked — automated tests never spend credits;
- emails/phones are stored only when literally published (never guessed);
- every evidence row carries its exact source URL;
- Unknown business type when the site shows no evidence;
- quota/config errors surface as typed failures, never as "0 results";
- user B can never see or touch user A's campaigns/leads/jobs.
"""
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import User
from app.models_gbleads import SearchCampaign, BusinessLead, LeadEvidence, GBJob
from app import gbleads_jobs
from app import config as cfg
from app.google_places import (
    FIELD_MASK, GooglePlacesProvider, build_queries, parse_place,
    parse_search_response, humanize_type, PlacesConfigError, PlacesQuotaError,
)
from app.gb_scoring import (
    score_relevance, classify_business_type, normalize_domain,
    dedup_key_name_city, label_for,
)
from app.gb_enricher import extract_page, enrich_lead

client = TestClient(app)


# ---------------------------------------------------------------------------
# helpers
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
           rating=None, reviews=None, category=None):
    return {
        "place_id": pid, "name": name, "address": None, "city": city,
        "state": None, "country": country, "latitude": None, "longitude": None,
        "website": website, "phone": phone, "primary_type": category,
        "business_category": category, "types": [], "google_maps_url": None,
        "rating": rating, "review_count": reviews, "business_status": None,
        "description": None,
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
            website=kw.get("website"),
            website_domain=kw.get("website_domain"),
            email=kw.get("email"), phone=kw.get("phone"),
            country=kw.get("country"), city=kw.get("city"),
            business_category=kw.get("business_category"),
            business_type=kw.get("business_type", "Unknown"),
            relevance_score=kw.get("relevance_score", 50),
            relevance_label=kw.get("relevance_label", "Potential Match"),
            enrichment_status=kw.get("enrichment_status", "NOT_ENRICHED"),
            lead_status="NEW",
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. keyword + location query construction
# ---------------------------------------------------------------------------

def test_build_queries_keyword_by_location():
    qs = build_queries(["copper bottles", "brass decor"],
                       ["Australia"], None, ["Sydney", "Melbourne"])
    assert len(qs) == 4  # 2 keywords x 2 cities
    assert qs[0]["query"] == "copper bottles in Sydney, Australia"
    assert qs[1]["query"] == "copper bottles in Melbourne, Australia"
    assert {q["keyword"] for q in qs} == {"copper bottles", "brass decor"}
    assert all(q["country"] == "Australia" for q in qs)


def test_build_queries_country_only_and_no_location():
    qs = build_queries(["home decor"], ["United Kingdom"], None, [])
    assert qs[0]["query"] == "home decor in United Kingdom"
    qs = build_queries(["handicrafts"], [], "Rajasthan", [])
    assert qs[0]["query"] == "handicrafts in Rajasthan"
    qs = build_queries(["copper"], [], None, [])
    assert qs[0]["query"] == "copper"


def test_build_queries_includes_business_type_and_dedupes():
    qs = build_queries(["copper bottles"], ["Australia"], None,
                       ["Sydney", "Sydney"], ["Wholesaler"], [])
    assert len(qs) == 1
    assert qs[0]["query"] == "copper bottles Wholesaler in Sydney, Australia"


# ---------------------------------------------------------------------------
# 2-3. Google response parsing (missing fields included)
# ---------------------------------------------------------------------------

def test_parse_place_missing_optional_fields():
    p = parse_place({"id": "x1", "displayName": {"text": "Only Name"}})
    assert p["place_id"] == "x1"
    assert p["name"] == "Only Name"
    # everything optional stays None — never faked
    for key in ("website", "phone", "rating", "review_count",
                "google_maps_url", "city", "state", "country", "description"):
        assert p[key] is None, key


def test_parse_place_prefers_intl_phone_and_address_components():
    p = parse_place({
        "id": "p9", "displayName": {"text": "Café X"},
        "nationalPhoneNumber": "020 7946 0958",
        "internationalPhoneNumber": "+44 20 7946 0958",
        "addressComponents": [
            {"longText": "London", "types": ["locality"]},
            {"longText": "England", "types": ["administrative_area_level_1"]},
            {"longText": "United Kingdom", "types": ["country"]},
        ],
        "rating": 4.4, "userRatingCount": 12,
    })
    assert p["phone"] == "+44 20 7946 0958"
    assert (p["city"], p["state"], p["country"]) == (
        "London", "England", "United Kingdom")
    assert p["rating"] == 4.4 and p["review_count"] == 12


def test_parse_search_response_empty_and_humanize():
    assert parse_search_response({}) == []
    assert parse_search_response({"places": []}) == []
    assert humanize_type("hardware_store") == "Hardware store"
    assert humanize_type(None) is None


def test_field_mask_covers_spec_fields():
    for f in ("places.id", "places.displayName", "places.formattedAddress",
              "places.location", "places.websiteUri",
              "places.nationalPhoneNumber", "places.internationalPhoneNumber",
              "places.primaryType", "places.types", "places.googleMapsUri",
              "places.rating", "places.userRatingCount"):
        assert f in FIELD_MASK, f


# ---------------------------------------------------------------------------
# provider HTTP behaviour (mock transport — no network, no credits)
# ---------------------------------------------------------------------------

def test_provider_retries_transient_with_backoff():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="backend error")
        return httpx.Response(
            200, json={"places": [{"id": "p", "displayName": {"text": "X"}}]})

    sleeps = []
    prov = GooglePlacesProvider(api_key="k", max_retries=3,
                                transport=httpx.MockTransport(handler),
                                sleep=sleeps.append)
    out = prov.search("copper in Sydney", max_results=5)
    assert len(out) == 1 and out[0]["place_id"] == "p"
    assert len(attempts) == 3 and prov.calls == 3
    assert sleeps and sleeps[0] >= 2  # exponential backoff, not instant


def test_provider_config_error_never_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403, json={"error": {
            "message": "API key not valid. Please pass a valid API key."}})

    prov = GooglePlacesProvider(api_key="bad",
                                transport=httpx.MockTransport(handler),
                                sleep=lambda s: None)
    with pytest.raises(PlacesConfigError):
        prov.search("q")
    assert len(calls) == 1  # a bad key cannot heal on retry


def test_provider_quota_error_is_typed_and_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {
            "message": "RESOURCE_EXHAUSTED: Quota exceeded"}})

    prov = GooglePlacesProvider(api_key="k",
                                transport=httpx.MockTransport(handler),
                                sleep=lambda s: None)
    with pytest.raises(PlacesQuotaError):
        prov.search("q")
    assert len(calls) == 1


def test_provider_missing_key_raises_config_without_http():
    prov = GooglePlacesProvider(api_key="", sleep=lambda s: None)
    with pytest.raises(PlacesConfigError):
        prov.search("q")
    assert prov.calls == 0  # no request is attempted without a key


# ---------------------------------------------------------------------------
# 7. relevance scoring
# ---------------------------------------------------------------------------

def test_relevance_high_when_name_and_website_match():
    score, label, reasons = score_relevance(
        ["copper bottles"], "Sydney Copper Bottle Co", "Wholesaler",
        [("copper bottles and drinkware", "https://x.com/products")])
    assert score >= 60 and label in ("Relevant", "Highly Relevant")
    assert any("business name" in r for r in reasons)
    assert any("website" in r for r in reasons)


def test_relevance_zero_without_evidence():
    score, label, reasons = score_relevance(
        ["brass handicrafts"], "Random Cafe", "Restaurant",
        [("we serve food", "https://y.com")])
    assert score == 0 and label == "Low Relevance"
    assert reasons and "No keyword evidence" in reasons[0]


def test_relevance_label_bands():
    assert label_for(85) == "Highly Relevant"
    assert label_for(60) == "Relevant"
    assert label_for(40) == "Potential Match"
    assert label_for(39) == "Low Relevance"


def test_classify_unknown_without_evidence():
    btype, evidence = classify_business_type(
        [("Google listing", "Nice Cafe Restaurant")])
    assert btype == "Unknown" and evidence == []


def test_classify_manufacturer_from_site_evidence():
    btype, evidence = classify_business_type(
        [("We are a manufacturer of copper bottles", "https://a.com")])
    assert btype == "Manufacturer"
    assert evidence and "a.com" in evidence[0]


def test_normalize_domain_variants():
    assert normalize_domain("https://www.Example.com/shop/?a=1") == "example.com"
    assert normalize_domain("http://example.com") == "example.com"
    assert normalize_domain(None) is None
    assert normalize_domain("") is None


# ---------------------------------------------------------------------------
# 6. duplicate detection rules
# ---------------------------------------------------------------------------

def test_dedup_key_includes_location():
    a = dedup_key_name_city("ABC Traders", "Sydney", "Australia")
    b = dedup_key_name_city("ABC Traders", "Melbourne", "Australia")
    assert a != b  # same generic name, different city => different business
    assert dedup_key_name_city("", "Sydney", "Australia") is None


def test_place_id_unique_per_user():
    """place_id dedups per tenant but never blocks another tenant."""
    _user("gb-iso-a@test.example")
    _user("gb-iso-b@test.example")
    a = _uid("gb-iso-a@test.example")
    b = _uid("gb-iso-b@test.example")
    _seed_lead(a, place_id="dup-place", company_name="A's Co")
    _seed_lead(b, place_id="dup-place", company_name="B's Co")
    db = SessionLocal()
    try:
        assert db.query(BusinessLead).filter(
            BusinessLead.place_id == "dup-place").count() == 2
        with pytest.raises(Exception):
            _seed_lead(a, place_id="dup-place", company_name="A's dupe")
            db.commit()
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 9. campaign creation + search run (mocked provider)
# ---------------------------------------------------------------------------

def test_create_campaign_requires_api_key(monkeypatch):
    # GB_PROVIDER=google is an explicit opt-in: without a key there is no
    # provider at all, so the API must say so honestly (auto mode falls
    # back to the free OpenStreetMap provider instead — see osm tests).
    t = _user("gb-nokey@test.example")
    monkeypatch.setattr(cfg.settings, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setattr(cfg.settings, "GB_PROVIDER", "google")
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "X", "keywords": ["copper"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 10}, headers=_auth(t))
    assert r.status_code == 400
    assert "GOOGLE_MAPS_API_KEY" in r.json()["detail"]


def test_create_campaign_validates_keywords():
    t = _user("gb-nokeywords@test.example")
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "X", "keywords": ["  "],
        "max_results": 10}, headers=_auth(t))
    assert r.status_code == 422


def test_search_flow_creates_scored_leads(monkeypatch):
    t = _user("gb-search@test.example")
    fake = FakePlaces(results=[_place(
        "p1", "Copper Bottle Traders", city="Sydney", country="Australia",
        website="https://coppertraders.example", phone="+61 2 1234 5678",
        rating=4.5, reviews=120, category="Wholesaler")])
    monkeypatch.setattr(gbleads_jobs, "get_places_provider", lambda: fake)

    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Australia Copper Buyers",
        "keywords": ["copper bottles"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 20, "enrich": False, "start": True,
    }, headers=_auth(t))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert _wait_search(cid) == "COMPLETED"

    c = client.get(f"/api/gbleads/campaigns/{cid}", headers=_auth(t)).json()
    assert c["total_discovered"] == 1
    assert c["api_calls"] >= 1
    assert c["estimated_cost_usd"] > 0
    assert c["error"] is None

    leads = client.get("/api/gbleads/leads", headers=_auth(t)).json()
    assert leads["total"] == 1
    lead = leads["items"][0]
    assert lead["company_name"] == "Copper Bottle Traders"
    assert lead["relevance_score"] > 0 and lead["relevance_label"]
    assert lead["city"] == "Sydney" and lead["country"] == "Australia"

    detail = client.get(f"/api/gbleads/leads/{lead['id']}",
                        headers=_auth(t)).json()
    assert any("Google search" in reason for reason in detail["match_reasons"])


def test_search_dedupes_same_place_across_queries(monkeypatch):
    t = _user("gb-dedup@test.example")
    fake = FakePlaces(results=[
        _place("same-pid", "Dup Co", city="Sydney", country="Australia",
               website="https://dup.example")])
    monkeypatch.setattr(gbleads_jobs, "get_places_provider", lambda: fake)
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Dup test", "keywords": ["copper", "brass"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 40, "start": True,
    }, headers=_auth(t))
    cid = r.json()["id"]
    assert _wait_search(cid) == "COMPLETED"
    c = client.get(f"/api/gbleads/campaigns/{cid}", headers=_auth(t)).json()
    assert c["total_discovered"] == 1
    assert c["duplicate_count"] >= 1  # second query hit the same place


def test_search_discovery_filters_apply(monkeypatch):
    t = _user("gb-filters@test.example")
    fake = FakePlaces(results=[
        _place("low", "Low Rated Co", city="Sydney", country="Australia",
               rating=2.0, reviews=3),
        _place("good", "Top Rated Co", city="Sydney", country="Australia",
               rating=4.8, reviews=300),
    ])
    monkeypatch.setattr(gbleads_jobs, "get_places_provider", lambda: fake)
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Filtered", "keywords": ["copper"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 20, "filters": {"min_rating": 4.0},
        "start": True,
    }, headers=_auth(t))
    cid = r.json()["id"]
    assert _wait_search(cid) == "COMPLETED"
    leads = client.get("/api/gbleads/leads", headers=_auth(t)).json()
    assert leads["total"] == 1
    assert leads["items"][0]["company_name"] == "Top Rated Co"


def test_quota_error_marks_campaign_failed_with_honest_message(monkeypatch):
    t = _user("gb-quota@test.example")
    fake = FakePlaces(fail=PlacesQuotaError(
        "Google quota/billing limit hit (HTTP 429): RESOURCE_EXHAUSTED"))
    monkeypatch.setattr(gbleads_jobs, "get_places_provider", lambda: fake)
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Quota", "keywords": ["copper"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 10, "start": True,
    }, headers=_auth(t))
    cid = r.json()["id"]
    assert _wait_search(cid) == "FAILED"
    c = client.get(f"/api/gbleads/campaigns/{cid}", headers=_auth(t)).json()
    assert c["status"] == "FAILED"
    assert "quota" in (c["error"] or "").lower()
    assert c["api_errors"] >= 1


def test_daily_budget_guard_blocks_creation(monkeypatch):
    t = _user("gb-budget@test.example")
    monkeypatch.setattr(cfg.settings, "GB_DAILY_API_CALLS", 0)
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Over budget", "keywords": ["copper"],
        "max_results": 10, "start": True,
    }, headers=_auth(t))
    assert r.status_code == 429
    assert "budget" in r.json()["detail"].lower()


def test_pause_resume_cancel_state_machine(monkeypatch):
    t = _user("gb-states@test.example")
    fake = FakePlaces(results=[_place(
        "s1", "State Co", city="Sydney", country="Australia")])
    monkeypatch.setattr(gbleads_jobs, "get_places_provider", lambda: fake)

    # saved draft => PAUSED, no thread
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "States", "keywords": ["copper"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 10, "start": False,
    }, headers=_auth(t))
    cid = r.json()["id"]
    assert r.json()["status"] == "PAUSED"

    # resume runs to completion
    r = client.post(f"/api/gbleads/campaigns/{cid}/resume", headers=_auth(t))
    assert r.status_code == 200
    assert _wait_search(cid) == "COMPLETED"

    # COMPLETED is terminal: cancel and resume leave it alone
    r = client.post(f"/api/gbleads/campaigns/{cid}/cancel", headers=_auth(t))
    assert r.json()["status"] == "COMPLETED"
    r = client.post(f"/api/gbleads/campaigns/{cid}/resume", headers=_auth(t))
    assert r.json()["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# 4-5, 8. website enrichment + evidence (fake fetcher, no network)
# ---------------------------------------------------------------------------

HOME = """<html><head><title>Acme Copper Bottles</title>
<meta name="description" content="Handmade copper bottles and drinkware"></head>
<body><main><h1>Acme Copper Bottles</h1>
<p>We are a manufacturer of copper bottles for wholesale.</p>
<a href="/contact">Contact us</a> <a href="/products">Products</a>
<a href="mailto:info@acme.example">Email us</a>
<a href="tel:+14155551234">Call us</a>
</main></body></html>"""

CONTACT = """<html><head><title>Contact — Acme</title></head>
<body><main><p>Email our sales team: sales@acme.example</p>
<a href="https://wa.me/14155559999">Chat on WhatsApp</a>
<a href="https://www.linkedin.com/company/acme-copper">LinkedIn</a>
</main></body></html>"""

PRODUCTS = """<html><head><title>Products</title></head>
<body><main><h2>Products</h2>
<p>Copper bottles, copper drinkware, brass decor.</p></main></body></html>"""


def _fetcher(mapping):
    def fetch(url):
        if url in mapping:
            return mapping[url]
        if url.endswith("/robots.txt"):
            return (404, "", url)
        return (0, "", url)
    return fetch


def _mock_ssrf(monkeypatch):
    # fake .example hosts don't resolve — bypass DNS while testing extraction
    monkeypatch.setattr("app.gb_enricher.validate_url", lambda u: (u, None))


def test_enrich_extracts_published_contacts_with_evidence(monkeypatch):
    _mock_ssrf(monkeypatch)
    _user("gb-enrich@test.example")
    uid = _uid("gb-enrich@test.example")
    lid = _seed_lead(uid, company_name="Acme Copper Bottles",
                     website="https://acme.example", country="United States")
    # campaign keyword drives re-scoring
    db = SessionLocal()
    try:
        c = SearchCampaign(user_id=uid, campaign_name="kw",
                           keywords=json.dumps(["copper bottles"]),
                           cities=json.dumps([]),
                           countries=json.dumps(["United States"]),
                           status="COMPLETED")
        db.add(c); db.commit(); db.refresh(c)
        lead = db.query(BusinessLead).get(lid)
        lead.campaign_id = c.id
        db.commit()

        res = enrich_lead(lead, db, uid, fetcher=_fetcher({
            "https://acme.example": (200, HOME, "https://acme.example"),
            "https://acme.example/contact": (200, CONTACT,
                                             "https://acme.example/contact"),
            "https://acme.example/products": (200, PRODUCTS,
                                              "https://acme.example/products"),
        }), provider=None, campaign=None)

        assert res["pages"] >= 2
        db.refresh(lead)
        assert lead.enrichment_status == "ENRICHED"
        # role preference: sales@ beats info@ — both were published
        assert lead.email == "sales@acme.example"
        assert lead.phone and lead.phone.startswith("+1")
        assert lead.business_type == "Manufacturer"
        assert lead.relevance_score >= 60

        evs = db.query(LeadEvidence).filter(LeadEvidence.lead_id == lid).all()
        by_field = {}
        for e in evs:
            by_field.setdefault(e.field_name, []).append(e)

        sales = [e for e in by_field.get("email", [])
                 if e.field_value == "sales@acme.example"]
        assert sales, "sales@ must be extracted from the contact page"
        assert sales[0].source_url.endswith("/contact")
        assert sales[0].extraction_method == "text_regex"
        assert sales[0].confidence is not None

        info = [e for e in by_field.get("email", [])
                if e.field_value == "info@acme.example"]
        assert info and info[0].extraction_method == "mailto_link"
        assert info[0].source_url == "https://acme.example"

        assert by_field.get("whatsapp"), "published wa.me link must be kept"
        assert by_field.get("social_linkedin"), "published LinkedIn kept"
        # evidence never claims verification — method describes how found
        assert all(e.source_url for e in evs)
    finally:
        db.close()


def test_enrich_never_guesses_missing_email(monkeypatch):
    _mock_ssrf(monkeypatch)
    _user("gb-noguess@test.example")
    uid = _uid("gb-noguess@test.example")
    lid = _seed_lead(uid, company_name="No Contact Co",
                     website="https://bare.example")
    bare = "<html><head><title>Bare</title></head><body><main>" \
           "<p>A nice company.</p></main></body></html>"
    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        enrich_lead(lead, db, uid, fetcher=_fetcher({
            "https://bare.example": (200, bare, "https://bare.example")}))
        db.refresh(lead)
        assert lead.email is None  # nothing published => nothing stored
        assert lead.business_type == "Unknown"  # no evidence => honest Unknown
        evs = db.query(LeadEvidence).filter(
            LeadEvidence.lead_id == lid,
            LeadEvidence.field_name.in_(["email", "phone"])).all()
        assert evs == []
    finally:
        db.close()


def test_enrich_no_website_marks_no_website():
    _user("gb-noweb@test.example")
    uid = _uid("gb-noweb@test.example")
    lid = _seed_lead(uid, company_name="Directory Only", website=None)
    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        res = enrich_lead(lead, db, uid, fetcher=_fetcher({}))
        db.refresh(lead)
        assert lead.enrichment_status == "NO_WEBSITE"
        assert res["pages"] == 0
    finally:
        db.close()


def test_enrich_invalid_site_marks_failed():
    _user("gb-badsite@test.example")
    uid = _uid("gb-badsite@test.example")
    lid = _seed_lead(uid, company_name="Dead Site",
                     website="https://dead.example")
    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        enrich_lead(lead, db, uid, fetcher=_fetcher({}))  # everything fails
        db.refresh(lead)
        assert lead.enrichment_status == "FAILED"
    finally:
        db.close()


def test_enrich_respects_robots_disallow(monkeypatch):
    _mock_ssrf(monkeypatch)
    _user("gb-robots@test.example")
    uid = _uid("gb-robots@test.example")
    lid = _seed_lead(uid, company_name="Robots Co",
                     website="https://locked.example")
    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        enrich_lead(lead, db, uid, fetcher=_fetcher({
            "https://locked.example/robots.txt":
                (200, "User-agent: *\nDisallow: /",
                 "https://locked.example/robots.txt"),
            # page would be fine, but robots forbids it
            "https://locked.example": (200, HOME, "https://locked.example"),
        }))
        db.refresh(lead)
        assert lead.enrichment_status == "FAILED"
        assert lead.email is None  # never read a disallowed page
    finally:
        db.close()


def test_enrich_blocked_internal_url_fails_honestly():
    # real validate_url (no monkeypatch): loopback is SSRF-blocked
    _user("gb-ssrf@test.example")
    uid = _uid("gb-ssrf@test.example")
    lid = _seed_lead(uid, company_name="Internal Co",
                     website="http://127.0.0.1/admin")
    db = SessionLocal()
    try:
        lead = db.query(BusinessLead).get(lid)
        res = enrich_lead(lead, db, uid, fetcher=_fetcher({}))
        db.refresh(lead)
        assert lead.enrichment_status == "FAILED"
        assert any("blocked" in e for e in res["errors"])
    finally:
        db.close()


def test_extract_page_handles_missing_and_junk_fields():
    empty = extract_page("<html></html>", "https://x.example")
    assert empty["emails"] == [] and empty["phones"] == []
    assert empty["title"] == "" and empty["links"] == []

    # year ranges and bare digit runs must never become phones
    junk = extract_page(
        "<html><body><p>Since 1990-2024 we served 1234567 customers</p>"
        "</body></html>", "https://x.example")
    assert junk["phones"] == []

    # a formatted published number is kept
    good = extract_page(
        '<html><body><p>Call +1 (415) 555-1234 today</p></body></html>',
        "https://x.example")
    assert good["phones"] and good["phones"][0]["value"] == "+14155551234"


def test_extract_page_never_makes_phones_from_svg_css_or_codes():
    """Live regression (DelightKB/Howdens crawls): scanning raw HTML
    picked up SVG coordinates, CSS decimals and product codes as
    'phones'. The scan runs on visible text with precision guards."""
    html = """
    <html><head><style>.a { width: 497.39px; color: #fff }</style>
    <script>var cfg = {"path": "M 24 24 0 0 0-28 6.9", "id": 438487692225};</script>
    </head><body>
    <svg viewBox="0 0 1000 1000"><path d="M0 0 512 512"/></svg>
    <main>
      <p>SKU 677631-225 and 78-6306-4 in stock</p>
      <p>Ref 4180-896 and code 5-4329-9629 listed</p>
      <p>Prices 497.39 and 6.99 updated 12.05.2024</p>
      <p>Since 1990-2024 we served 1234567 customers</p>
      <p>Open 2024 - 2025 season</p>
      <p class="tel">Tel: 020 8988 9916</p>
    </main>
    <footer>WhatsApp +44 7732 323270</footer>
    </body></html>"""
    out = extract_page(html, "https://x.example")
    values = [p["value"] for p in out["phones"]]
    # exactly the two real published numbers, no junk
    assert values == ["02089889916", "+447732323270"], values
    # footer numbers count as visible content too
    assert any("7732323270" in v for v in values)


# ---------------------------------------------------------------------------
# 12. background job processing + retry
# ---------------------------------------------------------------------------

def test_enrich_job_runs_and_retry_is_idempotent(monkeypatch):
    _mock_ssrf(monkeypatch)
    t = _user("gb-job@test.example")
    uid = _uid("gb-job@test.example")
    lid = _seed_lead(uid, company_name="Job Co",
                     website="https://job.example")

    # step 1: fetcher fails => lead FAILED, job completes with errors
    bad_fetch = _fetcher({})
    monkeypatch.setattr("app.gb_enricher.default_fetcher", bad_fetch)
    db = SessionLocal()
    try:
        job = GBJob(user_id=uid, kind="enrich",
                    lead_ids_json=json.dumps([lid]), status="PENDING")
        db.add(job); db.commit(); db.refresh(job)
        jid = job.id
    finally:
        db.close()
    gbleads_jobs.run_enrich(jid)

    db = SessionLocal()
    try:
        job = db.query(GBJob).get(jid)
        assert job.status == "COMPLETED" and job.processed == 1
        assert job.errors >= 1 and job.attempts == 1
        lead = db.query(BusinessLead).get(lid)
        assert lead.enrichment_status == "FAILED"
        # simulate a crash mid-job: FAILED unlocks the retry endpoint
        job.status = "FAILED"
        job.error = "crashed"
        db.commit()
    finally:
        db.close()

    # step 2: site is fixed; retry via API (thread made synchronous for test)
    good_fetch = _fetcher({
        "https://job.example": (200, HOME, "https://job.example"),
        "https://job.example/contact": (200, CONTACT,
                                        "https://job.example/contact"),
    })
    monkeypatch.setattr("app.gb_enricher.default_fetcher", good_fetch)
    monkeypatch.setattr(gbleads_jobs, "start_enrich",
                        lambda jid_: gbleads_jobs.run_enrich(jid_))
    r = client.post(f"/api/gbleads/jobs/{jid}/retry", headers=_auth(t))
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        job = db.query(GBJob).get(jid)
        assert job.status == "COMPLETED"
        assert job.attempts == 2  # retry actually re-ran
        lead = db.query(BusinessLead).get(lid)
        assert lead.enrichment_status == "ENRICHED"
        assert lead.email == "sales@acme.example"
    finally:
        db.close()


def test_enrich_endpoint_rejects_foreign_leads():
    t = _user("gb-enrich-owner@test.example")
    _user("gb-enrich-other@test.example")
    foreign = _uid("gb-enrich-other@test.example")
    other_lead = _seed_lead(foreign, company_name="Other Co")
    r = client.post("/api/gbleads/enrich", json={"lead_ids": [other_lead]},
                    headers=_auth(t))
    assert r.status_code == 404  # never enrich someone else's lead


# ---------------------------------------------------------------------------
# 10. CSV + Excel export
# ---------------------------------------------------------------------------

def test_csv_export_with_filters():
    t = _user("gb-export@test.example")
    uid = _uid("gb-export@test.example")
    a = _seed_lead(uid, company_name="Alpha Exports",
                   place_id="exp-a", email="a@x.example",
                   website="https://a.example", country="Australia")
    _seed_lead(uid, company_name="Beta Trading", place_id="exp-b",
               website="https://b.example", country="Australia")
    _seed_lead(uid, company_name="Gamma No Site", place_id="exp-c",
               country="Australia")

    r = client.get("/api/gbleads/export",
                   params={"format": "csv"}, headers=_auth(t))
    assert r.status_code == 200
    body = r.text
    assert body.splitlines()[0].startswith("Company Name,Business Category")
    assert "Alpha Exports" in body and "Beta Trading" in body
    assert body.count("exp-a") == 1  # no duplicate rows per business

    r = client.get("/api/gbleads/export",
                   params={"format": "csv", "only_with_email": True},
                   headers=_auth(t))
    assert "Alpha Exports" in r.text
    assert "Beta Trading" not in r.text
    assert "Gamma No Site" not in r.text
    del a


def test_xlsx_export_produces_readable_workbook():
    from openpyxl import load_workbook
    t = _user("gb-export-x@test.example")
    uid = _uid("gb-export-x@test.example")
    _seed_lead(uid, company_name="Excel Co", place_id="xls-1",
               email="e@x.example", country="India")

    r = client.get("/api/gbleads/export",
                   params={"format": "xlsx"}, headers=_auth(t))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    assert ws.cell(row=1, column=1).value == "Company Name"
    assert ws.max_row == 2  # header + one lead
    assert ws.cell(row=2, column=1).value == "Excel Co"


# ---------------------------------------------------------------------------
# 13. user-level data isolation
# ---------------------------------------------------------------------------

def test_user_isolation_across_campaigns_leads_jobs_export():
    a = _user("gb-iso-owner@test.example")
    b = _user("gb-iso-stranger@test.example")
    uid_a = _uid("gb-iso-owner@test.example")

    # owner has data
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Owner campaign", "keywords": ["copper"],
        "max_results": 5, "start": False,
    }, headers=_auth(a))
    assert r.status_code == 201
    cid = r.json()["id"]
    lid = _seed_lead(uid_a, campaign_id=cid, company_name="Owner Lead",
                     place_id="iso-1", email="owner@x.example")

    # stranger sees nothing
    leads_b = client.get("/api/gbleads/leads", headers=_auth(b)).json()
    assert leads_b["total"] == 0
    camps_b = client.get("/api/gbleads/campaigns", headers=_auth(b)).json()
    assert camps_b["count"] == 0

    # stranger cannot read/modify owner rows
    assert client.get(f"/api/gbleads/leads/{lid}",
                      headers=_auth(b)).status_code == 404
    assert client.patch(f"/api/gbleads/leads/{lid}",
                        json={"lead_status": "SAVED"},
                        headers=_auth(b)).status_code == 404
    assert client.get(f"/api/gbleads/campaigns/{cid}",
                      headers=_auth(b)).status_code == 404
    assert client.post(f"/api/gbleads/campaigns/{cid}/cancel",
                       headers=_auth(b)).status_code == 404
    assert client.post("/api/gbleads/enrich", json={"lead_ids": [lid]},
                       headers=_auth(b)).status_code == 404

    # stranger's export contains no owner data
    r = client.get("/api/gbleads/export",
                   params={"format": "csv"}, headers=_auth(b))
    assert "Owner Lead" not in r.text

    # owner still sees everything
    leads_a = client.get("/api/gbleads/leads", headers=_auth(a)).json()
    assert leads_a["total"] == 1


# ---------------------------------------------------------------------------
# 11. settings (config status, never leaks the key)
# ---------------------------------------------------------------------------

def test_settings_reports_status_without_leaking_key(monkeypatch):
    t = _user("gb-settings@test.example")
    monkeypatch.setattr(cfg.settings, "GOOGLE_MAPS_API_KEY",
                        "SUPER-SECRET-GOOGLE-KEY")
    r = client.get("/api/gbleads/settings", headers=_auth(t))
    assert r.status_code == 200
    data = r.json()
    assert data["google_places"]["configured"] is True
    assert data["google_places"]["daily_call_budget"] > 0
    assert "SUPER-SECRET" not in r.text  # key never leaves the server

    monkeypatch.setattr(cfg.settings, "GOOGLE_MAPS_API_KEY", "")
    r = client.get("/api/gbleads/settings", headers=_auth(t))
    assert r.json()["google_places"]["configured"] is False
