"""Phase 4 tests: competitor analysis, HS intelligence, country comparison.

Honesty contracts under test:
- competitor rankings are aggregated COUNTRY-level statistics with an
  explicit "no company identities" note + source block — never supplier
  companies invented from trade rows;
- the World aggregate row is dropped when its parts are present
  (double-count guard), kept for explicit World-only results;
- HS hierarchy comes from the official UN reference — unknown codes are
  an honest 404, never an invented description;
- compare shares are labelled as shares of the COMPARED set, not world
  trade; typed provider errors (429/503/502) propagate — never a fake
  "0 results";
- statistics cache is per reporter so compare reuses warmed series;
- ALL external calls are mocked — zero network, zero credits.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models_trade import TradeStatistic, DataProvider
from app import trade_intel
from app import trade_providers as tp
from app import api_trade
from app import config as cfg

client = TestClient(app)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_uid = {"n": 0}


def _user():
    _uid["n"] += 1
    r = client.post("/api/auth/signup",
                    json={"email": f"p4-{_uid['n']}@example.com",
                          "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


# Destination-import rows for competitors (Germany 276 imports, partners
# as suppliers) — includes a World aggregate row to exercise the guard.
_IMPORT_ROWS = [
    {"period": "2023", "flowCode": "M", "reporterCode": 276,
     "partnerCode": 392, "cmdCode": "7113", "primaryValue": 600.0,
     "partnerDesc": "Japan"},
    {"period": "2023", "flowCode": "M", "reporterCode": 276,
     "partnerCode": 842, "cmdCode": "7113", "primaryValue": 300.0,
     "partnerDesc": "United States"},
    {"period": "2023", "flowCode": "M", "reporterCode": 276,
     "partnerCode": 380, "cmdCode": "7113", "primaryValue": 100.0,
     "partnerDesc": "Italy"},
    {"period": "2023", "flowCode": "M", "reporterCode": 276,
     "partnerCode": 0, "cmdCode": "7113", "primaryValue": 1000000.0,
     "partnerDesc": "World"},   # aggregate must NOT be ranked
]

_HS = {"results": [
    {"id": "71", "text": "71 - Natural or cultured pearls, precious stones",
     "parent": "#", "isLeaf": "0", "aggrLevel": 2},
    {"id": "7113", "text": "7113 - Articles of jewellery of precious metal",
     "parent": "71", "isLeaf": "0", "aggrLevel": 4},
    {"id": "711319", "text": "711319 - Of other precious metal",
     "parent": "7113", "isLeaf": "1", "aggrLevel": 6},
]}

_SERIES = {
    276: [{"period": "2022", "flow": "M", "value": 100.0, "records": 1},
          {"period": "2023", "flow": "M", "value": 150.0, "records": 1}],
    826: [{"period": "2022", "flow": "M", "value": 200.0, "records": 1},
          {"period": "2023", "flow": "M", "value": 100.0, "records": 1}],
}

_SOURCE = {
    "name": "UN Comtrade",
    "url": tp.PREVIEW_URL,
    "record_type": "aggregated_statistics",
    "coverage": "Annual merchandise trade (HS)",
    "periods": ["2023"],
    "disclaimer": "Aggregated trade statistics — NOT individual shipment "
                  "records and NOT buyer/supplier identities.",
}


class FakeTrade:
    """In-memory provider for Phase 4 endpoint tests — zero network."""

    name = "un_comtrade"

    def __init__(self, rows=None, series=None, fail=None):
        self.rows = rows if rows is not None else _IMPORT_ROWS
        self.series = series if series is not None else _SERIES
        self.fail = fail
        self.search_calls = 0
        self.stats_calls = 0

    def search(self, **kw):
        self.search_calls += 1
        if self.fail:
            raise self.fail
        return {"rows": [dict(r) for r in self.rows], "truncated": False,
                "source": dict(_SOURCE)}

    def statistics(self, **kw):
        self.stats_calls += 1
        if self.fail:
            raise self.fail
        rows = self.series.get(kw.get("reporter_code"), [])
        return {"rows": [], "truncated": False, "source": dict(_SOURCE),
                "series": [dict(s) for s in rows],
                "total_value": sum(s.get("value", 0) for s in rows)}

    def hs_search(self, query=None, code=None, limit=50):
        rows = _HS["results"]
        if code:
            return [dict(r) for r in rows
                    if str(r.get("id")) == code
                    or str(r.get("parent")) == code]
        q = (query or "").lower()
        return [dict(r) for r in rows if q in str(r.get("text", "")).lower()]

    def hs_rows(self):
        return [dict(r) for r in _HS["results"]]

    def reporter_map(self):
        return {
            276: {"reporterCode": 276, "reporterDesc": "Germany"},
            826: {"reporterCode": 826, "reporterDesc": "United Kingdom"},
            380: {"reporterCode": 380, "reporterDesc": "Italy"},
            392: {"reporterCode": 392, "reporterDesc": "Japan"},
            842: {"reporterCode": 842, "reporterDesc": "United States"},
        }

    def countries(self):
        return {"reporters": [], "partners": [],
                "source": {"name": "UN Comtrade reference",
                           "record_type": "reference_data"}}

    def status(self):
        return {"key": "un_comtrade", "name": "UN Comtrade",
                "endpoint": tp.PREVIEW_URL, "auth_type": "none",
                "auth_configured": False, "mode": "public preview",
                "limits": "<=500 records/call", "status": "active"}


@pytest.fixture(autouse=True)
def phase4_env(monkeypatch):
    """Offline, fast, deterministic environment for every Phase 4 test."""
    monkeypatch.setattr(cfg.settings, "TRADE_PROVIDER", "un_comtrade")
    monkeypatch.setattr(cfg.settings, "UN_COMTRADE_API_KEY", "")
    monkeypatch.setattr(cfg.settings, "TRADE_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(cfg.settings, "TRADE_MAX_RETRIES", 2)
    monkeypatch.setattr(cfg.settings, "TRADE_CACHE_TTL_HOURS", 24)
    tp.reset_trade_provider()
    db = SessionLocal()
    try:
        db.query(TradeStatistic).delete()
        db.query(DataProvider).delete()
        db.commit()
    finally:
        db.close()
    yield
    tp.reset_trade_provider()


@pytest.fixture
def fake(monkeypatch):
    f = FakeTrade()
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: f)
    return f


# ---------------------------------------------------------------------------
# pure: rank_suppliers
# ---------------------------------------------------------------------------

def test_rank_suppliers_orders_and_computes_shares():
    out = trade_intel.rank_suppliers(
        [dict(r) for r in _IMPORT_ROWS if r["partnerCode"] != 0], top_n=10)
    names = [(s["partner_desc"], s["value"], s["share_pct"])
             for s in out["suppliers"]]
    assert names[0][0] == "Japan" and names[0][1] == 600.0
    assert names[0][2] == pytest.approx(60.0)
    assert [s["rank"] for s in out["suppliers"]] == [1, 2, 3]
    assert out["total_value"] == 1000.0
    assert out["partners_count"] == 3
    assert out["periods"] == ["2023"]


def test_rank_suppliers_drops_world_aggregate_when_parts_present():
    out = trade_intel.rank_suppliers([dict(r) for r in _IMPORT_ROWS])
    codes = [s["partner_code"] for s in out["suppliers"]]
    assert 0 not in codes, "World aggregate must not be ranked alongside parts"
    assert out["dropped_world"] is True
    assert out["total_value"] == 1000.0  # not 1,001,000


def test_rank_suppliers_keeps_explicit_world_only_result():
    world_only = [r for r in _IMPORT_ROWS if r["partnerCode"] == 0]
    out = trade_intel.rank_suppliers([dict(r) for r in world_only])
    assert [s["partner_code"] for s in out["suppliers"]] == [0]
    assert out["dropped_world"] is False
    assert out["total_value"] == 1000000.0


def test_rank_suppliers_handles_missing_and_zero_values():
    rows = [{"period": "2023", "flowCode": "M", "partnerCode": 1,
             "primaryValue": None},
            {"period": "2023", "flowCode": "M", "partnerCode": 2,
             "primaryValue": 0}]
    out = trade_intel.rank_suppliers(rows)
    assert out["total_value"] == 0.0
    assert all(s["share_pct"] == 0.0 for s in out["suppliers"])
    assert out["partners_count"] == 2
    # empty input -> honest empty result, no crash
    empty = trade_intel.rank_suppliers([])
    assert empty["suppliers"] == [] and empty["total_value"] == 0.0


def test_rank_suppliers_top_n_truncates():
    rows = [{"period": "2023", "flowCode": "M", "partnerCode": i,
             "primaryValue": float(100 - i)} for i in range(1, 21)]
    out = trade_intel.rank_suppliers(rows, top_n=5)
    assert len(out["suppliers"]) == 5
    assert out["truncated"] is True
    assert out["partners_count"] == 20
    # total covers ALL partners, not just the shown top-N
    assert out["total_value"] == sum(100 - i for i in range(1, 21))


# ---------------------------------------------------------------------------
# pure: hs_hierarchy
# ---------------------------------------------------------------------------

def test_hs_hierarchy_walks_official_parent_chain():
    h = trade_intel.hs_hierarchy(_HS["results"], "711319")
    assert h is not None
    assert h["description"] == "Of other precious metal"
    assert [a["code"] for a in h["ancestors"]] == ["71", "7113"]
    assert h["chapter"]["code"] == "71"
    assert h["is_leaf"] is True
    assert h["children"] == []


def test_hs_hierarchy_lists_children_and_chapter_self():
    h = trade_intel.hs_hierarchy(_HS["results"], "7113")
    assert [c["code"] for c in h["children"]] == ["711319"]
    assert h["is_leaf"] is False
    chapter = trade_intel.hs_hierarchy(_HS["results"], "71")
    assert chapter["ancestors"] == []
    assert chapter["chapter"]["code"] == "71"


def test_hs_hierarchy_unknown_code_is_none_not_invented():
    assert trade_intel.hs_hierarchy(_HS["results"], "999999") is None
    assert trade_intel.hs_hierarchy([], "7113") is None


def test_strip_hs_desc_removes_code_prefix():
    assert trade_intel.strip_hs_desc(
        "7113 - Articles of jewellery") == "Articles of jewellery"
    assert trade_intel.strip_hs_desc(None) == ""


# ---------------------------------------------------------------------------
# endpoints: auth + competitors
# ---------------------------------------------------------------------------

def test_phase4_endpoints_require_auth():
    for path, params in (
            ("/api/trade/competitors", {"destination_code": 276}),
            ("/api/trade/hs-intel", {"cmd_code": "7113"}),
            ("/api/trade/compare", {"reporter_codes": "276,826"})):
        r = client.get(path, params=params)
        assert r.status_code == 401, path


def test_competitors_envelope_is_honest(fake):
    tok = _user()
    r = client.get("/api/trade/competitors",
                   params={"destination_code": 276, "cmd_code": "7113",
                           "period": "2023", "top_n": 10},
                   headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "aggregated_statistics"
    assert body["source"]["name"] == "UN Comtrade"
    assert body["source"]["destination_name"] == "Germany"
    assert "NOT individual shipment records" in body["source"]["disclaimer"]
    # no company identities claimed
    assert "do not identify individual companies" in body["note"]
    # ranked suppliers with shares; World row excluded
    sup = body["suppliers"]
    assert [s["rank"] for s in sup] == [1, 2, 3]
    assert sup[0]["partner_desc"] == "Japan"
    assert sup[0]["share_pct"] == pytest.approx(60.0)
    assert body["dropped_world"] is True
    assert body["total_value"] == 1000.0
    assert body["params"]["flow"] == "M"
    assert body["stale"] is False and body["cached"] is False


def test_competitors_validation_is_typed_422(fake):
    tok = _user()
    h = _auth(tok)
    # bad HS code
    r = client.get("/api/trade/competitors",
                   params={"destination_code": 276, "cmd_code": "gold"},
                   headers=h)
    assert r.status_code == 422
    assert "HS code" in r.json()["detail"]
    # bad period
    r = client.get("/api/trade/competitors",
                   params={"destination_code": 276, "period": "twenty"},
                   headers=h)
    assert r.status_code == 422
    # destination <= 0 (query validation)
    r = client.get("/api/trade/competitors",
                   params={"destination_code": 0}, headers=h)
    assert r.status_code == 422


def test_competitors_quota_is_typed_429_never_fake_empty(monkeypatch):
    tok = _user()
    f = FakeTrade(fail=tp.TradeQuotaError(
        "UN Comtrade rate limit reached (HTTP 429)"))
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: f)
    r = client.get("/api/trade/competitors",
                   params={"destination_code": 276, "cmd_code": "7113"},
                   headers=_auth(tok))
    assert r.status_code == 429
    assert "429" in r.json()["detail"]
    assert "0 results" not in r.json()["detail"].lower()


def test_competitors_serves_stale_cache_on_quota_error(fake):
    tok = _user()
    params = {"destination_code": 276, "cmd_code": "7113", "period": "2023"}
    r1 = client.get("/api/trade/competitors", params=params, headers=_auth(tok))
    assert r1.status_code == 200 and r1.json()["cached"] is False
    # expire the cache, then make the provider quota-fail
    db = SessionLocal()
    try:
        from datetime import datetime, timedelta
        for row in db.query(TradeStatistic).all():
            row.fetched_at = datetime.utcnow() - timedelta(hours=48)
        db.commit()
    finally:
        db.close()
    fake.fail = tp.TradeQuotaError("UN Comtrade rate limit (429)")
    r2 = client.get("/api/trade/competitors", params=params, headers=_auth(tok))
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["stale"] is True
    assert "rate limit" in body["stale_reason"].lower()
    assert body["suppliers"], "cached rows must be served, not a fake 0"


def test_competitors_config_error_is_503(monkeypatch):
    tok = _user()
    f = FakeTrade(fail=tp.TradeConfigError(
        "Trade data provider is disabled (TRADE_PROVIDER=none)"))
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: f)
    r = client.get("/api/trade/competitors",
                   params={"destination_code": 276}, headers=_auth(tok))
    assert r.status_code == 503
    assert "disabled" in r.json()["detail"]


# ---------------------------------------------------------------------------
# endpoints: HS intelligence
# ---------------------------------------------------------------------------

def test_hs_intel_returns_official_hierarchy_without_trend(fake):
    tok = _user()
    r = client.get("/api/trade/hs-intel", params={"cmd_code": "711319"},
                   headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "hs_intelligence"
    cls = body["classification"]
    assert cls["record_type"] == "reference_data"
    assert cls["code"] == "711319"
    assert cls["description"] == "Of other precious metal"
    assert [a["code"] for a in cls["ancestors"]] == ["71", "7113"]
    assert cls["chapter"]["code"] == "71"
    assert "customs broker" in cls["source"]["disclaimer"]
    assert body["trend"] is None, "no reporter -> no trend, no network"


def test_hs_intel_with_reporter_includes_aggregate_trend(fake):
    tok = _user()
    r = client.get("/api/trade/hs-intel",
                   params={"cmd_code": "7113", "reporter_code": 826,
                           "period_from": "2022", "period_to": "2023",
                           "flow": "M"},
                   headers=_auth(tok))
    assert r.status_code == 200, r.text
    trend = r.json()["trend"]
    assert trend is not None
    assert trend["record_type"] == "aggregated_statistics"
    assert trend["params"]["cmd_code"] == "7113"
    assert trend["params"]["reporter_code"] == 826
    assert isinstance(trend["series"], list)
    assert "NOT individual shipment records" in trend["source"]["disclaimer"]
    assert fake.stats_calls == 1


def test_hs_intel_unknown_code_is_honest_404(fake):
    tok = _user()
    r = client.get("/api/trade/hs-intel", params={"cmd_code": "999999"},
                   headers=_auth(tok))
    assert r.status_code == 404
    assert "999999" in r.json()["detail"]
    assert "not found" in r.json()["detail"].lower()


def test_hs_intel_bad_code_is_422(fake):
    tok = _user()
    r = client.get("/api/trade/hs-intel", params={"cmd_code": "gold"},
                   headers=_auth(tok))
    assert r.status_code == 422
    assert "HS code" in r.json()["detail"]


# ---------------------------------------------------------------------------
# endpoints: country comparison
# ---------------------------------------------------------------------------

def test_compare_two_countries_side_by_side(fake):
    tok = _user()
    r = client.get("/api/trade/compare",
                   params={"reporter_codes": "276,826",
                           "period_from": "2022", "period_to": "2023",
                           "flow": "M", "cmd_code": "7113"},
                   headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "aggregated_statistics"
    assert "NOT individual shipment records" in body["source"]["disclaimer"]
    names = {c["reporter_code"]: c["name"] for c in body["countries"]}
    assert names == {276: "Germany", 826: "United Kingdom"}
    assert body["countries"][0]["total_value"] == 250.0
    assert body["countries"][1]["total_value"] == 300.0
    # matrix: per (period, flow) values + shares among the COMPARED set
    m = {(row["period"], row["flow"]): row for row in body["matrix"]}
    cell = m[("2023", "M")]
    assert cell["values"] == {"276": 150.0, "826": 100.0}
    assert cell["combined"] == 250.0
    assert cell["shares"]["276"] == pytest.approx(60.0)
    assert "COMPARED set" in body["note"]
    assert fake.stats_calls == 2  # one cached series per reporter


def test_compare_requires_at_least_two_countries(fake):
    tok = _user()
    r = client.get("/api/trade/compare", params={"reporter_codes": "276"},
                   headers=_auth(tok))
    assert r.status_code == 422
    assert "at least 2" in r.json()["detail"]


def test_compare_caps_four_countries(fake):
    tok = _user()
    r = client.get("/api/trade/compare",
                   params={"reporter_codes": "1,2,3,4,5"},
                   headers=_auth(tok))
    assert r.status_code == 422
    assert "4 countries" in r.json()["detail"]


def test_compare_rejects_bad_reporter_codes(fake):
    tok = _user()
    r = client.get("/api/trade/compare",
                   params={"reporter_codes": "276,abc"}, headers=_auth(tok))
    assert r.status_code == 422
    assert "abc" in r.json()["detail"]
    r = client.get("/api/trade/compare",
                   params={"reporter_codes": ""}, headers=_auth(tok))
    assert r.status_code == 422


def test_compare_limits_span_to_five_years(fake):
    tok = _user()
    r = client.get("/api/trade/compare",
                   params={"reporter_codes": "276,826",
                           "period_from": "2018", "period_to": "2023"},
                   headers=_auth(tok))
    assert r.status_code == 422
    assert "5 years" in r.json()["detail"]
    # exactly 5 years is fine
    r = client.get("/api/trade/compare",
                   params={"reporter_codes": "276,826",
                           "period_from": "2019", "period_to": "2023"},
                   headers=_auth(tok))
    assert r.status_code == 200


def test_compare_reuses_statistics_cache(fake):
    tok = _user()
    params = {"reporter_codes": "276,826", "period_from": "2022",
              "period_to": "2023", "flow": "M"}
    r1 = client.get("/api/trade/compare", params=params, headers=_auth(tok))
    assert r1.status_code == 200
    assert r1.json()["cached"] is False
    assert fake.stats_calls == 2
    # identical request again: both series served from cache
    r2 = client.get("/api/trade/compare", params=params, headers=_auth(tok))
    assert r2.status_code == 200
    body = r2.json()
    assert body["cached"] is True
    assert all(c["cached"] for c in body["countries"])
    assert fake.stats_calls == 2, "cache must prevent extra provider calls"


def test_compare_quota_is_typed_429(monkeypatch):
    tok = _user()
    f = FakeTrade(fail=tp.TradeQuotaError(
        "UN Comtrade rate limit reached (HTTP 429)"))
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: f)
    r = client.get("/api/trade/compare",
                   params={"reporter_codes": "276,826"},
                   headers=_auth(tok))
    assert r.status_code == 429
    assert "429" in r.json()["detail"]
    assert "0 results" not in r.json()["detail"].lower()


def test_compare_stale_cache_served_per_country(monkeypatch):
    tok = _user()
    f = FakeTrade()
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: f)
    params = {"reporter_codes": "276,826", "period_from": "2022",
              "period_to": "2023"}
    r1 = client.get("/api/trade/compare", params=params, headers=_auth(tok))
    assert r1.status_code == 200
    db = SessionLocal()
    try:
        from datetime import datetime, timedelta
        for row in db.query(TradeStatistic).all():
            row.fetched_at = datetime.utcnow() - timedelta(hours=48)
        db.commit()
    finally:
        db.close()
    f.fail = tp.TradeQuotaError("UN Comtrade rate limit (429)")
    r2 = client.get("/api/trade/compare", params=params, headers=_auth(tok))
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["stale"] is True
    assert all(c["series"] for c in body["countries"]), \
        "cached series must be served, not empty"
