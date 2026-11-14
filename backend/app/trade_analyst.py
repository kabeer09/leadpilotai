"""Phase 5: AI Trade Analyst — deterministic analysis + labelled AI narratives.

Honesty contract (master prompt, AI section):
- Every narrative is assembled from FACTS computed from real payloads
  (UN Comtrade statistics / persisted research rows). The deterministic
  template only restates those computed numbers — it can never invent a
  figure, a company, a shipment or a source.
- When a Gemini key is configured the model is asked for a narrative
  over the SAME facts; its output is validated number-by-number against
  the facts and DISCARDED (falling back to the template, with an honest
  fallback_reason) if it contains any figure that is not in the facts.
- Every response carries: engine (gemini | deterministic_template),
  ai_generated (bool), fallback_reason (why the template was used) and
  the full facts list, so the user can audit the narrative line by line.
- Lead scoring is fully deterministic (engine=deterministic_v1): each
  factor carries points, max and a human-readable reason; the factors
  sum exactly to the score. No LLM anywhere in the scoring path.
"""
from __future__ import annotations

import os
import re

from app.config import settings

# Numbers as they appear in text: "1,234.56", "60.0", "2023", "-5"
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# A slow/hung Gemini must never hang the analyst: bounded wait, then the
# deterministic template (honest fallback_reason=gemini_unavailable).
GEMINI_TIMEOUT_S = 20


def numbers_in(text: str | None) -> set[float]:
    """All numeric values mentioned in a text (commas normalised)."""
    out: set[float] = set()
    for m in _NUM_RE.findall(str(text or "")):
        try:
            out.add(float(m.replace(",", "")))
        except ValueError:  # pragma: no cover - regex can't hit this
            continue
    return out


def _flow_label(flow: str | None) -> str:
    return {"M": "Imports", "X": "Exports", "both": "Imports + exports"}.get(
        str(flow or ""), str(flow or "—"))


def _fmt(v) -> str:
    """Money formatting used by BOTH facts and narrative (same string)."""
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _hs(cmd_code) -> str:
    return str(cmd_code) if cmd_code else "all products"


# ---------------------------------------------------------------------------
# fact builders — structured, computed, auditable
# ---------------------------------------------------------------------------

def statistics_facts(payload: dict) -> list[str]:
    """Facts from a /trade/statistics payload (aggregated series)."""
    p = payload.get("params") or {}
    series = payload.get("series") or []
    facts = [
        (f"Query: reporter {p.get('reporter_code')}, "
         f"{p.get('period_from')}-{p.get('period_to')}, flow {p.get('flow')}, "
         f"HS {_hs(p.get('cmd_code'))}"),
        (f"Series points: {len(series)}; periods covered: "
         f"{', '.join(sorted({str(s.get('period')) for s in series if s.get('period')})) or 'none'}"),
        f"Total trade value: {_fmt(payload.get('total_value'))} USD",
    ]
    for fl in ("M", "X"):
        vals = [s for s in series if s.get("flow") == fl]
        if vals:
            total = sum(float(s.get("value") or 0) for s in vals)
            facts.append(f"{_flow_label(fl)} total: {_fmt(total)} USD "
                         f"across {len(vals)} period(s)")
    if series:
        peak = max(series, key=lambda s: float(s.get("value") or 0))
        facts.append(f"Highest period: {peak.get('period')} "
                     f"{peak.get('flow')} at {_fmt(peak.get('value'))} USD")
    if payload.get("stale"):
        facts.append(f"Data is a stale cache (reason: "
                     f"{payload.get('stale_reason') or 'provider unavailable'})")
    return facts


def competitors_facts(payload: dict) -> list[str]:
    """Facts from a /trade/competitors payload (supplier countries)."""
    p = payload.get("params") or {}
    src = payload.get("source") or {}
    dest = src.get("destination_name") or p.get("destination_code")
    facts = [
        (f"Destination market: {dest} (reporter {p.get('destination_code')}), "
         f"period {p.get('period')}, flow M, HS {_hs(p.get('cmd_code'))}, "
         f"top {p.get('top_n')} suppliers"),
        (f"Total import value: {_fmt(payload.get('total_value'))} USD from "
         f"{payload.get('partners_count')} partner countries"),
    ]
    for s in (payload.get("suppliers") or [])[:5]:
        facts.append(
            f"Rank {s.get('rank')} {s.get('partner_desc') or s.get('partner_code')}: "
            f"{_fmt(s.get('value'))} USD ({s.get('share_pct')}% share, "
            f"{s.get('records')} records)")
    if payload.get("dropped_world"):
        facts.append("World aggregate row excluded (double-count guard)")
    if payload.get("truncated"):
        facts.append("Partner ranking truncated by the top-N limit")
    facts.append("Country-level statistics only — trade data does not "
                 "identify individual companies")
    return facts


def compare_facts(payload: dict) -> list[str]:
    """Facts from a /trade/compare payload (country matrix)."""
    p = payload.get("params") or {}
    countries = payload.get("countries") or []
    names = [f"{c.get('name')} ({c.get('reporter_code')})" for c in countries]
    facts = [
        f"Countries compared: {', '.join(names) or 'none'}",
        (f"Period {p.get('period_from')}-{p.get('period_to')}, "
         f"flow {p.get('flow')}, HS {_hs(p.get('cmd_code'))}"),
    ]
    for c in countries:
        facts.append(f"{c.get('name')} total: "
                     f"{_fmt(c.get('total_value'))} USD")
    if countries:
        top = max(countries, key=lambda c: float(c.get("total_value") or 0))
        facts.append(f"Largest total: {top.get('name')} with "
                     f"{_fmt(top.get('total_value'))} USD")
    matrix = payload.get("matrix") or []
    for row in sorted(matrix, key=lambda r: -float(r.get("combined") or 0))[:5]:
        values = row.get("values") or {}
        by_code = {c.get("reporter_code"): c.get("name") for c in countries}
        parts = ", ".join(
            f"{by_code.get(int(rc), rc)} {_fmt(v)} USD"
            for rc, v in sorted(values.items(),
                                key=lambda kv: -float(kv[1] or 0)))
        facts.append(f"{row.get('period')} {_flow_label(row.get('flow'))}: "
                     f"combined {_fmt(row.get('combined'))} USD — {parts}")
    facts.append("Shares are of the compared set only — not world trade; "
                 "aggregated statistics, not company data")
    return facts


def research_facts(report: dict) -> list[str]:
    """Facts from a persisted research report payload (DB-only, no network)."""
    c = report.get("company") or {}
    contacts = report.get("contacts") or []
    evidence = report.get("evidence") or []
    ev = report.get("evaluation")
    loc = ", ".join(x for x in [c.get("city"), c.get("country")] if x) \
        or "location not recorded"
    emails = sum(1 for x in contacts
                 if str(x.get("kind") or "").upper() == "EMAIL")
    phones = sum(1 for x in contacts
                 if str(x.get("kind") or "").upper() in ("PHONE", "WHATSAPP"))
    facts = [
        f"Company: {c.get('name')} — {loc}",
        f"Category: {c.get('category') or 'not recorded'}",
        (f"Saved contacts: {emails} email(s), {phones} phone(s); "
         f"evidence rows: {len(evidence)}"),
    ]
    if ev:
        scores = [(label, ev.get(key)) for label, key in (
            ("buyer fit", "buyer_fit_score"),
            ("overall", "overall_score"),
            ("contact completeness", "contact_completeness_score"),
            ("evidence quality", "evidence_quality_score"),
            ("validation coverage", "validation_coverage"),
        ) if ev.get(key) is not None]
        if scores:
            facts.append("Scores (0-100): "
                         + "; ".join(f"{lbl} {val:g}" for lbl, val in scores))
        else:
            facts.append("Scores: none recorded in the evaluation")
        if ev.get("buyer_type"):
            facts.append(f"Buyer type: {ev.get('buyer_type')}; "
                         f"product fit: {ev.get('product_fit')}; "
                         f"country fit: {ev.get('country_fit')}")
        if ev.get("recommended_next_action"):
            facts.append(f"Recommended next action: "
                         f"{ev.get('recommended_next_action')}")
        missing = ev.get("missing_evidence") or []
        if missing:
            facts.append("Missing evidence: "
                         + ", ".join(str(m) for m in missing[:6]))
        if ev.get("evaluated_at"):
            facts.append(f"Evaluated at: {str(ev['evaluated_at'])[:10]} "
                         f"(persisted run)")
    else:
        facts.append("No persisted research evaluation yet — run live "
                     "research to generate scores")
    return facts


def build_facts(kind: str, payload: dict) -> list[str]:
    if kind == "statistics":
        return statistics_facts(payload)
    if kind == "competitors":
        return competitors_facts(payload)
    if kind == "compare":
        return compare_facts(payload)
    if kind == "research_summary":
        return research_facts(payload)
    raise ValueError(f"unknown analyst kind: {kind}")


# ---------------------------------------------------------------------------
# narrative: deterministic template (default) / Gemini (validated)
# ---------------------------------------------------------------------------

_HEADS = {
    "statistics": "Trade statistics analysis (computed from UN Comtrade "
                  "aggregated data):",
    "competitors": "Supplier-market analysis (computed from UN Comtrade "
                   "aggregated data):",
    "compare": "Country comparison (computed from UN Comtrade aggregated "
               "data):",
    "research_summary": "Research summary (computed from persisted LeadPilot "
                        "data):",
}

_CAVEATS = {
    "statistics": "Aggregated statistics — not shipment records and not "
                  "buyer or supplier identities.",
    "competitors": "Country-level statistics only — trade data does not "
                   "identify individual companies or buyers.",
    "compare": "Shares are shares of the compared set, not of world trade.",
    "research_summary": "Built from persisted research rows — no live calls "
                        "and no invented facts.",
}


def template_report(kind: str, facts: list[str]) -> str:
    """Deterministic narrative: embeds ONLY the computed fact strings.

    Heads and caveats are deliberately digit-free, so every number in the
    output provably comes from a fact (enforced by tests).
    """
    head = _HEADS.get(kind, "Analysis (computed from stored data):")
    body = ". ".join(f for f in facts if f)
    caveat = _CAVEATS.get(kind, "")
    return f"{head} {body}. {caveat}".strip()


def gemini_report(kind: str, facts: list[str],
                  api_key: str | None = None) -> str | None:
    """Ask Gemini for a narrative over the given facts only.

    Returns None when unavailable (no key / library missing / call failed)
    — the caller then falls back to the deterministic template. Never
    raises: an AI outage must not break the analyst.
    """
    key = api_key or settings.GEMINI_API_KEY \
        or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-3.5-flash")
        prompt = (
            "You write a short trade-analysis narrative.\n"
            "STRICT RULES: Use ONLY the facts listed below. Every number in "
            "your answer must appear verbatim in the facts. Do not add "
            "numbers, dates, percentages or quantities of your own. Do not "
            "invent companies, shipments, buyers or sources. 3-5 sentences.\n\n"
            "FACTS:\n- " + "\n- ".join(facts))
        response = model.generate_content(
            prompt, request_options={"timeout": GEMINI_TIMEOUT_S})
        text = (getattr(response, "text", "") or "").strip()
        return text or None
    except Exception:
        return None


def narrative_for(kind: str, facts: list[str]
                  ) -> tuple[str, str, bool, str | None]:
    """Pick the narrative engine honestly.

    Returns (narrative, engine, ai_generated, fallback_reason).
    Gemini output containing ANY number that is not in the facts is
    discarded and the template is served instead.
    """
    key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return (template_report(kind, facts), "deterministic_template",
                False, "gemini_not_configured")
    try:
        text = gemini_report(kind, facts)
    except Exception:
        text = None
    if not text:
        return (template_report(kind, facts), "deterministic_template",
                False, "gemini_unavailable")
    invented = numbers_in(text) - numbers_in(" ".join(facts))
    if invented:
        return (template_report(kind, facts), "deterministic_template",
                False, "model_output_contained_unverified_numbers")
    return text, "gemini", True, None


# ---------------------------------------------------------------------------
# lead scoring — deterministic, explainable, factors sum to the score
# ---------------------------------------------------------------------------

def lead_score(evaluation: dict | None, email_count: int,
               phone_count: int, evidence_count: int,
               company: dict) -> dict:
    """Composite lead score (0-100) from persisted data only.

    Weights: research buyer fit 40 | reachability 25 | evidence depth 20 |
    profile completeness 15. Every factor carries points/max/reason and
    the factors sum exactly to `score`. No LLM, no live calls, no guesses:
    missing inputs score 0 with an honest reason.
    """
    factors: list[dict] = []

    # 1. research buyer fit (0-40)
    if evaluation and evaluation.get("buyer_fit_score") is not None:
        bf = float(evaluation["buyer_fit_score"])
        pts = round(bf * 0.40, 1)
        reason = (f"Research buyer-fit {bf:g}/100 x 40% weight "
                  f"= {pts} points")
    else:
        pts = 0.0
        reason = ("No persisted research evaluation — run live research "
                  "first (0/40)")
    factors.append({"key": "research_buyer_fit", "label": "Research buyer fit",
                    "points": pts, "max": 40, "reason": reason})

    # 2. reachability (0-25): saved contacts with sources
    ep = min(int(email_count or 0), 3) * 5
    pp = min(int(phone_count or 0), 2) * 5
    factors.append({
        "key": "reachability", "label": "Reachability (saved contacts)",
        "points": ep + pp, "max": 25,
        "reason": (f"{email_count} saved email contact(s) -> +{ep}/15; "
                   f"{phone_count} saved phone contact(s) -> +{pp}/10")})

    # 3. evidence depth (0-20)
    if evaluation and evaluation.get("evidence_quality_score") is not None:
        eq = float(evaluation["evidence_quality_score"])
        pts = round(eq * 0.20, 1)
        reason = (f"Evidence-quality score {eq:g}/100 x 20% weight "
                  f"= {pts} points")
    else:
        pts = float(min(int(evidence_count or 0), 4) * 5)
        reason = (f"{evidence_count} evidence row(s) with source URLs "
                  f"(max 4 counted) -> {pts:g}/20")
    factors.append({"key": "evidence_depth", "label": "Evidence depth",
                    "points": pts, "max": 20, "reason": reason})

    # 4. profile completeness (0-15)
    has_site = bool(company.get("website"))
    has_country = bool(company.get("country"))
    has_cat = bool(company.get("category") or company.get("product_summary"))
    pts = (5 if has_site else 0) + (5 if has_country else 0) + \
          (5 if has_cat else 0)
    factors.append({
        "key": "profile_completeness", "label": "Profile completeness",
        "points": pts, "max": 15,
        "reason": (f"website {'present' if has_site else 'missing'} +5/5; "
                   f"country {'present' if has_country else 'missing'} +5/5; "
                   f"category/product summary "
                   f"{'present' if has_cat else 'missing'} +5/5")})

    total = round(sum(f["points"] for f in factors), 1)
    band = ("Hot" if total >= 70 else "Warm" if total >= 45
            else "Nurture" if total >= 25 else "Low")
    return {
        "score": total,
        "max": 100,
        "band": band,
        "factors": factors,
        "engine": "deterministic_v1",
        "ai_generated": False,
        "note": ("Explainable composite of persisted data — every point has "
                 "a reason; no live calls, no invented facts. Directory "
                 "presence alone never proves active buying intent."),
    }
