"""Phase 5 AI endpoints — Trade Analyst, lead scoring, research summaries.

Contracts (master prompt, AI section):
- Narratives are assembled ONLY from computed facts (the payload's own
  numbers) and are labelled with engine + ai_generated; the Gemini path
  validates every number against those facts and falls back to the
  deterministic template (honest fallback_reason) when the model is
  unavailable or invents a figure.
- Lead scoring is a deterministic explainable composite: factors carry
  points/max/reason and sum exactly to the score (engine=deterministic_v1).
- Research summaries read the PERSISTED report only — zero network.
- Tenant-scoped on every endpoint; rate-limited; typed validation errors
  (422) reuse the trade layer's honest messages — never a fake empty.
"""
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import api_research, api_trade, trade_analyst
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, rate_limit
from app.models import Company, Contact, User
from app.models_research import ContactEvidence

router = APIRouter(prefix="/api", tags=["ai"])

_NOTE = ("Narrative restates only the facts listed below — it cannot add "
         "figures, companies or sources that are not in those facts.")


# ---------------------------------------------------------------------------
# POST /ai/trade-analyst
# ---------------------------------------------------------------------------

class AnalystIn(BaseModel):
    kind: Literal["statistics", "competitors", "compare"]
    params: dict[str, Any] = Field(default_factory=dict)


def _int_param(p: dict, key: str) -> int:
    v = p.get(key)
    if v is None or v == "":
        raise HTTPException(status_code=422,
                            detail=f"{key} is required for this analysis")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"{key} must be an integer UN Comtrade code")


def _opt_int(p: dict, key: str) -> int | None:
    v = p.get(key)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"{key} must be an integer UN Comtrade code")


@router.post("/ai/trade-analyst",
             dependencies=[Depends(rate_limit(
                 "ai_analyst", settings.RATE_SENSITIVE_PER_MIN))])
def ai_trade_analyst(payload: AnalystIn,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """AI narrative over a trade payload (statistics | competitors | compare).

    Reuses the trade endpoints' fetch/cache/validation path, then builds
    auditable facts from the payload and a labelled narrative over them.
    """
    kind = payload.kind
    p = payload.params or {}

    if kind == "statistics":
        data = api_trade.trade_statistics(
            reporter_code=_int_param(p, "reporter_code"),
            period_from=str(p.get("period_from") or "2019"),
            period_to=str(p.get("period_to") or "2023"),
            flow=str(p.get("flow") or "both"),
            cmd_code=(str(p["cmd_code"]) if p.get("cmd_code") else None),
            partner_code=_opt_int(p, "partner_code"),
            db=db, user=user)
    elif kind == "competitors":
        top_n = _opt_int(p, "top_n") or 15
        if not 1 <= top_n <= 100:
            raise HTTPException(status_code=422,
                                detail="top_n must be between 1 and 100")
        data = api_trade.trade_competitors(
            destination_code=_int_param(p, "destination_code"),
            cmd_code=(str(p["cmd_code"]) if p.get("cmd_code") else None),
            period=str(p.get("period") or "2023"),
            top_n=top_n, db=db, user=user)
    else:  # compare
        raw = p.get("reporter_codes")
        if isinstance(raw, (list, tuple)):
            codes = ",".join(str(c) for c in raw)
        elif raw:
            codes = str(raw)
        else:
            raise HTTPException(
                status_code=422,
                detail="reporter_codes is required (2-4 UN reporter codes)")
        data = api_trade.trade_compare(
            reporter_codes=codes,
            period_from=str(p.get("period_from") or "2022"),
            period_to=str(p.get("period_to") or "2023"),
            flow=str(p.get("flow") or "both"),
            cmd_code=(str(p["cmd_code"]) if p.get("cmd_code") else None),
            db=db, user=user)

    facts = trade_analyst.build_facts(kind, data)
    narrative, engine, ai_generated, fallback = trade_analyst.narrative_for(
        kind, facts)
    return {
        "record_type": "ai_analysis",
        "kind": kind,
        "engine": engine,
        "ai_generated": ai_generated,
        "fallback_reason": fallback,
        "facts": facts,
        "narrative": narrative,
        "source": data.get("source"),
        "params": data.get("params"),
        "stale": bool(data.get("stale")),
        "cached": bool(data.get("cached")),
        "data": data,
        "note": _NOTE,
    }


# ---------------------------------------------------------------------------
# POST /ai/lead-score
# ---------------------------------------------------------------------------

class LeadScoreIn(BaseModel):
    company_id: int


@router.post("/ai/lead-score",
             dependencies=[Depends(rate_limit(
                 "ai_lead_score", settings.RATE_SENSITIVE_PER_MIN))])
def ai_lead_score(payload: LeadScoreIn,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Explainable 0-100 lead score from persisted data (no live calls).

    Factors sum exactly to the score; each carries points/max/reason so
    the user can audit every point. Missing inputs score 0 with an honest
    reason — never a guessed number.
    """
    company = (db.query(Company)
               .filter(Company.id == payload.company_id,
                       Company.user_id == user.id).first())
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    evals = api_research._latest_evaluations(db, user.id, [company.id])
    ev_row = evals.get(company.id)
    evaluation = api_research._eval_out(ev_row) if ev_row else None

    emails = (db.query(func.count(Contact.id))
              .filter(Contact.company_id == company.id,
                      Contact.kind == "EMAIL").scalar() or 0)
    phones = (db.query(func.count(Contact.id))
              .filter(Contact.company_id == company.id,
                      Contact.kind.in_(("PHONE", "WHATSAPP"))).scalar() or 0)
    evidence = (db.query(func.count(ContactEvidence.id))
                .filter(ContactEvidence.user_id == user.id,
                        ContactEvidence.company_id == company.id)
                .scalar() or 0)

    score = trade_analyst.lead_score(
        evaluation, emails, phones, evidence,
        {"website": company.website, "country": company.country,
         "category": company.category,
         "product_summary": company.product_summary})
    return {
        "record_type": "lead_score",
        "company": {"id": company.id, "name": company.name},
        **score,
        "inputs": {
            "email_count": emails,
            "phone_count": phones,
            "evidence_count": evidence,
            "has_evaluation": evaluation is not None,
            "buyer_fit_score": (evaluation.get("buyer_fit_score")
                                if evaluation else None),
            "evaluated_at": (ev_row.evaluated_at.isoformat()
                             if ev_row and ev_row.evaluated_at else None),
        },
    }


# ---------------------------------------------------------------------------
# POST /ai/research-summary/{company_id}
# ---------------------------------------------------------------------------

@router.post("/ai/research-summary/{company_id}",
             dependencies=[Depends(rate_limit(
                 "ai_research_summary", settings.RATE_SENSITIVE_PER_MIN))])
def ai_research_summary(company_id: int,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Labelled narrative over a company's PERSISTED research report.

    Reads the stored report only (contacts, evidence, scores from real
    past runs) — zero network, zero invented facts.
    """
    company = (db.query(Company)
               .filter(Company.id == company_id,
                       Company.user_id == user.id).first())
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    report = api_research._report_payload(db, user, company)  # DB-only
    facts = trade_analyst.build_facts("research_summary", report)
    narrative, engine, ai_generated, fallback = trade_analyst.narrative_for(
        "research_summary", facts)
    return {
        "record_type": "ai_analysis",
        "kind": "research_summary",
        "engine": engine,
        "ai_generated": ai_generated,
        "fallback_reason": fallback,
        "facts": facts,
        "narrative": narrative,
        "company": {"id": company.id, "name": company.name},
        "note": ("Built from persisted research rows only (contacts, "
                 "evidence and scores from real runs) — no live calls."),
    }
