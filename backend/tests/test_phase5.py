"""Phase 5 tests: AI Trade Analyst, lead scoring, AI research summaries.

Honesty contracts under test:
- narratives are assembled ONLY from computed facts: every number in the
  template narrative must appear in the facts (enforced by extracting
  numbers from both) — the AI layer cannot invent figures;
- Gemini output containing an unverified number is discarded and the
  deterministic template is served with an honest fallback_reason;
- responses carry engine + ai_generated labels (never pretend to be AI,
  never hide AI);
- lead scores are explainable: factors carry points/max/reason and sum
  EXACTLY to the score; missing inputs score 0 with an honest reason;
- research summaries read persisted rows only — zero network;
- tenant scoping + typed validation (422) + rate limits (429);
- ALL external calls are mocked — zero network, zero credits.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Company, Contact, Source
from app.models_research import ContactEvidence, EvaluationResult
from app.models_trade import TradeStatistic, DataProvider
from app import trade_analyst
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
                    json={"email": f"p5-{_uid['n']}@example.com",
                          "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _find_user_id(tok):
    """Resolve the signed-up user's id (signup returns tokens, not id)."""
    from app.security import decode_token
    return decode_token(tok["access_token"])


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


def _mk_source(user_id, url="https://fair.example/profile"):
    db = SessionLocal()
    try:
        s = Source(user_id=user_id, url=url, name="Test Fair",
                   status="COMPLETED")
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id
    finally:
        db.close()


def _mk_contact(user_id, company_id, kind="EMAIL", value="a@example.com"):
    db = SessionLocal()
    try:
        db.add(Contact(user_id=user_id, company_id=company_id, kind=kind,
                       value=value, normalized=value.lower(),
                       source_url="https://example.com/contact"))
        db.commit()
    finally:
        db.close()


def _mk_evidence(user_id, company_id, value="b@example.com"):
    db = SessionLocal()
    try:
        db.add(ContactEvidence(
            user_id=user_id, company_id=company_id, contact_type="email",
            raw_value=value, source_url="https://example.com/about",
            extraction_method="mailto_link", confidence=0.9,
            validation_status="FORMAT_VALID"))
        db.commit()
    finally:
        db.close()


def _mk_evaluation(user_id, company_id, buyer_fit=80.0, **scores):
    db = SessionLocal()
    try:
        db.add(EvaluationResult(
            user_id=user_id, company_id=company_id,
            buyer_fit_score=buyer_fit,
            overall_score=scores.get("overall", 75.0),
            contact_completeness_score=scores.get("contact", 90.0),
            evidence_quality_score=scores.get("evidence", 70.0),
            validation_coverage=scores.get("validation", 60.0),
            result_json='{"buyer_type": "Importer", "product_fit": "High", '
                        '"country_fit": "Medium", '
                        '"recommended_next_action": "Send intro email", '
                        '"missing_evidence": ["phone number"]}',
            issues="[]", warnings="[]"))
        db.commit()
    finally:
        db.close()


# FakeTrade: in-memory provider (copied shape from Phase 4) — zero network.

_SOURCE = {
    "name": "UN Comtrade",
    "url": tp.PREVIEW_URL,
    "record_type": "aggregated_statistics",
    "coverage": "Annual merchandise trade (HS)",
    "periods": ["2023"],
    "disclaimer": "Aggregated trade statistics — NOT individual shipment "
                  "records and NOT buyer/supplier identities.",
}

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
]

_SERIES = {
    276: [{"period": "2022", "flow": "M", "value": 100.0, "records": 1},
          {"period": "2023", "flow": "M", "value": 150.0, "records": 1}],
    826: [{"period": "2022", "flow": "M", "value": 200.0, "records": 1},
          {"period": "2023", "flow": "M", "value": 100.0, "records": 1}],
}


class FakeTrade:
    name = "un_comtrade"

    def __init__(self, rows=None, series=None, fail=None):
        self.rows = rows if rows is not None else _IMPORT_ROWS
        self.series = series if series is not None else _SERIES
        self.fail = fail

    def search(self, **kw):
        if self.fail:
            raise self.fail
        return {"rows": [dict(r) for r in self.rows], "truncated": False,
                "source": dict(_SOURCE)}

    def statistics(self, **kw):
        if self.fail:
            raise self.fail
        rows = self.series.get(kw.get("reporter_code"), [])
        return {"rows": [], "truncated": False, "source": dict(_SOURCE),
                "series": [dict(s) for s in rows],
                "total_value": sum(s.get("value", 0) for s in rows)}

    def reporter_map(self):
        return {276: {"reporterCode": 276, "reporterDesc": "Germany"},
                826: {"reporterCode": 826, "reporterDesc": "United Kingdom"},
                380: {"reporterCode": 380, "reporterDesc": "Italy"}}

    def countries(self):
        return {"reporters": [], "partners": [],
                "source": {"name": "UN Comtrade reference",
                           "record_type": "reference_data"}}


@pytest.fixture(autouse=True)
def phase5_env(monkeypatch):
    """Offline, fast, deterministic environment for every Phase 5 test.

    GEMINI_API_KEY is forced empty so the AI layer always takes the
    deterministic template path unless a test opts in explicitly —
    no network, no credits, ever.
    """
    monkeypatch.setattr(cfg.settings, "TRADE_PROVIDER", "un_comtrade")
    monkeypatch.setattr(cfg.settings, "UN_COMTRADE_API_KEY", "")
    monkeypatch.setattr(cfg.settings, "TRADE_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(cfg.settings, "TRADE_MAX_RETRIES", 2)
    monkeypatch.setattr(cfg.settings, "TRADE_CACHE_TTL_HOURS", 24)
    monkeypatch.setattr(cfg.settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
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


def _numbers(text):
    return trade_analyst.numbers_in(text)


# ---------------------------------------------------------------------------
# pure: fact builders only restate computed numbers
# ---------------------------------------------------------------------------

def test_statistics_facts_restate_payload_numbers():
    payload = {
        "params": {"reporter_code": 276, "period_from": "2022",
                   "period_to": "2023", "flow": "M", "cmd_code": None},
        "series": _SERIES[276], "total_value": 250.0, "stale": False,
    }
    facts = trade_analyst.statistics_facts(payload)
    joined = " ".join(facts)
    assert "250.00" in joined          # total (and the imports total)
    assert "150.00" in joined          # peak period value
    assert "276" in joined             # reporter
    assert "all products" in joined    # no HS filter stated honestly
    # every number in every fact is a number FROM the payload
    p = payload["params"]
    payload_nums = _numbers(
        f"{p['reporter_code']} {p['period_from']} {p['period_to']} "
        f"{p['period_from']}-{p['period_to']} "
        f"{p['flow']} 250.0 100.0 150.0 2")
    for f in facts:
        assert _numbers(f) <= payload_nums, f


def test_competitors_facts_include_ranks_and_no_company_claims():
    payload = {
        "params": {"destination_code": 276, "period": "2023",
                   "cmd_code": "7113", "top_n": 15, "flow": "M"},
        "source": {"destination_name": "Germany"},
        "suppliers": [
            {"rank": 1, "partner_code": 392, "partner_desc": "Japan",
             "value": 600.0, "share_pct": 60.0, "records": 1},
            {"rank": 2, "partner_code": 842, "partner_desc": "United States",
             "value": 300.0, "share_pct": 30.0, "records": 1},
        ],
        "total_value": 1000.0, "partners_count": 3,
        "dropped_world": True, "truncated": False,
    }
    facts = trade_analyst.competitors_facts(payload)
    joined = " ".join(facts)
    assert "Germany" in joined and "Japan" in joined
    assert "1,000.00" in joined
    assert "double-count guard" in joined
    assert "does not identify individual companies" in joined


def test_compare_facts_label_shares_as_compared_set():
    payload = {
        "params": {"reporter_codes": [276, 826], "period_from": "2022",
                   "period_to": "2023", "flow": "M", "cmd_code": None},
        "countries": [
            {"reporter_code": 276, "name": "Germany", "total_value": 250.0,
             "series": []},
            {"reporter_code": 826, "name": "United Kingdom",
             "total_value": 300.0, "series": []},
        ],
        "matrix": [{"period": "2023", "flow": "M",
                    "values": {"276": 150.0, "826": 100.0},
                    "combined": 250.0}],
    }
    facts = trade_analyst.compare_facts(payload)
    joined = " ".join(facts)
    assert "Germany (276)" in joined and "United Kingdom (826)" in joined
    assert "Largest total: United Kingdom" in joined
    assert "not world trade" in joined


def test_research_facts_read_only_persisted_fields():
    report = {
        "company": {"name": "Acme Imports", "city": "Mumbai",
                    "country": "India", "category": "Importer"},
        "contacts": [{"kind": "EMAIL", "value": "a@x.com"},
                     {"kind": "PHONE", "value": "+1 555"}],
        "evidence": [{"id": 1}, {"id": 2}],
        "evaluation": {"buyer_fit_score": 80.0, "overall_score": 75.0,
                       "contact_completeness_score": 90.0,
                       "evidence_quality_score": 70.0,
                       "validation_coverage": 60.0,
                       "buyer_type": "Importer", "product_fit": "High",
                       "country_fit": "Medium",
                       "recommended_next_action": "Send intro email",
                       "missing_evidence": ["phone number"],
                       "evaluated_at": "2026-11-01T00:00:00"},
    }
    facts = trade_analyst.research_facts(report)
    joined = " ".join(facts)
    assert "Acme Imports" in joined
    assert "1 email(s), 1 phone(s)" in joined
    assert "buyer fit 80" in joined
    assert "Importer" in joined
    assert "phone number" in joined


def test_research_facts_without_evaluation_are_honest():
    facts = trade_analyst.research_facts({
        "company": {"name": "Bare Co"}, "contacts": [], "evidence": [],
        "evaluation": None})
    joined = " ".join(facts)
    assert "No persisted research evaluation yet" in joined
    assert "evidence rows: 0" in joined


# ---------------------------------------------------------------------------
# pure: template narrative can ONLY restate the facts
# ---------------------------------------------------------------------------

def test_template_narrative_numbers_all_come_from_facts():
    for kind, payload in (
            ("statistics", {
                "params": {"reporter_code": 276, "period_from": "2022",
                           "period_to": "2023", "flow": "M", "cmd_code": None},
                "series": _SERIES[276], "total_value": 250.0}),
            ("competitors", {
                "params": {"destination_code": 276, "period": "2023",
                           "cmd_code": "7113", "top_n": 15, "flow": "M"},
                "source": {"destination_name": "Germany"},
                "suppliers": [{"rank": 1, "partner_code": 392,
                               "partner_desc": "Japan", "value": 600.0,
                               "share_pct": 60.0, "records": 1}],
                "total_value": 600.0, "partners_count": 1})):
        facts = trade_analyst.build_facts(kind, payload)
        narrative = trade_analyst.template_report(kind, facts)
        fact_nums = _numbers(" ".join(facts))
        # NO number may appear in the narrative that is not in the facts
        assert _numbers(narrative) <= fact_nums, kind
        # every fact is embedded verbatim
        for f in facts:
            assert f in narrative


def test_template_heads_and_caveats_are_digit_free():
    # this is what makes the "numbers come from facts" guarantee possible
    for kind in ("statistics", "competitors", "compare", "research_summary"):
        assert not _numbers(trade_analyst._HEADS[kind])
        assert not _numbers(trade_analyst._CAVEATS[kind])


# ---------------------------------------------------------------------------
# pure: engine selection + Gemini validation
# ---------------------------------------------------------------------------

def test_narrative_without_key_is_labelled_template():
    facts = ["Total trade value: 250.00 USD"]
    text, engine, ai_gen, fallback = trade_analyst.narrative_for(
        "statistics", facts)
    assert engine == "deterministic_template"
    assert ai_gen is False
    assert fallback == "gemini_not_configured"
    assert "250.00" in text


def test_narrative_discards_gemini_output_with_invented_numbers(monkeypatch):
    monkeypatch.setattr(cfg.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        trade_analyst, "gemini_report",
        lambda kind, facts, api_key=None:
        "German imports totalled 999999999 USD last year.")
    facts = ["Total trade value: 250.00 USD"]
    text, engine, ai_gen, fallback = trade_analyst.narrative_for(
        "statistics", facts)
    assert engine == "deterministic_template"
    assert ai_gen is False
    assert fallback == "model_output_contained_unverified_numbers"
    assert "250.00" in text  # fell back to the honest template


def test_narrative_accepts_gemini_output_only_when_numbers_verified(monkeypatch):
    monkeypatch.setattr(cfg.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        trade_analyst, "gemini_report",
        lambda kind, facts, api_key=None:
        "Total trade value of 250.00 USD across the queried periods.")
    facts = ["Total trade value: 250.00 USD"]
    text, engine, ai_gen, fallback = trade_analyst.narrative_for(
        "statistics", facts)
    assert engine == "gemini"
    assert ai_gen is True
    assert fallback is None
    assert "250.00" in text


def test_narrative_falls_back_when_gemini_unavailable(monkeypatch):
    monkeypatch.setattr(cfg.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(trade_analyst, "gemini_report",
                        lambda kind, facts, api_key=None: None)
    facts = ["Total trade value: 250.00 USD"]
    text, engine, ai_gen, fallback = trade_analyst.narrative_for(
        "statistics", facts)
    assert engine == "deterministic_template"
    assert ai_gen is False
    assert fallback == "gemini_unavailable"
    assert "250.00" in text


def test_gemini_report_never_raises_without_key_or_library():
    # no key -> None; missing library -> None (never an exception)
    assert trade_analyst.gemini_report("statistics", ["x"], api_key="") is None


def test_gemini_report_is_bounded_by_a_timeout(monkeypatch):
    """A hung Gemini must not hang the analyst — the call is bounded so
    the caller can fall back to the deterministic template."""
    assert trade_analyst.GEMINI_TIMEOUT_S > 0
    seen: dict = {}

    class _Resp:
        text = "ok narrative 123"

    class _Model:
        def generate_content(self, prompt, request_options=None, **kw):
            seen["request_options"] = request_options
            return _Resp()

    class _Genai:
        @staticmethod
        def configure(api_key=None):
            seen["api_key"] = api_key

        @staticmethod
        def GenerativeModel(name):
            seen["model"] = name
            return _Model()

    import sys
    monkeypatch.setitem(sys.modules, "google.generativeai", _Genai)
    # `import google.generativeai as genai` resolves via the parent package
    # attribute when it already exists — patch both paths.
    parent = sys.modules.get("google")
    if parent is not None:
        monkeypatch.setattr(parent, "generativeai", _Genai, raising=False)
    out = trade_analyst.gemini_report(
        "statistics", ["Total: 250.00 USD"], api_key="k")
    assert out == "ok narrative 123"
    assert seen["api_key"] == "k"
    assert seen["model"] == "gemini-3.5-flash"
    assert seen["request_options"] == {
        "timeout": trade_analyst.GEMINI_TIMEOUT_S}


# ---------------------------------------------------------------------------
# pure: lead score explainability
# ---------------------------------------------------------------------------

def test_lead_score_factors_sum_exactly_to_score():
    out = trade_analyst.lead_score(
        evaluation={"buyer_fit_score": 80.0, "evidence_quality_score": 70.0},
        email_count=2, phone_count=1, evidence_count=3,
        company={"website": "https://x.example", "country": "India",
                 "category": "Importer"})
    assert out["engine"] == "deterministic_v1"
    assert out["ai_generated"] is False
    total = round(sum(f["points"] for f in out["factors"]), 1)
    assert total == out["score"], "factors must sum exactly to the score"
    assert sum(f["max"] for f in out["factors"]) == 100
    for f in out["factors"]:
        assert f["reason"], "every factor needs a human-readable reason"
        assert 0 <= f["points"] <= f["max"]
    # expected math: 80*0.4=32 + (2*5 + 1*5)=15 + 70*0.2=14 + 15 = 76
    assert out["score"] == 76.0
    assert out["band"] == "Hot"


def test_lead_score_without_research_is_honest_zero():
    out = trade_analyst.lead_score(
        evaluation=None, email_count=0, phone_count=0, evidence_count=0,
        company={})
    assert out["score"] == 0.0
    assert out["band"] == "Low"
    research = out["factors"][0]
    assert research["points"] == 0
    assert "No persisted research evaluation" in research["reason"]
    profile = out["factors"][3]
    assert profile["points"] == 0
    assert "missing" in profile["reason"]
    assert round(sum(f["points"] for f in out["factors"]), 1) == out["score"]


def test_lead_score_partial_profile_counts_only_present_fields():
    out = trade_analyst.lead_score(
        evaluation=None, email_count=0, phone_count=0, evidence_count=0,
        company={"country": "India"})
    profile = out["factors"][3]
    assert profile["points"] == 5  # country only
    assert "website missing" in profile["reason"]
    assert "present" in profile["reason"]


# ---------------------------------------------------------------------------
# endpoints: /ai/trade-analyst
# ---------------------------------------------------------------------------

def test_ai_trade_analyst_requires_auth():
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "statistics",
                          "params": {"reporter_code": 276}})
    assert r.status_code == 401


def test_ai_trade_analyst_statistics_template_path(fake):
    tok = _user()
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "statistics",
                          "params": {"reporter_code": 276,
                                     "period_from": "2022",
                                     "period_to": "2023", "flow": "M"}},
                    headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "ai_analysis"
    assert body["kind"] == "statistics"
    assert body["engine"] == "deterministic_template"
    assert body["ai_generated"] is False
    assert body["fallback_reason"] == "gemini_not_configured"
    assert body["facts"] and body["narrative"]
    # the narrative cannot contain a number that is not in the facts
    assert _numbers(body["narrative"]) <= _numbers(" ".join(body["facts"]))
    # attribution travels with the payload
    assert body["source"]["name"] == "UN Comtrade"
    assert "NOT individual shipment records" in body["source"]["disclaimer"]
    assert body["data"]["record_type"] == "aggregated_statistics"
    assert "cannot add figures" in body["note"]


def test_ai_trade_analyst_competitors_path(fake):
    tok = _user()
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "competitors",
                          "params": {"destination_code": 276,
                                     "cmd_code": "7113", "period": "2023"}},
                    headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    joined_facts = " ".join(body["facts"])
    assert "Japan" in joined_facts and "Germany" in joined_facts
    assert "does not identify individual companies" in joined_facts
    assert _numbers(body["narrative"]) <= _numbers(joined_facts)
    assert body["source"]["destination_name"] == "Germany"


def test_ai_trade_analyst_compare_path(fake):
    tok = _user()
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "compare",
                          "params": {"reporter_codes": [276, 826],
                                     "period_from": "2022",
                                     "period_to": "2023", "flow": "M"}},
                    headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    joined_facts = " ".join(body["facts"])
    assert "Germany (276)" in joined_facts
    assert "United Kingdom (826)" in joined_facts
    assert "not world trade" in joined_facts
    assert _numbers(body["narrative"]) <= _numbers(joined_facts)


def test_ai_trade_analyst_validation_is_typed_422(fake):
    tok = _user()
    h = _auth(tok)
    # unknown kind -> pydantic 422
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "telepathy", "params": {}}, headers=h)
    assert r.status_code == 422
    # missing reporter_code -> honest typed 422 (never a fake empty result)
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "statistics", "params": {}}, headers=h)
    assert r.status_code == 422
    assert "reporter_code is required" in r.json()["detail"]
    # non-numeric reporter code
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "statistics",
                          "params": {"reporter_code": "abc"}}, headers=h)
    assert r.status_code == 422
    # bad HS code propagates the trade layer's typed message
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "competitors",
                          "params": {"destination_code": 276,
                                     "cmd_code": "gold"}}, headers=h)
    assert r.status_code == 422
    assert "HS code" in r.json()["detail"]
    # compare with fewer than 2 codes
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "compare",
                          "params": {"reporter_codes": [276]}}, headers=h)
    assert r.status_code == 422
    # top_n bounds
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "competitors",
                          "params": {"destination_code": 276,
                                     "top_n": 500}}, headers=h)
    assert r.status_code == 422
    assert "top_n" in r.json()["detail"]


def test_ai_trade_analyst_quota_is_typed_429_never_fake_empty(monkeypatch):
    tok = _user()
    f = FakeTrade(fail=tp.TradeQuotaError(
        "UN Comtrade rate limit reached (HTTP 429)"))
    monkeypatch.setattr(api_trade, "get_trade_provider", lambda: f)
    r = client.post("/api/ai/trade-analyst",
                    json={"kind": "statistics",
                          "params": {"reporter_code": 276}},
                    headers=_auth(tok))
    assert r.status_code == 429
    assert "429" in r.json()["detail"]
    assert "0 results" not in r.json()["detail"].lower()


def test_ai_trade_analyst_rate_limited(fake):
    tok = _user()
    h = _auth(tok)
    per_min = cfg.settings.RATE_SENSITIVE_PER_MIN
    codes = []
    for _ in range(per_min + 3):
        r = client.post("/api/ai/trade-analyst",
                        json={"kind": "statistics",
                              "params": {"reporter_code": 276}},
                        headers=h)
        codes.append(r.status_code)
    assert codes[0] == 200
    assert 429 in codes, f"expected a 429 within {per_min + 3} calls"


# ---------------------------------------------------------------------------
# endpoints: /ai/lead-score
# ---------------------------------------------------------------------------

def test_ai_lead_score_requires_auth():
    r = client.post("/api/ai/lead-score", json={"company_id": 1})
    assert r.status_code == 401


def test_ai_lead_score_explainable_from_persisted_data():
    tok = _user()
    uid = _find_user_id(tok)
    cid = _mk_company(uid, "Scored Co", website="https://scored.example",
                      country="India", category="Importer")
    _mk_contact(uid, cid, "EMAIL", "s@example.com")
    _mk_contact(uid, cid, "PHONE", "+15550100")
    _mk_evidence(uid, cid)
    _mk_evaluation(uid, cid, buyer_fit=80.0, evidence=70.0)
    r = client.post("/api/ai/lead-score", json={"company_id": cid},
                    headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "lead_score"
    assert body["engine"] == "deterministic_v1"
    assert body["ai_generated"] is False
    total = round(sum(f["points"] for f in body["factors"]), 1)
    assert total == body["score"]
    assert sum(f["max"] for f in body["factors"]) == 100
    for f in body["factors"]:
        assert f["reason"]
    # inputs are disclosed so the score is auditable
    assert body["inputs"]["email_count"] == 1
    assert body["inputs"]["phone_count"] == 1
    assert body["inputs"]["evidence_count"] == 1
    assert body["inputs"]["has_evaluation"] is True
    assert body["inputs"]["buyer_fit_score"] == 80.0
    assert body["company"]["name"] == "Scored Co"
    assert "Directory presence alone never proves" in body["note"]


def test_ai_lead_score_without_research_still_scores_honestly():
    tok = _user()
    uid = _find_user_id(tok)
    cid = _mk_company(uid, "Bare Co")
    r = client.post("/api/ai/lead-score", json={"company_id": cid},
                    headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inputs"]["has_evaluation"] is False
    assert body["factors"][0]["points"] == 0
    assert "No persisted research evaluation" in body["factors"][0]["reason"]
    assert body["score"] == round(
        sum(f["points"] for f in body["factors"]), 1)


def test_ai_lead_score_is_tenant_scoped():
    tok_a = _user()
    tok_b = _user()
    uid_a = _find_user_id(tok_a)
    cid = _mk_company(uid_a, "Private Co")
    # owner sees it
    r = client.post("/api/ai/lead-score", json={"company_id": cid},
                    headers=_auth(tok_a))
    assert r.status_code == 200
    # another tenant gets an honest 404 (no data leak)
    r = client.post("/api/ai/lead-score", json={"company_id": cid},
                    headers=_auth(tok_b))
    assert r.status_code == 404
    # missing company
    r = client.post("/api/ai/lead-score", json={"company_id": 99999999},
                    headers=_auth(tok_a))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# endpoints: /ai/research-summary/{id}
# ---------------------------------------------------------------------------

def test_ai_research_summary_requires_auth():
    r = client.post("/api/ai/research-summary/1")
    assert r.status_code == 401


def test_ai_research_summary_reads_persisted_rows_only():
    tok = _user()
    uid = _find_user_id(tok)
    cid = _mk_company(uid, "Summary Co", country="India",
                      category="Importer")
    _mk_contact(uid, cid, "EMAIL", "sum@example.com")
    _mk_evidence(uid, cid)
    _mk_evaluation(uid, cid, buyer_fit=80.0)
    r = client.post(f"/api/ai/research-summary/{cid}", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["record_type"] == "ai_analysis"
    assert body["kind"] == "research_summary"
    assert body["engine"] == "deterministic_template"
    assert body["ai_generated"] is False
    joined_facts = " ".join(body["facts"])
    assert "Summary Co" in joined_facts
    assert "buyer fit 80" in joined_facts
    assert "1 email(s)" in joined_facts
    # narrative can only restate the facts
    assert _numbers(body["narrative"]) <= _numbers(joined_facts)
    assert "no live calls" in body["note"]
    assert body["company"]["id"] == cid


def test_ai_research_summary_without_evaluation_is_honest():
    tok = _user()
    uid = _find_user_id(tok)
    cid = _mk_company(uid, "Unresearched Co")
    r = client.post(f"/api/ai/research-summary/{cid}", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "No persisted research evaluation yet" in " ".join(body["facts"])


def test_ai_research_summary_is_tenant_scoped():
    tok_a = _user()
    tok_b = _user()
    uid_a = _find_user_id(tok_a)
    cid = _mk_company(uid_a, "Secret Co")
    assert client.post(f"/api/ai/research-summary/{cid}",
                       headers=_auth(tok_b)).status_code == 404
    assert client.post(f"/api/ai/research-summary/{cid}",
                       headers=_auth(tok_a)).status_code == 200


def test_ai_research_summary_rate_limited():
    tok = _user()
    uid = _find_user_id(tok)
    cid = _mk_company(uid, "Rate Summary Co")
    h = _auth(tok)
    per_min = cfg.settings.RATE_SENSITIVE_PER_MIN
    codes = []
    for _ in range(per_min + 3):
        r = client.post(f"/api/ai/research-summary/{cid}", headers=h)
        codes.append(r.status_code)
    assert codes[0] == 200
    assert 429 in codes, f"expected a 429 within {per_min + 3} calls"
