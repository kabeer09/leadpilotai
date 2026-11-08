"""Phase 3 tests: buyer/supplier discovery, exhibitor dashboard, research.

Honesty contracts under test:
- discovery payloads carry record_type=directory_discovery + the
  directory-presence disclaimer; classification is a keyword signal with
  per-row evidence (matched keyword + stored field), never a verified fact;
- exhibitor payloads carry record_type=fair_directory_listing + disclaimer;
  extraction launches are refused honestly (unregistered URL / registry
  says not automatable) instead of starting doomed crawls;
- research reports are DB-only by default (NO network on GET) and persist
  evidence rows (source_url + method) + evaluations from real runs;
- /research/qualified defaults to persisted scores (no website hits);
- tenant isolation on every endpoint; export files carry attribution rows;
- ALL website/external calls are monkeypatched — zero network.
"""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import User, Company, Contact, Source, CrawlJob
from app.models_research import ContactEvidence, EvaluationResult
from app import discovery as disc
from app import config as cfg

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


_uid = {"n": 0}


def _uniq(prefix="t3"):
    _uid["n"] += 1
    return f"{prefix}-{_uid['n']}-{datetime.utcnow().timestamp()}@example.com"


def _mk_company(user_id, name, **kw):
    db = SessionLocal()
    try:
        c = Company(user_id=user_id, name=name, **kw)
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id, c
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


def _mk_job(user_id, source_id, status="COMPLETED", pages=10,
            created_at=None):
    db = SessionLocal()
    try:
        j = CrawlJob(user_id=user_id, source_id=source_id,
                     url="https://example.com/seed", max_pages=50,
                     status=status, pages_visited=pages)
        if created_at is not None:
            j.created_at = created_at
        db.add(j)
        db.commit()
        db.refresh(j)
        return j.id
    finally:
        db.close()


def _fake_research(**over):
    base = {
        "company_id": 0,
        "website": "https://example.com",
        "emails": [{"value": "sales@example.com",
                    "source_url": "https://example.com/contact",
                    "extraction_method": "mailto_link", "confidence": 0.9}],
        "phones": [{"value": "+15551234567",
                    "source_url": "https://example.com/contact",
                    "extraction_method": "tel_link", "confidence": 0.9}],
        "whatsapp_links": [],
        "description": "Wholesale importer of kitchenware and gifts",
        "country": "United States",
        "product_categories": None,
        "pages_checked": ["https://example.com/"],
        "retrieved_at": datetime.utcnow().isoformat(),
        "method": "mocked",
        "errors": [],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# classification (pure)
# ---------------------------------------------------------------------------

def test_classify_importer_signals():
    out = disc.classify_fields({"name": "Global Sourcing Ltd",
                                "product_summary": "we import ceramics"})
    assert "Importers" in out["types"]
    assert out["role"] == "buyer"
    assert out["signals"], "evidence rows must be present"


def test_classify_manufacturer_role_supplier():
    out = disc.classify_fields({"name": "Acme Manufacturing Co"})
    assert out["buyer_type"] == "Manufacturers"
    assert out["role"] == "supplier"
    assert out["signals"][0]["field"] == "name"


def test_classify_no_match_is_unclassified_not_guessed():
    out = disc.classify_fields({"name": "Blue Sky Consulting"})
    assert out["buyer_type"] == "Unclassified"
    assert out["role"] == "unclassified"
    assert out["signals"] == []


def test_classify_word_boundaries_no_false_positives():
    # "important" must not match import, "workshop" not shop, "storage"
    # not store
    out = disc.classify_fields({"name": "Important Workshop Storage Inc"})
    assert "Importers" not in out["types"]
    assert "Retailers" not in out["types"]


def test_classify_multi_type_membership_and_priority():
    out = disc.classify_fields(
        {"category": "Wholesale distribution",
         "product_summary": "manufacturer of tableware"})
    assert "Wholesalers" in out["types"]
    assert "Distributors" in out["types"]
    assert "Manufacturers" in out["types"]
    # buy-side signal has priority over manufacturer
    assert out["buyer_type"] == "Distributors"


def test_import_activity_signals():
    act = disc.import_activity({"product_summary": "imports copper bottles"})
    assert act["active"] is True
    assert act["signals"][0]["field"] == "product_summary"
    none = disc.import_activity({"name": "Local Bakery"})
    assert none["active"] is False


def test_type_definitions_cover_master_prompt_categories():
    values = {t["value"] for t in disc.type_definitions()}
    for expected in ("Importers", "Distributors", "Wholesalers", "Retailers",
                     "Manufacturers", "Trading", "Procurement",
                     "Corporate gifting", "Unclassified"):
        assert expected in values


# ---------------------------------------------------------------------------
# GET /api/discovery/buyers
# ---------------------------------------------------------------------------

def test_buyers_envelope_is_honest():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    _mk_company(uid, "Honesty Check GmbH",
                country="Germany", category="Giftware",
                product_summary="corporate gifting supplier")
    r = client.get("/api/discovery/buyers", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "directory_discovery"
    assert "NOT evidence of active buying" in body["disclaimer"]
    assert body["source"]["record_type"] == "directory_discovery"
    assert "coverage" in body["source"]
    assert body["facet_note"]


def test_buyers_filters_and_evidence():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    _mk_company(uid, "Import Hub BV", country="Netherlands",
                product_summary="importer of home decor")
    _mk_company(uid, "Forge Works", country="India",
                product_summary="manufacturer of brassware")
    h = _auth(tok)

    r = client.get("/api/discovery/buyers", params={"role": "supplier"},
                   headers=h)
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["buyer_type"] == "Manufacturers"
    assert item["signals"], "matched keyword evidence must be returned"
    assert item["signals"][0]["keyword"]
    assert item["signals"][0]["field"] in ("name", "category",
                                           "product_summary", "address",
                                           "products")

    r2 = client.get("/api/discovery/buyers",
                    params={"buyer_type": "Importers"}, headers=h)
    b2 = r2.json()
    assert b2["total"] == 1
    assert b2["items"][0]["name"] == "Import Hub BV"
    assert b2["items"][0]["import_activity"] is True


def test_buyers_country_filter_and_facets():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    _mk_company(uid, "Alpha Imports", country="United States",
                product_summary="importer")
    _mk_company(uid, "Beta GmbH", country="Germany")
    body = client.get("/api/discovery/buyers",
                      params={"country": "Germany"},
                      headers=_auth(tok)).json()
    assert body["total"] == 1
    facets = {f["value"]: f["count"] for f in body["facets"]["countries"]}
    assert facets.get("United States", 0) >= 1
    assert facets.get("Germany", 0) >= 1


def test_buyers_contact_filters():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    a, _ = _mk_company(uid, "With Mail", country="USA")
    _mk_company(uid, "No Mail", country="USA")
    _mk_contact(uid, a, "EMAIL", "hi@withmail.example")
    h = _auth(tok)
    yes = client.get("/api/discovery/buyers",
                     params={"has_email": "true"}, headers=h).json()
    assert yes["total"] == 1
    assert yes["items"][0]["emails"] == ["hi@withmail.example"]
    no = client.get("/api/discovery/buyers",
                    params={"has_email": "false"}, headers=h).json()
    assert no["total"] == 1
    assert no["items"][0]["name"] == "No Mail"


def test_buyers_rejects_unknown_type():
    tok = _user(_uniq())
    r = client.get("/api/discovery/buyers",
                   params={"buyer_type": "Wizard"}, headers=_auth(tok))
    assert r.status_code == 422


def test_buyers_pagination_and_tenant_isolation():
    tok_a = _user(_uniq())
    uid_a = _find_user_id(tok_a)
    for i in range(3):
        _mk_company(uid_a, f"Paging Co {i}", country="USA")
    tok_b = _user(_uniq())
    uid_b = _find_user_id(tok_b)
    _mk_company(uid_b, "B Only GmbH", country="Germany")

    page1 = client.get("/api/discovery/buyers",
                       params={"page": 1, "page_size": 2},
                       headers=_auth(tok_a)).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    names = {i["name"] for i in page1["items"]}
    assert "B Only GmbH" not in names
    page2 = client.get("/api/discovery/buyers",
                       params={"page": 2, "page_size": 2},
                       headers=_auth(tok_a)).json()
    assert len(page2["items"]) == 1


def test_discovery_includes_non_fair_companies():
    """Buyer discovery scans ALL active companies, not just exhibitors."""
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    _mk_company(uid, "Plain GB Trader", country="United Kingdom",
                product_summary="retail store")   # no source/hall/booth
    body = client.get("/api/discovery/buyers",
                      headers=_auth(tok)).json()
    assert any(i["name"] == "Plain GB Trader" for i in body["items"])


# ---------------------------------------------------------------------------
# discovery export + saved searches
# ---------------------------------------------------------------------------

def test_discovery_export_has_attribution():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    _mk_company(uid, "Export Me GmbH", country="Germany",
                product_summary="wholesale distributor")
    r = client.get("/api/discovery/export", headers=_auth(tok))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    assert "# Source:" in text
    assert "# Record type: directory_discovery" in text
    assert "KEYWORD SIGNALS" in text
    assert "NOT evidence of active buying activity" in text
    assert "Export Me GmbH" in text


def test_discovery_saved_search_crud_and_isolation():
    tok_a = _user(_uniq())
    h = _auth(tok_a)
    r = client.post("/api/discovery/saved-searches",
                    json={"name": "EU importers",
                          "query": {"role": "buyer", "country": "Germany"}},
                    headers=h)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["kind"] == "buyer"

    dup = client.post("/api/discovery/saved-searches",
                      json={"name": "EU importers", "query": {}}, headers=h)
    assert dup.status_code == 409

    lst = client.get("/api/discovery/saved-searches", headers=h).json()
    assert lst["count"] == 1
    assert lst["items"][0]["query"]["role"] == "buyer"

    tok_b = _user(_uniq())
    lst_b = client.get("/api/discovery/saved-searches",
                       headers=_auth(tok_b)).json()
    assert lst_b["count"] == 0
    steal = client.delete(f"/api/discovery/saved-searches/{sid}",
                          headers=_auth(tok_b))
    assert steal.status_code == 404

    gone = client.delete(f"/api/discovery/saved-searches/{sid}", headers=h)
    assert gone.status_code == 204


# ---------------------------------------------------------------------------
# exhibitor dashboard
# ---------------------------------------------------------------------------

def test_exhibitors_list_status_labels_and_scope():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    src = _mk_source(uid, "https://www.highpointmarket.org/list",
                     "High Point List")
    _mk_job(uid, src, status="CRAWLING")   # -> Running
    _mk_company(uid, "Booth Co", source_id=src, hall="Hall 2",
                booth="C-14", country="USA",
                products_json=json.dumps([{"name": "Lamps"}]))
    _mk_company(uid, "Plain Co", country="USA")  # not an exhibitor row

    r = client.get("/api/exhibitors", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "fair_directory_listing"
    assert "NOT current buying/selling activity" in body["disclaimer"]
    names = [i["name"] for i in body["items"]]
    assert "Booth Co" in names
    assert "Plain Co" not in names, "non-fair companies are not exhibitors"
    item = next(i for i in body["items"] if i["name"] == "Booth Co")
    assert item["extraction_status"] == "Running"
    assert item["fair"] == "High Point List"
    assert item["products"][0]["name"] == "Lamps"
    assert "Not run" in body["status_labels"]


def test_exhibitors_status_filter():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    src_done = _mk_source(uid, "https://www.atlantamarket.com/x", "ATL")
    _mk_job(uid, src_done, status="COMPLETED")
    _mk_company(uid, "Done Co", source_id=src_done, hall="A1")
    src_fail = _mk_source(uid, "https://www.lasvegasmarket.com/x", "LV")
    _mk_job(uid, src_fail, status="FAILED")
    _mk_company(uid, "Fail Co", source_id=src_fail, hall="B2")
    h = _auth(tok)
    running = client.get("/api/exhibitors", params={"status": "Failed"},
                         headers=h).json()
    assert running["total"] == 1
    assert running["items"][0]["name"] == "Fail Co"
    done = client.get("/api/exhibitors", params={"status": "Completed"},
                      headers=h).json()
    assert done["total"] == 1
    assert done["items"][0]["name"] == "Done Co"


def test_exhibitors_search_q():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    src = _mk_source(uid, "https://www.springfair.com/z", "SF")
    _mk_company(uid, "Copper Kettle Ltd", source_id=src, hall="Hall 4",
                category="Kitchen")
    _mk_company(uid, "Glass House GmbH", source_id=src, hall="Hall 5")
    body = client.get("/api/exhibitors", params={"q": "Copper"},
                      headers=_auth(tok)).json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Copper Kettle Ltd"


def test_exhibitors_overview_registry_and_jobs():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    src = _mk_source(uid, "https://www.highpointmarket.org/exhibitors",
                     "HP extraction")
    _mk_job(uid, src, status="PENDING")
    _mk_company(uid, "HP Co", source_id=src, hall="C-3")
    body = client.get("/api/exhibitors/overview",
                      headers=_auth(tok)).json()
    assert body["record_type"] == "exhibitor_extraction_overview"
    hp = next(f for f in body["fairs"] if f["id"] == "highpoint")
    assert hp["status"] == "READY_WITH_LIMITATIONS"
    assert hp["automation_permitted"] is True
    assert hp["jobs_by_status"]["Pending"] == 1
    assert hp["exhibitors"] == 1
    assert hp["last_extraction"]["status"] == "Pending"
    assert body["summary"]["jobs_by_status"]["Pending"] >= 1
    assert body["summary"]["fairs_registered"] >= 10
    # honesty: a registry fair with no local data reports zeros, not fiction
    nf = next(f for f in body["fairs"] if f["id"] == "nynow")
    assert nf["exhibitors"] == 0
    assert nf["can_extract"] is False
    assert "disclaimer" in body


def test_exhibitors_export_attribution():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    src = _mk_source(uid, "https://ambiente.messefrankfurt.com/e", "Amb")
    _mk_company(uid, "Amb Exhibitor", source_id=src, hall="8.0",
                booth="A11")
    r = client.get("/api/exhibitors/export", headers=_auth(tok))
    assert r.status_code == 200
    text = r.text
    assert "# Source:" in text
    assert "# Record type: fair_directory_listing" in text
    assert "NOT evidence of current buying/selling activity" in text
    assert "Amb Exhibitor" in text
    assert "8.0" in text


def test_extract_refuses_unregistered_url(monkeypatch):
    monkeypatch.setattr("app.api_exhibitors.validate_url",
                        lambda u: (u, None))
    tok = _user(_uniq())
    r = client.post("/api/exhibitors/extract",
                    json={"url": "https://example.com/exhibitors"},
                    headers=_auth(tok))
    assert r.status_code == 422
    assert "not a registered trade-fair source" in r.json()["detail"]
    assert "highpointmarket.org" in r.json()["detail"]


def test_extract_refuses_non_automatable_fair(monkeypatch):
    monkeypatch.setattr("app.api_exhibitors.validate_url",
                        lambda u: (u, None))
    tok = _user(_uniq())
    # registry: ihgf = MANUAL_IMPORT_REQUIRED / automation_permitted False
    r = client.post("/api/exhibitors/extract",
                    json={"url": "https://www.ihgfdelhifair.com/"},
                    headers=_auth(tok))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "cannot be automated" in detail
    assert "Manual" in detail  # registry fallback is surfaced honestly


def test_extract_starts_job_for_registered_fair(monkeypatch):
    monkeypatch.setattr("app.api_exhibitors.validate_url",
                        lambda u: (u, None))
    tok = _user(_uniq())
    started = []
    monkeypatch.setattr("app.crawler.start_job",
                        lambda jid, force_recrawl=False: started.append(jid)
                        or True)
    r = client.post("/api/exhibitors/extract",
                    json={"url": "https://www.highpointmarket.org/",
                          "max_pages": 25},
                    headers=_auth(tok))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "Pending"
    assert body["fair"] == "High Point Market"
    assert started == [body["job_id"]]
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).get(body["job_id"])
        assert job.status == "PENDING"
        assert job.max_pages == 25
        assert job.source_id is not None
    finally:
        db.close()


def test_extract_daily_quota_429(monkeypatch):
    monkeypatch.setattr("app.api_exhibitors.validate_url",
                        lambda u: (u, None))
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    monkeypatch.setattr("app.crawler.start_job",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("must not start")))
    _mk_job(uid, None, status="COMPLETED",
            pages=cfg.settings.DAILY_CRAWL_PAGES + 10)
    r = client.post("/api/exhibitors/extract",
                    json={"url": "https://www.highpointmarket.org/"},
                    headers=_auth(tok))
    assert r.status_code == 429
    assert "quota" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# company research module
# ---------------------------------------------------------------------------

def test_research_picker_search_and_isolation():
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    _mk_company(uid, "Alpha Trading", country="USA",
                website="https://alpha.example")
    _mk_company(uid, "Beta Manufacturing", country="India")
    h = _auth(tok)
    body = client.get("/api/research/companies",
                      params={"q": "alpha"}, headers=h).json()
    assert body["count"] == 1
    assert body["items"][0]["name"] == "Alpha Trading"
    assert body["items"][0]["has_research"] is False
    # tenant isolation
    tok_b = _user(_uniq())
    body_b = client.get("/api/research/companies",
                        params={"q": "alpha"},
                        headers=_auth(tok_b)).json()
    assert body_b["count"] == 0


def test_research_report_is_db_only_no_network(monkeypatch):
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    cid, _ = _mk_company(uid, "Report Co", country="Germany",
                         website="https://report.example")
    _mk_contact(uid, cid, "EMAIL", "info@report.example",
                source_url="https://report.example/contact")

    def boom(*a, **k):
        raise AssertionError("GET report must not hit the network")
    monkeypatch.setattr("app.agents.research_company_website", boom)

    r = client.get(f"/api/research/report/{cid}", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "company_research_report"
    assert body["company"]["name"] == "Report Co"
    assert body["contacts"][0]["value"] == "info@report.example"
    assert body["contacts"][0]["source_url"].startswith("https://")
    assert body["evaluation"] is None
    assert body["research"] is None
    assert body["has_research"] is False
    assert "never guessed" in body["note"]


def test_research_refresh_persists_evidence_and_evaluation(monkeypatch):
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    cid, _ = _mk_company(uid, "Refresh Co", country="United States",
                         website="https://refresh.example")
    monkeypatch.setattr("app.agents.research_company_website",
                        lambda company, db: _fake_research(
                            company_id=company.id,
                            website=company.website))
    h = _auth(tok)
    r = client.post(f"/api/research/report/{cid}/refresh", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refreshed_at"]
    assert body["research"]["emails"][0]["value"] == "sales@example.com"
    assert body["evaluation"] is not None
    assert body["evaluation"]["buyer_type"]
    assert body["evaluation"]["emails"] == ["sales@example.com"]
    assert body["has_research"] is True

    # persisted as evidence rows with provenance
    db = SessionLocal()
    try:
        evs = db.query(ContactEvidence).filter(
            ContactEvidence.company_id == cid).all()
        assert evs, "evidence rows must be persisted"
        assert all(e.source_url.startswith("https://") for e in evs)
        assert all(e.extraction_method for e in evs)
        evs_eval = db.query(EvaluationResult).filter(
            EvaluationResult.company_id == cid).all()
        assert len(evs_eval) == 1
        payload = json.loads(evs_eval[0].result_json)
        assert payload["buyer_type"]
        assert payload["emails"] == ["sales@example.com"]
    finally:
        db.close()

    # follow-up GET reads persisted data (no refresh, no network)
    g = client.get(f"/api/research/report/{cid}", headers=h).json()
    assert g["research"] is None
    assert g["evaluation"] is not None
    assert g["has_research"] is True
    assert len(g["evidence"]) >= 2


def test_research_refresh_rate_limited(monkeypatch):
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    cid, _ = _mk_company(uid, "Rate Co", website="https://rate.example")
    monkeypatch.setattr("app.agents.research_company_website",
                        lambda company, db: _fake_research())
    from app.deps import rate_limit
    per_min = cfg.settings.RATE_SENSITIVE_PER_MIN
    h = _auth(tok)
    codes = []
    for _ in range(per_min + 3):
        r = client.post(f"/api/research/report/{cid}/refresh", headers=h)
        codes.append(r.status_code)
    assert 429 in codes, f"expected a 429 within {per_min + 3} calls"
    assert codes[0] == 200


def test_research_run_pipeline_persists(monkeypatch):
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    cid, _ = _mk_company(uid, "Pipe Co", country="United States",
                         website="https://pipe.example")
    monkeypatch.setattr("app.agents.research_company_website",
                        lambda company, db: _fake_research(
                            company_id=company.id))
    r = client.post("/api/research/run",
                    json={"company_ids": [cid]}, headers=_auth(tok))
    assert r.status_code == 200, r.text
    assert r.json()["companies_processed"] == 1
    db = SessionLocal()
    try:
        assert db.query(EvaluationResult).filter(
            EvaluationResult.company_id == cid).count() == 1
        assert db.query(ContactEvidence).filter(
            ContactEvidence.company_id == cid).count() >= 2
    finally:
        db.close()


def test_research_qualified_fast_path_persisted(monkeypatch):
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    cid, _ = _mk_company(uid, "Qualify Co", country="United States",
                         website="https://qualify.example")

    def boom(*a, **k):
        raise AssertionError("default /qualified must not hit the network")
    monkeypatch.setattr("app.agents.research_company_website", boom)

    # nothing researched yet -> empty, honest note, no crash
    empty = client.get("/api/research/qualified",
                       headers=_auth(tok)).json()
    assert empty["mode"] == "persisted"
    assert empty["count"] == 0

    # run one live (mocked) research to persist a score
    monkeypatch.setattr("app.agents.research_company_website",
                        lambda company, db: _fake_research(
                            company_id=company.id))
    client.post("/api/research/run", json={"company_ids": [cid]},
                headers=_auth(tok))

    body = client.get("/api/research/qualified",
                      params={"min_score": 10},
                      headers=_auth(tok)).json()
    assert body["mode"] == "persisted"
    assert body["count"] == 1
    row = body["companies"][0]
    assert row["company_id"] == cid
    assert row["score"] >= 10
    # buyer_type here is qualify_buyer's vocabulary (Research module)
    from app.agents import BUYER_TYPES as QUALIFY_TYPES
    assert row["buyer_type"] in QUALIFY_TYPES
    assert row["evaluated_at"]
    assert "refresh=true" in body["note"]


def test_research_qualified_tenant_isolation():
    tok_a = _user(_uniq())
    uid_a = _find_user_id(tok_a)
    cid, _ = _mk_company(uid_a, "Scored Co", country="United States")
    db = SessionLocal()
    try:
        db.add(EvaluationResult(user_id=uid_a, company_id=cid,
                                buyer_fit_score=80, overall_score=80,
                                result_json=json.dumps(
                                    {"buyer_type": "Importers"})))
        db.commit()
    finally:
        db.close()
    seen_a = client.get("/api/research/qualified",
                        headers=_auth(tok_a)).json()
    assert seen_a["count"] == 1
    tok_b = _user(_uniq())
    body = client.get("/api/research/qualified",
                      headers=_auth(tok_b)).json()
    assert body["count"] == 0


def test_research_company_endpoint_persists(monkeypatch):
    tok = _user(_uniq())
    uid = _find_user_id(tok)
    cid, _ = _mk_company(uid, "Single Co", country="United States",
                         website="https://single.example")
    monkeypatch.setattr("app.agents.research_company_website",
                        lambda company, db: _fake_research(
                            company_id=company.id))
    r = client.post(f"/api/research/company/{cid}", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qualification"]["score"] >= 0
    assert body["evidence_saved"]["added"] >= 2
    db = SessionLocal()
    try:
        assert db.query(EvaluationResult).filter(
            EvaluationResult.company_id == cid).count() == 1
    finally:
        db.close()


def test_research_report_404_tenant_isolation():
    tok_a = _user(_uniq())
    uid_a = _find_user_id(tok_a)
    cid, _ = _mk_company(uid_a, "Owner Only")
    tok_b = _user(_uniq())
    r = client.get(f"/api/research/report/{cid}", headers=_auth(tok_b))
    assert r.status_code == 404


# ---------------------------------------------------------------------------

def _find_user_id(token_json):
    """Resolve the signed-up user's id (signup returns tokens, not id)."""
    from app.security import decode_token
    return decode_token(token_json["access_token"])
