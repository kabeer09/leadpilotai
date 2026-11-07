"""Global Trade Intelligence tests (Phase 2).

Honesty contracts under test:
- every search/statistics payload carries record_type=aggregated_statistics
  + a source block (provider, endpoint, coverage, disclaimer);
- quota (429) and config failures surface as typed errors — never a fake
  "0 results" — and a cached payload is served flagged stale when present;
- saved searches are tenant-scoped (user B cannot see/delete user A's);
- export files include source-attribution rows;
- /api/data-sources never leaks a configured key;
- ALL external calls are mocked (httpx.MockTransport) — zero network,
  zero credits, zero real sleeping (sleep is injected).
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import User
from app.models_trade import SavedSearch, TradeStatistic, DataProvider
from app import trade_providers as tp
from app import api_trade
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


# Sample UN Comtrade preview payload (mirrors the live API: *Desc null).
_PREVIEW = {
    "count": 2,
    "data": [
        {"period": "2023", "flowCode": "M", "reporterCode": 826,
         "partnerCode": 392, "cmdCode": "7113",
         "primaryValue": 5574287.248, "cifvalue": 5574287.248,
         "fobvalue": None, "qty": 0, "netWgt": None,
         "reporterDesc": None, "partnerDesc": None, "cmdDesc": None,
         "flowDesc": None, "classificationCode": "H6"},
        {"period": "2023", "flowCode": "X", "reporterCode": 826,
         "partnerCode": 392, "cmdCode": "7113",
         "primaryValue": 3164941142.71, "cifvalue": None,
         "fobvalue": 3164941142.71, "qty": 120, "netWgt": 9000,
         "reporterDesc": None, "partnerDesc": None, "cmdDesc": None,
         "flowDesc": None, "classificationCode": "H6"},
    ],
}

_REPORTERS = {"results": [
    {"id": 826, "text": "United Kingdom", "reporterCode": 826,
     "reporterDesc": "United Kingdom", "reporterCodeIsoAlpha2": "GB",
     "reporterCodeIsoAlpha3": "GBR", "isGroup": False},
]}
_PARTNERS = {"results": [
    {"id": 392, "text": "Japan", "PartnerCode": 392,
     "PartnerDesc": "Japan", "PartnerCodeIsoAlpha2": "JP",
     "PartnerCodeIsoAlpha3": "JPN", "isGroup": False},
]}
_HS = {"results": [
    {"id": "71", "text": "71 - Natural or cultured pearls, precious stones",
     "parent": "#", "isLeaf": "0", "aggrLevel": 2},
    {"id": "7113", "text": "7113 - Articles of jewellery of precious metal",
     "parent": "71", "isLeaf": "0", "aggrLevel": 4},
    {"id": "711319", "text": "711319 - Of other precious metal",
     "parent": "7113", "isLeaf": "1", "aggrLevel": 6},
]}


def _handler(preview=None, quota=False):
    """Mocked UN Comtrade transport: preview + reference files."""
    preview = _PREVIEW if preview is None else preview

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if quota:
            return httpx.Response(429, json={"error": "rate limit"},
                                  request=request)
        if "Reporters.json" in url:
            return httpx.Response(200, json=_REPORTERS, request=request)
        if "partnerAreas.json" in url:
            return httpx.Response(200, json=_PARTNERS, request=request)
        if "HS.json" in url:
            return httpx.Response(200, json=_HS, request=request)
        if "/preview/" in url:
            return httpx.Response(200, json=preview, request=request)
        return httpx.Response(404, json={"error": "not found"},
                              request=request)

    return handle


def _provider(handler=None, sleep=None):
    return tp.UNComtradeProvider(
        transport=httpx.MockTransport(handler or _handler()),
        sleep=sleep or (lambda s: None),  # never really sleep
        api_key="")


@pytest.fixture(autouse=True)
def trade_env(monkeypatch):
    """Offline, fast, deterministic environment for every trade test."""
    monkeypatch.setattr(cfg.settings, "TRADE_PROVIDER", "un_comtrade")
    monkeypatch.setattr(cfg.settings, "UN_COMTRADE_API_KEY", "")
    monkeypatch.setattr(cfg.settings, "TRADE_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(cfg.settings, "TRADE_MAX_RETRIES", 2)
    monkeypatch.setattr(cfg.settings, "TRADE_CACHE_TTL_HOURS", 24)
    tp.reset_trade_provider()
    # trade_statistics is a GLOBAL cache (no user_id): wipe it per test so
    # one test's warmed payload never answers another test's query.
    db = SessionLocal()
    try:
        db.query(TradeStatistic).delete()
        db.query(DataProvider).delete()
        db.commit()
    finally:
        db.close()
    yield
    tp.reset_trade_provider()


# ---------------------------------------------------------------------------
# pure query helpers
# ---------------------------------------------------------------------------

def test_normalize_flow_accepts_m_x_both():
    assert tp.normalize_flow("m") == "M"
    assert tp.normalize_flow("X") == "X"
    assert tp.normalize_flow("both") == "both"
    assert tp.normalize_flow(None) == "both"
    with pytest.raises(tp.TradeConfigError):
        tp.normalize_flow("sideways")


def test_expand_periods_validates_and_expands():
    assert tp.expand_periods("2021", "2023") == ["2021", "2022", "2023"]
    # reversed range is normalized, not an error
    assert tp.expand_periods("2023", "2021") == ["2021", "2022", "2023"]
    with pytest.raises(tp.TradeConfigError):
        tp.expand_periods("2023", "not-a-year")
    with pytest.raises(tp.TradeConfigError):
        tp.expand_periods("2000", "2023")  # >10 years


def test_normalize_cmd():
    assert tp.normalize_cmd("7113") == "7113"
    assert tp.normalize_cmd("") is None
    assert tp.normalize_cmd(None) is None
    with pytest.raises(tp.TradeConfigError):
        tp.normalize_cmd("brass handicrafts")


# ---------------------------------------------------------------------------
# provider (mocked transport)
# ---------------------------------------------------------------------------

def test_provider_search_decorates_null_descriptions():
    p = _provider()
    out = p.search(reporter_code=826, periods=["2023"], flow="both")
    assert out["rows"], "expected mocked rows"
    row = out["rows"][0]
    # descriptions resolved locally from UN reference files, not invented
    assert row["reporterDesc"] == "United Kingdom"
    assert row["partnerDesc"] == "Japan"
    assert row["cmdDesc"] == "Articles of jewellery of precious metal"
    assert row["flowDesc"] in ("Import", "Export")
    # source attribution present
    src = out["source"]
    assert src["record_type"] == "aggregated_statistics"
    assert "NOT individual shipment records" in src["disclaimer"]
    assert src["name"] == "UN Comtrade"


def test_provider_loops_one_period_per_call():
    calls = []

    def handle(request):
        # count only preview searches (reference files also go through here)
        if "/preview/" in str(request.url):
            calls.append(str(request.url))
        return _handler()(request)

    p = _provider(handler=handle)
    p.search(reporter_code=826, periods=["2021", "2022", "2023"],
             flow="M")
    # preview API accepts ONE period per request -> 3 calls
    assert len(calls) == 3


def test_provider_flow_both_sends_both_codes_in_one_call():
    seen = {}

    def handle(request):
        if "/preview/" in str(request.url):
            seen["flow"] = request.url.params.get("flowCode")
        return _handler()(request)

    p = _provider(handler=handle)
    p.search(reporter_code=826, periods=["2023"], flow="both")
    assert seen["flow"] == "M,X"


def test_provider_429_raises_typed_quota_error_with_backoff():
    sleeps = []
    p = _provider(handler=_handler(quota=True), sleep=sleeps.append)
    with pytest.raises(tp.TradeQuotaError) as exc:
        p.search(reporter_code=826, periods=["2023"], flow="M")
    # retries happened (TRADE_MAX_RETRIES=2 -> 2 backoff sleeps)
    assert len(sleeps) == 2
    assert "rate limit" in str(exc.value).lower()
    assert "429" in str(exc.value)


def test_provider_config_error_when_disabled(monkeypatch):
    monkeypatch.setattr(cfg.settings, "TRADE_PROVIDER", "none")
    with pytest.raises(tp.TradeConfigError):
        tp.get_trade_provider()
    monkeypatch.setattr(cfg.settings, "TRADE_PROVIDER", "banana")
    with pytest.raises(tp.TradeConfigError):
        tp.get_trade_provider()


def test_provider_hs_search_by_code_and_text():
    p = _provider()
    by_code = p.hs_search(code="7113")
    assert any(r["id"] == "7113" for r in by_code)
    assert any(r["id"] == "711319" for r in by_code)  # child via parent
    by_text = p.hs_search(query="jewellery")
    assert any(r["id"] == "7113" for r in by_text)
    assert p.hs_search(query="zzz-no-match") == []


def test_provider_countries_lists():
    p = _provider()
    out = p.countries()
    names = [r["name"] for r in out["reporters"]]
    assert "United Kingdom" in names
    partner_names = [r["name"] for r in out["partners"]]
    assert "World" in partner_names and "Japan" in partner_names
    assert out["source"]["record_type"] == "reference_data"


def test_provider_statistics_groups_by_period_and_flow():
    p = _provider()
    out = p.statistics(reporter_code=826, periods=["2023"], flow="both")
    assert out["record_type"] == "aggregated_statistics"
    flows = {s["flow"] for s in out["series"]}
    assert flows == {"M", "X"}
    total = sum(s["value"] for s in out["series"])
    assert total == pytest.approx(
        5574287.248 + 3164941142.71)
    assert out["total_value"] == pytest.approx(total)


def test_provider_statistics_excludes_world_row_when_parts_present():
    """An unfiltered query can return the World aggregate alongside the
    individual partner rows that compose it — summing both would
    double-count. The World row must be dropped when parts exist."""
    mixed = {"count": 3, "data": [
        {"period": "2023", "flowCode": "X", "reporterCode": 826,
         "partnerCode": 0, "cmdCode": "7113", "primaryValue": 300.0},
        {"period": "2023", "flowCode": "X", "reporterCode": 826,
         "partnerCode": 392, "cmdCode": "7113", "primaryValue": 200.0},
        {"period": "2023", "flowCode": "X", "reporterCode": 826,
         "partnerCode": 842, "cmdCode": "7113", "primaryValue": 100.0},
    ]}
    p = _provider(handler=_handler(preview=mixed))
    out = p.statistics(reporter_code=826, periods=["2023"], flow="X")
    assert len(out["series"]) == 1
    cell = out["series"][0]
    assert cell["value"] == 300.0          # parts only, World not double-counted
    assert cell["records"] == 2

    # explicit World query (partner_code=0): World rows ARE the result
    only_world = {"count": 1, "data": [
        {"period": "2023", "flowCode": "X", "reporterCode": 826,
         "partnerCode": 0, "cmdCode": "7113", "primaryValue": 300.0},
    ]}
    p2 = _provider(handler=_handler(preview=only_world))
    out2 = p2.statistics(reporter_code=826, periods=["2023"], flow="X",
                         partner_code=0)
    assert out2["series"][0]["value"] == 300.0
    assert out2["series"][0]["records"] == 1


# ---------------------------------------------------------------------------
# API endpoints (mocked provider injected)
# ---------------------------------------------------------------------------

class FakeTradeProvider:
    """In-memory provider for endpoint tests — zero network."""

    name = "un_comtrade"

    def __init__(self, rows=None, fail=None, series=None):
        self.rows = rows if rows is not None else _PREVIEW["data"]
        self.fail = fail  # TradeError to raise
        self.series = series or []

    def search(self, **kw):
        if self.fail:
            raise self.fail
        return {"rows": [dict(r) for r in self.rows], "truncated": False,
                "source": {"name": "UN Comtrade",
                           "url": tp.PREVIEW_URL,
                           "record_type": "aggregated_statistics",
                           "coverage": "Annual merchandise trade (HS)",
                           "periods": kw.get("periods", []),
                           "disclaimer": "Aggregated trade statistics — NOT "
                                         "individual shipment records and "
                                         "NOT buyer/supplier identities."}}

    def statistics(self, **kw):
        if self.fail:
            raise self.fail
        return {"rows": [dict(r) for r in self.rows], "truncated": False,
                "source": self.search(**kw)["source"],
                "series": self.series,
                "total_value": sum(s.get("value", 0) for s in self.series)}

    def hs_search(self, query=None, code=None, limit=50):
        return [_HS["results"][1]] if code == "7113" else []

    def countries(self):
        return {"reporters": [{"code": 826, "name": "United Kingdom"}],
                "partners": [{"code": 0, "name": "World"}],
                "source": {"name": "UN Comtrade reference",
                           "record_type": "reference_data"}}

    def status(self):
        return {"key": "un_comtrade", "name": "UN Comtrade",
                "endpoint": tp.PREVIEW_URL, "auth_type": "none",
                "auth_configured": False, "mode": "public preview (no key)",
                "limits": "<=500 records/call", "status": "active"}


@pytest.fixture
def fake_provider(monkeypatch):
    fp = FakeTradeProvider()
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: fp)
    return fp


def test_search_requires_auth():
    r = client.get("/api/trade/search",
                   params={"reporter_code": 826, "period_from": "2023",
                           "period_to": "2023"})
    assert r.status_code == 401


def test_search_returns_attributed_aggregate(fake_provider):
    tok = _user("trade-search@example.com")
    r = client.get("/api/trade/search",
                   params={"reporter_code": 826, "period_from": "2023",
                           "period_to": "2023", "flow": "both"},
                   headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "aggregated_statistics"
    assert body["source"]["name"] == "UN Comtrade"
    assert body["source"]["url"].startswith("https://comtradeapi.un.org")
    assert "NOT individual shipment records" in body["source"]["disclaimer"]
    assert body["count"] == len(body["rows"]) == 2
    assert body["stale"] is False
    assert body["params"]["reporter_code"] == 826


def test_search_validation_is_typed_422(fake_provider):
    tok = _user("trade-valid@example.com")
    base = {"reporter_code": 826, "period_from": "2023",
            "period_to": "2023"}
    # bad HS code
    r = client.get("/api/trade/search",
                   params={**base, "cmd_code": "gold"},
                   headers=_auth(tok))
    assert r.status_code == 422
    assert "HS code" in r.json()["detail"]
    # bad year
    r = client.get("/api/trade/search",
                   params={**base, "period_from": "twenty"},
                   headers=_auth(tok))
    assert r.status_code == 422
    # bad flow
    r = client.get("/api/trade/search",
                   params={**base, "flow": "diagonal"},
                   headers=_auth(tok))
    assert r.status_code == 422


def test_search_serves_stale_cache_on_quota_error(fake_provider):
    tok = _user("trade-stale@example.com")
    # 1) warm the cache with a successful call
    r1 = client.get("/api/trade/search",
                    params={"reporter_code": 826, "period_from": "2023",
                            "period_to": "2023", "flow": "M"},
                    headers=_auth(tok))
    assert r1.status_code == 200
    assert r1.json()["cached"] is False
    # expire the cached payload so the next call must hit the provider
    db = SessionLocal()
    try:
        for row in db.query(TradeStatistic).all():
            from datetime import datetime, timedelta
            row.fetched_at = datetime.utcnow() - timedelta(hours=48)
        db.commit()
    finally:
        db.close()
    # 2) provider now quota-fails -> cached payload served, flagged stale
    fake_provider.fail = tp.TradeQuotaError("UN Comtrade rate limit (429)")
    r2 = client.get("/api/trade/search",
                    params={"reporter_code": 826, "period_from": "2023",
                            "period_to": "2023", "flow": "M"},
                    headers=_auth(tok))
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["stale"] is True
    assert "rate limit" in body["stale_reason"].lower()
    assert body["count"] == 2  # real rows from cache, not a fake 0
    assert body["cached"] is True


def test_search_quota_error_without_cache_is_typed_429(monkeypatch):
    tok = _user("trade-quota@example.com")
    fp = FakeTradeProvider(fail=tp.TradeQuotaError(
        "UN Comtrade rate limit reached (HTTP 429)"))
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: fp)
    r = client.get("/api/trade/search",
                   params={"reporter_code": 826, "period_from": "2023",
                           "period_to": "2023"},
                   headers=_auth(tok))
    assert r.status_code == 429
    assert "429" in r.json()["detail"]
    assert "0 results" not in r.json()["detail"].lower()


def test_search_config_error_is_503(monkeypatch):
    tok = _user("trade-cfg@example.com")
    fp = FakeTradeProvider(fail=tp.TradeConfigError(
        "Trade data provider is disabled (TRADE_PROVIDER=none)"))
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: fp)
    r = client.get("/api/trade/search",
                   params={"reporter_code": 826, "period_from": "2023",
                           "period_to": "2023"},
                   headers=_auth(tok))
    assert r.status_code == 503
    assert "disabled" in r.json()["detail"]


def test_statistics_returns_series(fake_provider):
    fake_provider.series = [
        {"period": "2022", "flow": "M", "value": 100.0, "records": 3},
        {"period": "2023", "flow": "M", "value": 250.0, "records": 4},
    ]
    tok = _user("trade-stats@example.com")
    r = client.get("/api/trade/statistics",
                   params={"reporter_code": 826, "period_from": "2022",
                           "period_to": "2023", "flow": "M"},
                   headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "aggregated_statistics"
    assert len(body["series"]) == 2
    assert body["total_value"] == 350.0


def test_hs_codes_endpoint(fake_provider):
    tok = _user("trade-hs@example.com")
    r = client.get("/api/trade/hs-codes", params={"code": "7113"},
                   headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["record_type"] == "reference_data"
    assert body["items"][0]["code"] == "7113"
    # needs q or code
    r2 = client.get("/api/trade/hs-codes", headers=_auth(tok))
    assert r2.status_code == 422


def test_countries_endpoint(fake_provider):
    tok = _user("trade-countries@example.com")
    r = client.get("/api/trade/countries", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["reporters"][0]["name"] == "United Kingdom"


# ---------------------------------------------------------------------------
# saved searches (CRUD + tenant isolation)
# ---------------------------------------------------------------------------

def test_saved_search_crud_and_tenant_isolation():
    a = _user("trade-owner@example.com")
    b = _user("trade-other@example.com")
    q = {"reporter_code": 826, "period_from": "2022", "period_to": "2023",
         "flow": "both", "cmd_code": "7113"}

    # A creates
    r = client.post("/api/trade/saved-searches",
                    json={"name": "UK jewellery imports", "kind": "trade",
                          "query": q}, headers=_auth(a))
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["query"]["cmd_code"] == "7113"

    # duplicate name for same user -> 409
    r = client.post("/api/trade/saved-searches",
                    json={"name": "UK jewellery imports", "kind": "trade",
                          "query": q}, headers=_auth(a))
    assert r.status_code == 409

    # A lists -> sees it
    r = client.get("/api/trade/saved-searches", headers=_auth(a))
    assert r.status_code == 200
    assert any(i["id"] == sid for i in r.json()["items"])

    # B lists -> does NOT see it
    r = client.get("/api/trade/saved-searches", headers=_auth(b))
    assert r.status_code == 200
    assert all(i["id"] != sid for i in r.json()["items"])

    # B cannot delete A's row (404, not 403 — no existence leak)
    r = client.delete(f"/api/trade/saved-searches/{sid}", headers=_auth(b))
    assert r.status_code == 404

    # A deletes it
    r = client.delete(f"/api/trade/saved-searches/{sid}", headers=_auth(a))
    assert r.status_code == 204
    r = client.get("/api/trade/saved-searches", headers=_auth(a))
    assert all(i["id"] != sid for i in r.json()["items"])


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def test_export_csv_includes_source_attribution(fake_provider):
    tok = _user("trade-export@example.com")
    r = client.get("/api/trade/export",
                   params={"format": "csv", "reporter_code": 826,
                           "period_from": "2023", "period_to": "2023"},
                   headers=_auth(tok))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in r.headers["content-disposition"]
    text = r.text
    assert "# Source: UN Comtrade" in text
    assert "# Record type: aggregated_statistics" in text
    assert "NOT individual shipment records" in text
    # header row + 2 data rows (plus 8 comment lines)
    assert "Period,Flow,Flow Code,Reporter" in text
    assert text.count("\n") >= 10


def test_export_xlsx_works(fake_provider):
    tok = _user("trade-export-x@example.com")
    r = client.get("/api/trade/export",
                   params={"format": "xlsx", "reporter_code": 826,
                           "period_from": "2023", "period_to": "2023"},
                   headers=_auth(tok))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"  # real xlsx zip magic


# ---------------------------------------------------------------------------
# data sources (no secrets)
# ---------------------------------------------------------------------------

def test_data_sources_reports_status_without_key(monkeypatch):
    monkeypatch.setattr(cfg.settings, "UN_COMTRADE_API_KEY",
                        "super-secret-key-do-not-leak")
    tok = _user("trade-ds@example.com")
    r = client.get("/api/data-sources", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.text
    assert "super-secret-key-do-not-leak" not in body
    keys = [i["key"] for i in r.json()["items"]]
    assert "un_comtrade" in keys
    # planned sources are honestly marked not_configured
    itc = next(i for i in r.json()["items"] if i["key"] == "itc_trade_map")
    assert itc["status"] == "not_configured"
    un = next(i for i in r.json()["items"] if i["key"] == "un_comtrade")
    assert un["auth_configured"] is True  # bool only, key never returned
    assert un["status"] == "active"


def test_data_sources_never_connected_sources_stay_unclaimed():
    tok = _user("trade-ds2@example.com")
    r = client.get("/api/data-sources", headers=_auth(tok))
    for item in r.json()["items"]:
        if item["key"] != "un_comtrade":
            assert item["status"] == "not_configured"


# ---------------------------------------------------------------------------
# cache layer (direct)
# ---------------------------------------------------------------------------

def test_cache_roundtrip_and_stale_flag():
    db = SessionLocal()
    try:
        key = api_trade._cache_key("search", {"reporter_code": 1,
                                              "flow": "M"})
        assert api_trade._cache_key("search", {"flow": "M",
                                               "reporter_code": 1}) == key
        payload = {"record_type": "aggregated_statistics",
                   "rows": [{"period": "2023"}], "count": 1}
        api_trade._cache_put(db, key, {"reporter_code": 1}, payload, 1,
                             status=200)
        row = api_trade._cache_get(db, key)
        assert row is not None and api_trade._fresh(row)
        stale = api_trade._stale_wrap(row, "UN Comtrade rate limit (429)")
        assert stale["stale"] is True
        assert stale["count"] == 1  # cached rows preserved
        # TTL: backdate -> no longer fresh
        from datetime import datetime, timedelta
        row.fetched_at = datetime.utcnow() - timedelta(hours=48)
        db.commit()
        assert not api_trade._fresh(row)
        # cleanup
        db.delete(row)
        db.commit()
    finally:
        db.close()


def test_provider_status_has_no_secret(monkeypatch):
    monkeypatch.setattr(cfg.settings, "UN_COMTRADE_API_KEY", "sk-secret")
    p = tp.UNComtradeProvider(transport=httpx.MockTransport(_handler()),
                              sleep=lambda s: None)
    st = p.status()
    assert st["auth_configured"] is True
    assert "sk-secret" not in json.dumps(st)
    p.close()
