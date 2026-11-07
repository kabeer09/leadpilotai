"""Google Business Lead Finder API — campaigns, leads, jobs, export, settings.

Tenant-scoped everywhere (user_id on every query), rate-limited on writes,
audit-logged. Long work (search, website crawling) never runs inside the
HTTP request — it starts a background thread in gbleads_jobs.
"""
import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, rate_limit, audit
from app.models import User
from app.models_gbleads import (SearchCampaign, BusinessLead, LeadEvidence,
                                GBJob)
from app.schemas import GBCampaignCreate, GBLeadUpdate, GBEnrichRequest
from app import gbleads_jobs
from app.gb_to_companies import sync_lead_to_company
from app.google_places import provider_name

router = APIRouter(prefix="/api/gbleads", tags=["gbleads"])


def _jloads(s: str | None, default):
    try:
        v = json.loads(s) if s else default
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------

@router.post("/campaigns", status_code=201,
             dependencies=[Depends(rate_limit("gblead", settings.RATE_SENSITIVE_PER_MIN))])
def create_campaign(payload: GBCampaignCreate,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    filters = dict(payload.filters or {})
    if payload.enrich:
        filters["enrich_after_search"] = True
    used = gbleads_jobs.api_used_today(db, user.id)
    if used >= settings.GB_DAILY_API_CALLS:
        raise HTTPException(status_code=429, detail=(
            f"Daily search API request budget reached "
            f"({settings.GB_DAILY_API_CALLS}/day) — try tomorrow"))
    # auto/openstreetmap modes always have a provider (free OSM fallback);
    # only an explicitly Google-only setup requires the key up front.
    if (settings.GB_PROVIDER or "auto").strip().lower() == "google" \
            and not settings.GOOGLE_MAPS_API_KEY:
        raise HTTPException(status_code=400, detail=(
            "GB_PROVIDER=google but GOOGLE_MAPS_API_KEY is not configured on "
            "the server — add a Places API (New) key (Settings → Google "
            "Business Lead Finder) or set GB_PROVIDER=auto to use the free "
            "OpenStreetMap provider"))

    c = SearchCampaign(
        user_id=user.id,
        campaign_name=payload.campaign_name.strip(),
        keywords=json.dumps(payload.keywords, ensure_ascii=False),
        categories=json.dumps(payload.categories, ensure_ascii=False),
        business_types=json.dumps(payload.business_types, ensure_ascii=False),
        countries=json.dumps(payload.countries, ensure_ascii=False),
        state=(payload.state or None),
        cities=json.dumps(payload.cities, ensure_ascii=False),
        max_results=min(payload.max_results, settings.GB_MAX_RESULTS_PER_SEARCH),
        filters_json=json.dumps(filters, ensure_ascii=False),
        status="PENDING" if payload.start else "PAUSED")
    db.add(c)
    db.commit()
    db.refresh(c)
    audit(db, user.id, "gb_campaign_create", "gb_campaign", c.id,
          payload.campaign_name[:200])
    db.commit()
    if payload.start:
        gbleads_jobs.start_search(c.id)
    return _campaign_out(db, c, used_today=gbleads_jobs.api_used_today(db, user.id))


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    rows = (db.query(SearchCampaign).filter(SearchCampaign.user_id == user.id)
            .order_by(SearchCampaign.id.desc()).limit(100).all())
    return {"count": len(rows),
            "campaigns": [_campaign_out(db, c) for c in rows]}


def _campaign_out(db: Session, c: SearchCampaign, used_today: int | None = None) -> dict:
    return {
        "id": c.id, "campaign_name": c.campaign_name,
        "keywords": _jloads(c.keywords, []),
        "categories": _jloads(c.categories, []),
        "business_types": _jloads(c.business_types, []),
        "countries": _jloads(c.countries, []),
        "state": c.state, "cities": _jloads(c.cities, []),
        "max_results": c.max_results,
        "filters": _jloads(c.filters_json, {}),
        "status": c.status,
        "total_discovered": c.total_discovered,
        "total_enriched": c.total_enriched,
        "total_skipped": c.total_skipped,
        "duplicate_count": c.duplicate_count,
        "api_calls": c.api_calls, "api_errors": c.api_errors,
        "website_errors": c.website_errors,
        "estimated_cost_usd": c.estimated_cost_usd,
        "error": c.error,
        "started_at": c.started_at, "finished_at": c.finished_at,
        "created_at": c.created_at,
        "running": gbleads_jobs.search_running(c.id),
        "quota_calls_used_today": (used_today if used_today is not None
                                   else gbleads_jobs.api_used_today(db, c.user_id)),
        "quota_calls_daily": settings.GB_DAILY_API_CALLS,
    }


def _get_campaign(db, user, campaign_id) -> SearchCampaign:
    c = db.query(SearchCampaign).filter(
        SearchCampaign.id == campaign_id,
        SearchCampaign.user_id == user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    return _campaign_out(db, _get_campaign(db, user, campaign_id))


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    c = _get_campaign(db, user, campaign_id)
    if c.status in ("COMPLETED", "CANCELLED"):
        return _campaign_out(db, c)
    c.status = "CANCELLED"
    c.finished_at = datetime.utcnow()
    audit(db, user.id, "gb_campaign_cancel", "gb_campaign", c.id)
    db.commit()
    return _campaign_out(db, c)


@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Stop between units of work; partial results are kept (resume re-runs
    discovery — place/domain/name dedup makes that idempotent)."""
    c = _get_campaign(db, user, campaign_id)
    if c.status in ("PENDING", "RUNNING"):
        c.status = "PAUSED"
        db.commit()
    return _campaign_out(db, c)


@router.post("/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Resume PAUSED (continue) or retry FAILED (after e.g. quota reset).
    COMPLETED/CANCELLED are terminal."""
    c = _get_campaign(db, user, campaign_id)
    if c.status in ("COMPLETED", "CANCELLED"):
        return _campaign_out(db, c)
    if gbleads_jobs.search_running(c.id):
        return _campaign_out(db, c)  # already running
    if c.status == "FAILED":
        # retry budget guard: don't restart into an exhausted quota
        if gbleads_jobs.api_used_today(db, user.id) >= settings.GB_DAILY_API_CALLS:
            raise HTTPException(status_code=429, detail=(
                "Daily Google request budget still reached — retry tomorrow"))
    c.status = "PENDING"
    c.error = None
    c.finished_at = None
    db.commit()
    if not gbleads_jobs.start_search(c.id):
        c.status = "RUNNING"
        db.commit()
    audit(db, user.id, "gb_campaign_resume", "gb_campaign", c.id)
    db.commit()
    return _campaign_out(db, c)


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------

_SORTABLE = {
    "relevance": BusinessLead.relevance_score,
    "rating": BusinessLead.rating,
    "reviews": BusinessLead.review_count,
    "name": BusinessLead.company_name,
    "created": BusinessLead.created_at,
    "country": BusinessLead.country,
}


@router.get("/leads")
def list_leads(
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=200),
        q: str | None = None,
        campaign_id: int | None = None,
        country: str | None = None,
        city: str | None = None,
        category: str | None = None,
        business_type: str | None = None,
        lead_status: str | None = None,
        enrichment_status: str | None = None,
        min_relevance: int | None = Query(None, ge=0, le=100),
        has_email: bool | None = None,
        has_website: bool | None = None,
        has_phone: bool | None = None,
        sort: str = "relevance",
        order: str = "desc",
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    query = db.query(BusinessLead).filter(BusinessLead.user_id == user.id)
    if campaign_id:
        query = query.filter(BusinessLead.campaign_id == campaign_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            BusinessLead.company_name.ilike(like),
            BusinessLead.city.ilike(like),
            BusinessLead.country.ilike(like),
            BusinessLead.business_category.ilike(like)))
    if country:
        query = query.filter(BusinessLead.country == country)
    if city:
        query = query.filter(BusinessLead.city == city)
    if category:
        query = query.filter(BusinessLead.business_category == category)
    if business_type:
        query = query.filter(BusinessLead.business_type == business_type)
    if lead_status:
        query = query.filter(BusinessLead.lead_status == lead_status)
    if enrichment_status:
        query = query.filter(BusinessLead.enrichment_status == enrichment_status)
    if min_relevance is not None:
        query = query.filter(BusinessLead.relevance_score >= min_relevance)
    if has_email is not None:
        query = query.filter(BusinessLead.email.isnot(None)) if has_email \
            else query.filter(BusinessLead.email.is_(None))
    if has_website is not None:
        query = query.filter(BusinessLead.website.isnot(None)) if has_website \
            else query.filter(BusinessLead.website.is_(None))
    if has_phone is not None:
        query = query.filter(BusinessLead.phone.isnot(None)) if has_phone \
            else query.filter(BusinessLead.phone.is_(None))

    total = query.count()
    col = _SORTABLE.get(sort, BusinessLead.relevance_score)
    col = col.desc() if order == "desc" else col.asc()
    rows = (query.order_by(col, BusinessLead.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_lead_out(l, detail=False) for l in rows]}


def _lead_out(l: BusinessLead, detail: bool = True) -> dict:
    out = {
        "id": l.id, "campaign_id": l.campaign_id,
        "company_name": l.company_name,
        "business_category": l.business_category,
        "business_type": l.business_type,
        "business_description": l.business_description,
        "website": l.website, "phone": l.phone, "email": l.email,
        "country": l.country, "state": l.state, "city": l.city,
        "address": l.address,
        "google_maps_url": l.google_maps_url,
        "rating": l.rating, "review_count": l.review_count,
        "relevance_score": l.relevance_score,
        "relevance_label": l.relevance_label,
        "enrichment_status": l.enrichment_status,
        "lead_status": l.lead_status,
        "place_id": l.place_id,
        "created_at": l.created_at, "enriched_at": l.enriched_at,
    }
    if detail:
        out["match_reasons"] = _jloads(l.match_reasons, [])
        out["business_type_evidence"] = _jloads(l.business_type_evidence, [])
        out["types"] = _jloads(l.types_json, [])
    return out


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    l = db.query(BusinessLead).filter(
        BusinessLead.id == lead_id,
        BusinessLead.user_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Lead not found")
    evidence = (db.query(LeadEvidence).filter(LeadEvidence.lead_id == l.id)
                .order_by(LeadEvidence.id).all())
    out = _lead_out(l, detail=True)
    out["evidence"] = [{
        "id": e.id, "field_name": e.field_name, "field_value": e.field_value,
        "source_url": e.source_url, "extraction_method": e.extraction_method,
        "confidence": e.confidence, "extracted_at": e.extracted_at,
    } for e in evidence]
    return out


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: int, payload: GBLeadUpdate,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    l = db.query(BusinessLead).filter(
        BusinessLead.id == lead_id,
        BusinessLead.user_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Lead not found")
    if payload.lead_status is not None:
        if payload.lead_status not in ("NEW", "SAVED", "CONTACTED", "ARCHIVED"):
            raise HTTPException(status_code=422,
                                detail="Invalid lead_status")
        l.lead_status = payload.lead_status
    db.commit()
    return _lead_out(l, detail=True)


# ---------------------------------------------------------------------------
# enrichment jobs
# ---------------------------------------------------------------------------

@router.post("/enrich",
             dependencies=[Depends(rate_limit("gblead", settings.RATE_SENSITIVE_PER_MIN))])
def start_enrichment(payload: GBEnrichRequest,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Queue website enrichment as a background job (never in-request)."""
    if not settings.GB_ENRICHMENT_ENABLED:
        raise HTTPException(status_code=400,
                            detail="Website enrichment is disabled (GB_ENRICHMENT_ENABLED=false)")
    if not payload.lead_ids and not payload.campaign_id:
        raise HTTPException(status_code=422,
                            detail="lead_ids or campaign_id required")
    if payload.campaign_id:
        _get_campaign(db, user, payload.campaign_id)  # tenant check
    if payload.lead_ids:
        owned = db.query(func.count(BusinessLead.id)).filter(
            BusinessLead.user_id == user.id,
            BusinessLead.id.in_(payload.lead_ids)).scalar() or 0
        if owned != len(set(payload.lead_ids)):
            raise HTTPException(status_code=404,
                                detail="Some leads not found in your account")
    job = GBJob(user_id=user.id, kind="enrich",
                campaign_id=payload.campaign_id,
                lead_ids_json=(json.dumps(payload.lead_ids)
                               if payload.lead_ids else None),
                status="PENDING")
    db.add(job)
    db.commit()
    db.refresh(job)
    audit(db, user.id, "gb_enrich_start", "gb_job", job.id,
          f"leads={len(payload.lead_ids)} campaign={payload.campaign_id}")
    db.commit()
    gbleads_jobs.start_enrich(job.id)
    return _job_out(job)


@router.post("/save-to-companies",
             dependencies=[Depends(rate_limit("gblead", settings.RATE_SENSITIVE_PER_MIN))])
def save_to_companies(payload: GBEnrichRequest,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Copy crawled lead facts into the CRM Companies list (idempotent).

    Same sync the search/enrich pipelines run automatically — this endpoint
    backfills leads that were crawled before the sync existed. Every value
    comes from the stored lead + its evidence rows; nothing is guessed and
    existing Company/Contact values are never overwritten (fill-only).
    """
    if not payload.lead_ids and not payload.campaign_id:
        raise HTTPException(status_code=422,
                            detail="lead_ids or campaign_id required")
    if payload.campaign_id:
        _get_campaign(db, user, payload.campaign_id)  # tenant check
    if payload.lead_ids:
        owned = db.query(func.count(BusinessLead.id)).filter(
            BusinessLead.user_id == user.id,
            BusinessLead.id.in_(payload.lead_ids)).scalar() or 0
        if owned != len(set(payload.lead_ids)):
            raise HTTPException(status_code=404,
                                detail="Some leads not found in your account")
    q = db.query(BusinessLead).filter(BusinessLead.user_id == user.id)
    if payload.campaign_id:
        q = q.filter(BusinessLead.campaign_id == payload.campaign_id)
    if payload.lead_ids:
        q = q.filter(BusinessLead.id.in_(payload.lead_ids))
    leads = q.all()
    synced, errors = 0, []
    for l in leads:
        try:
            sync_lead_to_company(db, l)
            synced += 1
        except Exception as e:  # noqa: BLE001 — report honestly, keep going
            errors.append(f"lead {l.id}: {e}")
    db.commit()
    audit(db, user.id, "gb_save_to_companies", "gb_lead", None,
          f"synced={synced} errors={len(errors)} campaign={payload.campaign_id}")
    db.commit()
    return {"requested": len(leads), "synced": synced, "errors": errors}


def _job_out(j: GBJob) -> dict:
    return {
        "id": j.id, "kind": j.kind, "campaign_id": j.campaign_id,
        "status": j.status, "total": j.total, "processed": j.processed,
        "enriched": j.enriched, "skipped": j.skipped, "errors": j.errors,
        "attempts": j.attempts, "error": j.error,
        "started_at": j.started_at, "finished_at": j.finished_at,
        "created_at": j.created_at,
        "running": gbleads_jobs.enrich_running(j.id),
    }


@router.get("/jobs")
def list_jobs(limit: int = 30, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    rows = (db.query(GBJob).filter(GBJob.user_id == user.id)
            .order_by(GBJob.id.desc()).limit(min(limit, 100)).all())
    return {"count": len(rows), "jobs": [_job_out(j) for j in rows]}


def _get_job(db, user, job_id) -> GBJob:
    j = db.query(GBJob).filter(GBJob.id == job_id,
                               GBJob.user_id == user.id).first()
    if not j:
        raise HTTPException(status_code=404, detail="Job not found")
    return j


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    j = _get_job(db, user, job_id)
    if j.status in ("COMPLETED", "CANCELLED"):
        return _job_out(j)
    j.status = "CANCELLED"
    j.finished_at = datetime.utcnow()
    db.commit()
    return _job_out(j)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """Retry a FAILED enrichment job — already-enriched leads are skipped
    (enrichment_status ENRICHED filter), so retry is safe and idempotent."""
    j = _get_job(db, user, job_id)
    if j.status in ("RUNNING", "PENDING"):
        return _job_out(j)
    if j.status == "COMPLETED":
        return _job_out(j)
    if gbleads_jobs.enrich_running(j.id):
        return _job_out(j)
    j.status = "PENDING"
    j.error = None
    j.finished_at = None
    db.commit()
    gbleads_jobs.start_enrich(j.id)
    audit(db, user.id, "gb_enrich_retry", "gb_job", j.id)
    db.commit()
    return _job_out(j)


# ---------------------------------------------------------------------------
# export (CSV / Excel)
# ---------------------------------------------------------------------------

_EXPORT_COLS = [
    ("Company Name", "company_name"), ("Business Category", "business_category"),
    ("Country", "country"), ("State", "state"), ("City", "city"),
    ("Website", "website"), ("Email", "email"), ("Phone", "phone"),
    ("Google Rating", "rating"), ("Review Count", "review_count"),
    ("Relevance Score", "relevance_score"), ("Relevance Label", "relevance_label"),
    ("Business Type", "business_type"),
    ("Enrichment Status", "enrichment_status"), ("Lead Status", "lead_status"),
    ("Address", "address"), ("Google Maps URL", "google_maps_url"),
    ("Place ID", "place_id"), ("Created At", "created_at"),
]


def _export_query(db, user, campaign_id, country, city, min_relevance,
                  only_email, only_phone, only_website):
    query = db.query(BusinessLead).filter(BusinessLead.user_id == user.id)
    if campaign_id:
        query = query.filter(BusinessLead.campaign_id == campaign_id)
    if country:
        query = query.filter(BusinessLead.country == country)
    if city:
        query = query.filter(BusinessLead.city == city)
    if min_relevance:
        query = query.filter(BusinessLead.relevance_score >= min_relevance)
    if only_email:
        query = query.filter(BusinessLead.email.isnot(None))
    if only_phone:
        query = query.filter(BusinessLead.phone.isnot(None))
    if only_website:
        query = query.filter(BusinessLead.website.isnot(None))
    # each business appears once (PK + place_id unique per user) — no dup rows
    return query.order_by(BusinessLead.relevance_score.desc(),
                          BusinessLead.id)


def _cell(l: BusinessLead, attr: str):
    v = getattr(l, attr)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return v


@router.get("/export")
def export_leads(
        format: str = Query("csv", pattern="^(csv|xlsx)$"),
        campaign_id: int | None = None,
        country: str | None = None,
        city: str | None = None,
        min_relevance: int | None = Query(None, ge=0, le=100),
        only_with_email: bool = False,
        only_with_phone: bool = False,
        only_with_website: bool = False,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)):
    rows = _export_query(db, user, campaign_id, country, city, min_relevance,
                         only_with_email, only_with_phone,
                         only_with_website).limit(20000).all()
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
    base = f"leadpilot-gb-leads-{stamp}"

    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([h for h, _ in _EXPORT_COLS])
        for l in rows:
            w.writerow([_cell(l, a) for _, a in _EXPORT_COLS])
        audit(db, user.id, "gb_export", "gb_lead", None,
              f"csv rows={len(rows)}")
        db.commit()
        return StreamingResponse(
            iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition":
                     f"attachment; filename={base}.csv"})

    # Excel (openpyxl)
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append([h for h, _ in _EXPORT_COLS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for l in rows:
        ws.append([_cell(l, a) for _, a in _EXPORT_COLS])
    for i, (_h, _a) in enumerate(_EXPORT_COLS, start=1):
        width = max(12, min(45, max(
            (len(str(ws.cell(row=r, column=i).value or ""))
             for r in range(1, min(ws.max_row, 200) + 1)), default=12) + 2))
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    audit(db, user.id, "gb_export", "gb_lead", None,
          f"xlsx rows={len(rows)}")
    db.commit()
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        iter([out.getvalue()]), media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition":
                 f"attachment; filename={base}.xlsx"})


# ---------------------------------------------------------------------------
# settings / status (spec section 11 — never exposes the key itself)
# ---------------------------------------------------------------------------

@router.get("/settings")
def gb_settings(db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    used_today = gbleads_jobs.api_used_today(db, user.id)
    leads_total = db.query(func.count(BusinessLead.id)).filter(
        BusinessLead.user_id == user.id).scalar() or 0
    enriched = db.query(func.count(BusinessLead.id)).filter(
        BusinessLead.user_id == user.id,
        BusinessLead.enrichment_status == "ENRICHED").scalar() or 0
    campaigns = db.query(func.count(SearchCampaign.id)).filter(
        SearchCampaign.user_id == user.id).scalar() or 0
    api_calls = db.query(func.coalesce(
        func.sum(SearchCampaign.api_calls), 0)).filter(
        SearchCampaign.user_id == user.id).scalar() or 0
    cost = db.query(func.coalesce(
        func.sum(SearchCampaign.estimated_cost_usd), 0.0)).filter(
        SearchCampaign.user_id == user.id).scalar() or 0.0
    pname = provider_name()
    google_cfg = bool(settings.GOOGLE_MAPS_API_KEY)
    if pname == "openstreetmap":
        note = ("Using the free OpenStreetMap provider (Nominatim + "
                "Overpass): no key, no billing, but sparse coverage and no "
                "ratings/reviews. For Google-quality data set "
                "GOOGLE_MAPS_API_KEY in backend/.env, enable \"Places API "
                "(New)\" in Google Cloud, link billing, and restart. The key "
                "never leaves the server.")
    else:
        note = ("Google Places API key is loaded from the server .env — "
                "restrict it to \"Places API (New)\" in Google Cloud and set "
                "a daily quota. The key never leaves the server. Set "
                "GB_PROVIDER=openstreetmap to use the free OSM provider "
                "instead.")
    return {
        "provider": {
            "name": pname,
            "free": pname != "google_places",
            "google_configured": google_cfg,
        },
        "google_places": {
            "configured": google_cfg,
            "provider": pname,
            "max_results_per_search": settings.GB_MAX_RESULTS_PER_SEARCH,
            "cost_per_call_usd": (0.0 if pname == "openstreetmap"
                                  else settings.GB_COST_PER_CALL_USD),
            "calls_used_today": used_today,
            "daily_call_budget": settings.GB_DAILY_API_CALLS,
            "search_timeout_seconds": settings.GB_SEARCH_TIMEOUT,
            "max_retries": settings.GB_SEARCH_MAX_RETRIES,
        },
        "enrichment": {
            "enabled": settings.GB_ENRICHMENT_ENABLED,
            "max_pages_per_site": settings.GB_MAX_PAGES_PER_SITE,
            "request_delay_seconds": settings.GB_REQUEST_DELAY,
        },
        "usage": {
            "campaigns": campaigns,
            "leads": leads_total,
            "enriched": enriched,
            "api_calls_total": api_calls,
            "estimated_cost_total_usd": round(float(cost), 4),
        },
        "note": note,
    }
