"""Phase 6: CRM pipeline + conversion funnel — persisted rows only.

Honesty contract (master prompt):
- Every count is a real tenant-scoped query. Nothing is estimated, sampled
  silently, or carried over from another tenant.
- Rates are `None` when their denominator is zero — an empty workspace can
  never show a fake 0% or a flattering 100%.
- Funnel stages are strictly nested (stage N is a subset of stage N-1), so
  `pct_of_previous` is a real conversion, not two unrelated counts.
- Lead-score bands reuse `trade_analyst.score_band` — the exact thresholds
  shown by POST /ai/lead-score — so the CRM, dashboard and AI endpoint can
  never disagree about what "Hot" means.
- Bulk scoring is bounded and reports `sampled: true` when it is.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, exists, func
from sqlalchemy.orm import Session

from app import trade_analyst
from app.models import (Company, Contact, EmailMessage, Lead, ProviderEvent,
                        Source)
from app.models_research import ContactEvidence, EvaluationResult

# Bands are declared in one place (trade_analyst) and reused everywhere.
BANDS = ("Hot", "Warm", "Nurture", "Low")
# The active working set: archived / excluded / merged rows are out of scope,
# and every payload says so.
_ACTIVE = "active"


def _pct(part: int, whole: int) -> float | None:
    """Percentage, or None when the denominator is zero (never a fake 0/100)."""
    if not whole:
        return None
    return round(100.0 * part / whole, 1)


# ---------------------------------------------------------------------------
# bulk lead scoring (same math as POST /ai/lead-score, computed in bulk)
# ---------------------------------------------------------------------------

def lead_scores_for(db: Session, user_id: int,
                    companies: list[Company]) -> dict[int, dict]:
    """Composite Phase-5 lead score for each company, from persisted rows.

    Uses the same `trade_analyst.lead_score` as the AI endpoint: research
    buyer fit, reachability, evidence depth and profile completeness.
    Zero network — evaluations, contacts and evidence all come from the DB.
    """
    out: dict[int, dict] = {}
    if not companies:
        return out

    from app import api_research  # local import: avoids a module import cycle
    ids = [c.id for c in companies]
    evals = api_research._latest_evaluations(db, user_id, ids)
    eval_out = {cid: api_research._eval_out(row) for cid, row in evals.items()}

    def grouped(model, extra):
        rows = (db.query(model.company_id, func.count(model.id))
                .filter(model.user_id == user_id,
                        model.company_id.in_(ids), *extra)
                .group_by(model.company_id).all())
        return dict(rows)

    emails = grouped(Contact, (Contact.kind == "EMAIL",))
    phones = grouped(Contact, (Contact.kind.in_(("PHONE", "WHATSAPP")),))
    evidence = grouped(ContactEvidence, ())

    for c in companies:
        out[c.id] = trade_analyst.lead_score(
            eval_out.get(c.id),
            emails.get(c.id, 0), phones.get(c.id, 0), evidence.get(c.id, 0),
            {"website": c.website, "country": c.country,
             "category": c.category, "product_summary": c.product_summary})
    return out


# ---------------------------------------------------------------------------
# CRM pipeline
# ---------------------------------------------------------------------------

def research_flags_for(db: Session, user_id: int,
                       company_ids: list[int]) -> dict[int, bool]:
    """True when the company has persisted research (evidence or a score run)."""
    if not company_ids:
        return {}
    ev = {r[0] for r in db.query(ContactEvidence.company_id).filter(
        ContactEvidence.user_id == user_id,
        ContactEvidence.company_id.in_(company_ids)).distinct().all()}
    el = {r[0] for r in db.query(EvaluationResult.company_id).filter(
        EvaluationResult.user_id == user_id,
        EvaluationResult.company_id.in_(company_ids)).distinct().all()}
    return {cid: (cid in ev or cid in el) for cid in company_ids}


def pipeline_summary(db: Session, user_id: int,
                     score_limit: int = 5000) -> dict:
    """Status/lifecycle/coverage counters + lead-score band distribution."""
    active_q = db.query(Company).filter(
        Company.user_id == user_id, Company.lifecycle == _ACTIVE)
    active_total = active_q.count()

    lead_status = dict(db.query(Lead.status, func.count(Lead.id)).filter(
        Lead.user_id == user_id).group_by(Lead.status).all())
    lifecycle = dict(db.query(Company.lifecycle, func.count(Company.id)).filter(
        Company.user_id == user_id).group_by(Company.lifecycle).all())

    def distinct_companies(model, *extra):
        return (db.query(func.count(func.distinct(model.company_id)))
                .filter(model.user_id == user_id, *extra).scalar() or 0)

    with_evidence = distinct_companies(ContactEvidence)
    with_eval = distinct_companies(EvaluationResult)
    with_email = distinct_companies(Contact, Contact.kind == "EMAIL")
    with_phone = distinct_companies(Contact,
                                    Contact.kind.in_(("PHONE", "WHATSAPP")))

    # sources that actually produced companies (attribution, not guesses)
    src_rows = db.query(Company.source_id, func.count(Company.id)).filter(
        Company.user_id == user_id, Company.lifecycle == _ACTIVE,
        Company.source_id.isnot(None)).group_by(
        Company.source_id).order_by(func.count(Company.id).desc()).limit(10).all()
    src_names = dict(db.query(Source.id, Source.name).filter(
        Source.user_id == user_id).all()) if src_rows else {}
    top_sources = [{"source_id": sid, "name": src_names.get(sid) or "Unknown",
                    "companies": n} for sid, n in src_rows]

    # lead-score bands over the bounded active working set
    sampled = active_total > score_limit
    scored_rows = (active_q.order_by(Company.id).limit(score_limit).all()
                   if sampled else active_q.all())
    scores = lead_scores_for(db, user_id, scored_rows)
    band_counts: dict[str, int] = {b: 0 for b in BANDS}
    for s in scores.values():
        band_counts[s["band"]] = band_counts.get(s["band"], 0) + 1
    scored_total = len(scores)
    bands = [{"band": b, "count": band_counts.get(b, 0),
              "pct": _pct(band_counts.get(b, 0), scored_total)}
             for b in BANDS]

    return {
        "record_type": "crm_pipeline",
        "scope": {"lifecycle": _ACTIVE, "scored_companies": scored_total,
                  "companies_in_scope": active_total,
                  "score_limit": score_limit, "sampled": sampled},
        "lead_status": [{"status": k, "count": v} for k, v in
                        sorted(lead_status.items(), key=lambda x: -x[1])],
        "lifecycle": [{"lifecycle": k, "count": v} for k, v in
                      sorted(lifecycle.items(), key=lambda x: -x[1])],
        "research_coverage": {
            "companies_in_scope": active_total,
            "with_evidence": with_evidence,
            "with_evaluation": with_eval,
            "pct_evidence": _pct(with_evidence, active_total),
            "pct_evaluation": _pct(with_eval, active_total),
        },
        "contact_coverage": {
            "companies_in_scope": active_total,
            "with_email": with_email,
            "with_phone": with_phone,
            "pct_email": _pct(with_email, active_total),
        },
        "lead_score_bands": bands,
        "lead_score_note": (
            "Bands are the deterministic Phase-5 composite (research buyer fit "
            "+ reachability + evidence depth + profile completeness) computed "
            "from persisted rows only — same thresholds as POST /ai/lead-score. "
            "A company with no research yet scores Low; directory presence "
            "alone never proves buying intent."),
        "top_sources": top_sources,
        "note": ("Every figure is a count of your own stored rows. Percentages "
                 "are null when the denominator is zero — never a fake 0%."),
    }


# ---------------------------------------------------------------------------
# conversion funnel (strictly nested stages)
# ---------------------------------------------------------------------------

def _has_email(user_id: int):
    """Correlated EXISTS: the row's Company has an EMAIL contact."""
    return exists().where(and_(Contact.company_id == Company.id,
                               Contact.user_id == user_id,
                               Contact.kind == "EMAIL"))


def _has_evidence(user_id: int):
    """Correlated EXISTS: the row's Company has research evidence."""
    return exists().where(and_(ContactEvidence.company_id == Company.id,
                               ContactEvidence.user_id == user_id))


def funnel_summary(db: Session, user_id: int) -> dict:
    """Nested conversion funnel + honest rates (null when undefined)."""
    co_base = (Company.user_id == user_id, Company.lifecycle != "MERGED")

    discovered = db.query(func.count(Company.id)).filter(*co_base).scalar() or 0
    with_email = db.query(func.count(Company.id)).filter(
        *co_base, _has_email(user_id)).scalar() or 0
    researched = db.query(func.count(Company.id)).filter(
        *co_base, _has_email(user_id),
        _has_evidence(user_id)).scalar() or 0

    # Lead-side stages: the lead belongs to a company that has email + evidence
    def lead_scoped(*extra):
        company_ok = exists().where(and_(
            Company.id == Lead.company_id, *co_base,
            _has_email(user_id), _has_evidence(user_id)))
        return (Lead.user_id == user_id, company_ok, *extra)

    was_emailed = exists().where(and_(
        EmailMessage.lead_id == Lead.id,
        EmailMessage.user_id == user_id,
        EmailMessage.is_test.is_(False)))
    # Must correlate BOTH hops (lead -> message -> event); otherwise the
    # EXISTS is uncorrelated and any reply anywhere would match.
    did_reply = exists().where(and_(
        EmailMessage.lead_id == Lead.id,
        EmailMessage.user_id == user_id,
        EmailMessage.is_test.is_(False),
        ProviderEvent.message_id == EmailMessage.id,
        ProviderEvent.event_type == "reply"))

    emailed = db.query(func.count(func.distinct(Lead.company_id))).filter(
        *lead_scoped(was_emailed)).scalar() or 0
    replied = db.query(func.count(func.distinct(Lead.company_id))).filter(
        *lead_scoped(was_emailed, did_reply)).scalar() or 0

    stages = [
        {"key": "discovered", "label": "Companies discovered",
         "count": discovered},
        {"key": "with_email", "label": "With a business email",
         "count": with_email},
        {"key": "researched", "label": "Researched (evidence saved)",
         "count": researched},
        {"key": "emailed", "label": "Emailed (researched leads)",
         "count": emailed},
        {"key": "replied", "label": "Replied", "count": replied},
    ]
    prev = None
    for st in stages:
        st["pct_of_previous"] = (_pct(st["count"], prev)
                                 if prev is not None else None)
        st["pct_of_first"] = _pct(st["count"], discovered)
        prev = st["count"]

    def count(model, *extra):
        return db.query(func.count(model.id)).filter(
            model.user_id == user_id, *extra).scalar() or 0

    attempted = count(EmailMessage, EmailMessage.is_test.is_(False))
    delivered = count(EmailMessage, EmailMessage.is_test.is_(False),
                      EmailMessage.status.in_(("delivered", "replied")))
    bounced = count(EmailMessage, EmailMessage.status == "bounced")
    # replies only count on real (non-test) messages — same denominator
    replies = (db.query(func.count(ProviderEvent.id)).filter(
        ProviderEvent.user_id == user_id,
        ProviderEvent.event_type == "reply",
        ProviderEvent.message_id.in_(
            db.query(EmailMessage.id).filter(
                EmailMessage.user_id == user_id,
                EmailMessage.is_test.is_(False)))).scalar() or 0)
    followups_due = count(Lead, Lead.next_followup_at.isnot(None),
                          Lead.next_followup_at <= datetime.utcnow())

    return {
        "record_type": "conversion_funnel",
        "stages": stages,
        "rates": {
            "delivery_rate_pct": _pct(delivered, attempted),
            "reply_rate_pct": _pct(replies, attempted),
            "bounce_rate_pct": _pct(bounced, attempted),
            "followups_due": followups_due,
            "denominators": {"attempted": attempted, "delivered": delivered,
                             "bounced": bounced, "replies": replies},
        },
        "note": ("Stages are strictly nested: each is a subset of the one "
                 "before it, so pct_of_previous is a real conversion. Rates "
                 "are null when nothing has been sent yet — never a fake 0%."),
    }
