"""Multi-agent research and qualification API.

Phase 3 additions (extend, never replace):
  GET  /research/companies        company picker for the research UI
  GET  /research/report/{id}      durable research report (DB-only)
  POST /research/report/{id}/refresh  re-run live website research
Existing endpoints keep their paths; single-company research and the
pipeline now persist evidence (ContactEvidence) + evaluations
(EvaluationResult) so results are durable and auditable.
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Company, Contact, Source
from app.models_research import ContactEvidence, EvaluationResult
from app.deps import get_current_user, audit, rate_limit
from app.config import settings
from app.agents import (run_research_pipeline, research_and_persist,
                        research_company_website, qualify_buyer,
                        generate_outreach_draft)

router = APIRouter(prefix="/api", tags=["research"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _latest_evaluations(db: Session, user_id: int,
                        company_ids: list[int]) -> dict[int, EvaluationResult]:
    """Latest evaluation row per company (rows are append-per-run)."""
    out: dict[int, EvaluationResult] = {}
    if not company_ids:
        return out
    rows = (db.query(EvaluationResult)
            .filter(EvaluationResult.user_id == user_id,
                    EvaluationResult.company_id.in_(company_ids))
            .order_by(EvaluationResult.id.desc()).all())
    for r in rows:
        out.setdefault(r.company_id, r)
    return out


def _eval_out(e: EvaluationResult | None) -> dict | None:
    if e is None:
        return None
    try:
        result = json.loads(e.result_json or "{}")
    except Exception:
        result = {}
    return {
        "buyer_fit_score": e.buyer_fit_score,
        "overall_score": e.overall_score,
        "contact_completeness_score": e.contact_completeness_score,
        "evidence_quality_score": e.evidence_quality_score,
        "validation_coverage": e.validation_coverage,
        "buyer_type": result.get("buyer_type"),
        "product_fit": result.get("product_fit"),
        "country_fit": result.get("country_fit"),
        "recommended_next_action": result.get("recommended_next_action"),
        "missing_evidence": result.get("missing_evidence", []),
        "supporting_snippets": result.get("supporting_snippets", []),
        "emails": result.get("emails", []),
        "phones": result.get("phones", []),
        "qc_issues": result.get("qc_issues", []),
        "issues": _jloads(e.issues), "warnings": _jloads(e.warnings),
        "evaluated_at": e.evaluated_at,
    }


def _jloads(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else [])
    except Exception:
        return default if default is not None else []


def _company_profile(c: Company, smap: dict[int, Source]) -> dict:
    try:
        products = json.loads(c.products_json or "[]")
    except Exception:
        products = []
    src = smap.get(c.source_id) if c.source_id else None
    return {
        "id": c.id, "name": c.name, "website": c.website,
        "country": c.country, "city": c.city, "category": c.category,
        "hall": c.hall, "booth": c.booth, "address": c.address,
        "product_summary": c.product_summary,
        "products": products if isinstance(products, list) else [],
        "profile_url": c.profile_url, "source_id": c.source_id,
        "fair": src.name if src else None,
        "lead_status": c.lead_status, "confidence": c.confidence,
        "first_seen": c.first_seen, "last_verified": c.last_verified,
    }


def _report_payload(db: Session, user: User, company: Company,
                    research: dict | None = None,
                    refreshed: bool = False) -> dict:
    smap = {s.id: s for s in db.query(Source).filter(
        Source.user_id == user.id).all()}
    contacts = (db.query(Contact)
                .filter(Contact.company_id == company.id)
                .order_by(Contact.id).all())
    evidence = (db.query(ContactEvidence)
                .filter(ContactEvidence.user_id == user.id,
                        ContactEvidence.company_id == company.id)
                .order_by(ContactEvidence.id.desc()).limit(200).all())
    evals = _latest_evaluations(db, user.id, [company.id])
    evaluation = _eval_out(evals.get(company.id))
    return {
        "record_type": "company_research_report",
        "company": _company_profile(company, smap),
        "contacts": [{
            "id": x.id, "kind": x.kind, "value": x.value,
            "source_url": x.source_url, "source_page": x.source_page,
            "is_primary": x.is_primary, "verification": x.verification,
            "whatsapp_status": x.whatsapp_status} for x in contacts],
        "evidence": [{
            "id": ev.id, "contact_type": ev.contact_type,
            "raw_value": ev.raw_value, "source_url": ev.source_url,
            "source_type": ev.source_type,
            "extraction_method": ev.extraction_method,
            "confidence": ev.confidence,
            "validation_status": ev.validation_status,
            "first_observed": ev.first_observed,
            "last_observed": ev.last_observed} for ev in evidence],
        "evaluation": evaluation,
        "research": research,   # only present after a live refresh
        "has_research": bool(evaluation or evidence),
        "refreshed_at": datetime.utcnow().isoformat() if refreshed else None,
        "note": ("Scores and contacts are persisted from real website "
                 "research runs — never guessed. POST .../refresh to "
                 "re-run live research (rate-limited)."),
    }


# ---------------------------------------------------------------------------
# existing: run pipeline
# ---------------------------------------------------------------------------

@router.post("/research/run")
def run_research(
    payload: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run the research pipeline on selected companies."""
    company_ids = payload.get("company_ids", [])
    if not company_ids:
        # Default: research all companies without website research
        companies = db.query(Company).filter(
            Company.user_id == user.id).limit(50).all()
        company_ids = [c.id for c in companies]

    if not company_ids:
        raise HTTPException(status_code=404, detail="No companies to research")

    # Run synchronously (for now; can be made async later)
    result = run_research_pipeline(db, user.id, company_ids)
    audit(db, user.id, "research_run", "research", None,
          f"{result['companies_processed']} processed")
    db.commit()

    return result


# ---------------------------------------------------------------------------
# existing: single company research (now persists evidence + evaluation)
# ---------------------------------------------------------------------------

@router.post("/research/company/{company_id}")
def research_single_company(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Research a single company's website (persists evidence + scores)."""
    company = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    outcome = research_and_persist(db, user.id, company)
    db.commit()
    return {
        "company_id": company.id,
        "company_name": company.name,
        "research": outcome["research"],
        "qualification": outcome["qualification"],
        "quality_check": outcome["qc"],
        "evidence_saved": outcome["evidence"],
    }


# ---------------------------------------------------------------------------
# existing: qualified list — now a fast path over persisted evaluations
# ---------------------------------------------------------------------------

@router.get("/research/qualified")
def list_qualified(
    min_score: int = 40,
    refresh: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Companies with qualification scores above threshold.

    Default: reads persisted EvaluationResult rows (instant, no network).
    refresh=true: re-runs live website research for up to 50 of the most
    recently updated companies and persists the new scores.
    """
    if not refresh:
        latest = (db.query(
                EvaluationResult.company_id.label("cid"),
                func.max(EvaluationResult.id).label("eid"))
            .filter(EvaluationResult.user_id == user.id)
            .group_by(EvaluationResult.company_id).subquery())
        rows = (db.query(EvaluationResult, Company)
                .join(latest, EvaluationResult.id == latest.c.eid)
                .join(Company, EvaluationResult.company_id == Company.id)
                .filter(EvaluationResult.user_id == user.id,
                        Company.user_id == user.id,
                        Company.lifecycle == "active",
                        func.coalesce(EvaluationResult.buyer_fit_score,
                                      0) >= min_score)
                .order_by(EvaluationResult.buyer_fit_score.desc())
                .limit(min(max(limit, 1), 500)).all())
        results = []
        for e, c in rows:
            payload = _eval_out(e) or {}
            results.append({
                "company_id": c.id, "name": c.name, "website": c.website,
                "score": e.buyer_fit_score,
                "buyer_type": payload.get("buyer_type") or "Unclear",
                "product_fit": payload.get("product_fit"),
                "country_fit": payload.get("country_fit"),
                "recommended_action": payload.get("recommended_next_action"),
                "missing_evidence": payload.get("missing_evidence", []),
                "emails": payload.get("emails", []),
                "phones": payload.get("phones", []),
                "evaluated_at": e.evaluated_at,
            })
        return {"count": len(results), "companies": results,
                "mode": "persisted",
                "note": "Scores from persisted research runs. Pass "
                        "refresh=true to re-run live website research "
                        "(capped at 50 companies)."}

    # live refresh path (capped — this hits real websites)
    companies = (db.query(Company)
                 .filter(Company.user_id == user.id,
                         Company.lifecycle == "active")
                 .order_by(Company.updated_at.desc())
                 .limit(min(max(limit, 1), 50)).all())
    results = []
    errors = []
    for c in companies:
        try:
            outcome = research_and_persist(db, user.id, c)
            db.commit()
            q = outcome["qualification"]
            if q["score"] < min_score:
                continue
            results.append({
                "company_id": c.id, "name": c.name, "website": c.website,
                "score": q["score"], "buyer_type": q["buyer_type"],
                "product_fit": q["product_fit"],
                "country_fit": q["country_fit"],
                "recommended_action": q["recommended_next_action"],
                "missing_evidence": q["missing_evidence"],
                "emails": [e["value"] for e
                           in outcome["research"].get("emails", [])],
                "phones": [p["value"] for p
                           in outcome["research"].get("phones", [])],
                "evaluated_at": c.last_verified,
            })
        except Exception as ex:
            db.rollback()
            errors.append(f"{c.name}: {str(ex)[:100]}")
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"count": len(results), "companies": results,
            "mode": "live_refresh", "errors": errors[:5]}


# ---------------------------------------------------------------------------
# NEW: company picker + durable report
# ---------------------------------------------------------------------------

@router.get("/research/companies")
def pick_companies(
    q: str | None = None,
    researched: bool | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Lightweight company picker for the research UI (no raw IDs)."""
    limit = min(max(limit, 1), 50)
    qry = db.query(Company).filter(Company.user_id == user.id,
                                   Company.lifecycle == "active")
    if q:
        like = f"%{q}%"
        qry = qry.filter(Company.name.ilike(like) |
                         (Company.website.isnot(None) &
                          Company.website.ilike(like)))
    rows = qry.order_by(Company.name.asc()).limit(limit).all()
    ids = [c.id for c in rows]
    evals = _latest_evaluations(db, user.id, ids)
    emails: dict[int, int] = {}
    phones: dict[int, int] = {}
    if ids:
        for cid, n in (db.query(Contact.company_id, func.count(Contact.id))
                       .filter(Contact.user_id == user.id,
                               Contact.company_id.in_(ids),
                               Contact.kind == "EMAIL")
                       .group_by(Contact.company_id).all()):
            emails[cid] = n
        for cid, n in (db.query(Contact.company_id, func.count(Contact.id))
                       .filter(Contact.user_id == user.id,
                               Contact.company_id.in_(ids),
                               Contact.kind.in_(["PHONE", "WHATSAPP"]))
                       .group_by(Contact.company_id).all()):
            phones[cid] = n
    items = []
    for c in rows:
        ev = evals.get(c.id)
        if researched is True and ev is None:
            continue
        if researched is False and ev is not None:
            continue
        items.append({
            "id": c.id, "name": c.name, "country": c.country,
            "city": c.city, "website": c.website, "category": c.category,
            "profile_url": c.profile_url,
            "email_count": emails.get(c.id, 0),
            "phone_count": phones.get(c.id, 0),
            "has_research": ev is not None,
            "research_score": ev.buyer_fit_score if ev else None,
            "evaluated_at": ev.evaluated_at if ev else None,
        })
    return {"count": len(items), "items": items,
            "note": "Live website research is only run when you request "
                    "it — listing is DB-only."}


@router.get("/research/report/{company_id}")
def research_report(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Durable research report assembled from stored data only.

    Never performs network calls: contacts, evidence rows and scores come
    from persisted research runs. Use POST /refresh to re-run live.
    """
    company = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return _report_payload(db, user, company)


@router.post("/research/report/{company_id}/refresh",
             dependencies=[Depends(rate_limit(
                 "research_refresh", settings.RATE_SENSITIVE_PER_MIN))])
def research_report_refresh(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-run live website research for a company, persist evidence +
    evaluation, and return the fresh report. Rate-limited."""
    company = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    outcome = research_and_persist(db, user.id, company)
    db.commit()
    audit(db, user.id, "research_refresh", "company", company.id)
    db.commit()
    return _report_payload(db, user, company,
                           research=outcome["research"], refreshed=True)


# ---------------------------------------------------------------------------
# existing: draft generation (unchanged behaviour)
# ---------------------------------------------------------------------------

@router.post("/research/draft/{company_id}")
def generate_draft(
    company_id: int,
    payload: dict = {},
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate an outreach draft for a company (no sending). Uses Gemini if available."""
    company = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    from app.config import settings as cfg_settings
    research = research_company_website(company, db)
    qualification = qualify_buyer(company, research)
    gemini_key = payload.get("gemini_key") or cfg_settings.GEMINI_API_KEY
    draft = generate_outreach_draft(company, qualification, research, gemini_key)

    return {
        "company_id": company.id,
        "company_name": company.name,
        "draft": draft,
        "ai_available": bool(gemini_key),
    }
