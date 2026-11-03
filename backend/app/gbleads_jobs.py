"""Background job runner for Google Business Lead Finder.

Mirrors the crawler's proven pattern: daemon threads keyed by job, sessions
per thread, incremental commits (crash leaves valid partial data), and
cancel/pause checked between units of work.

- Search runs on its own SearchCampaign row (status IS the job status).
- Enrichment batches run on GBJob rows (retryable).
- Startup recovery (main.py) marks stale RUNNING rows FAILED, matching the
  crawl-job convention: FAILED unlocks Retry.
"""
import json
import logging
import math
import threading
from datetime import datetime

from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models_gbleads import SearchCampaign, BusinessLead, GBJob
from app.google_places import (PlacesAPIError, PlacesConfigError,
                               PlacesQuotaError, build_queries,
                               get_places_provider)
from app.gb_scoring import (score_relevance, classify_business_type,
                            normalize_domain, dedup_key_name_city)
from app.gb_enricher import enrich_lead

logger = logging.getLogger(__name__)

_search_threads: dict[int, threading.Thread] = {}
_enrich_threads: dict[int, threading.Thread] = {}
_lock = threading.Lock()


def _jloads(s: str | None, default):
    try:
        v = json.loads(s) if s else default
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


def api_used_today(db, user_id: int) -> int:
    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.coalesce(func.sum(SearchCampaign.api_calls), 0)).filter(
        SearchCampaign.user_id == user_id,
        SearchCampaign.created_at >= day).scalar() or 0


# ---------------------------------------------------------------------------
# start helpers (idempotent, like crawler.start_job)
# ---------------------------------------------------------------------------

def start_search(campaign_id: int) -> bool:
    with _lock:
        t = _search_threads.get(campaign_id)
        if t and t.is_alive():
            return False
        t = threading.Thread(target=run_search, args=(campaign_id,),
                             daemon=True, name=f"gb-search-{campaign_id}")
        _search_threads[campaign_id] = t
        t.start()
        return True


def start_enrich(job_id: int) -> bool:
    with _lock:
        t = _enrich_threads.get(job_id)
        if t and t.is_alive():
            return False
        t = threading.Thread(target=run_enrich, args=(job_id,),
                             daemon=True, name=f"gb-enrich-{job_id}")
        _enrich_threads[job_id] = t
        t.start()
        return True


def search_running(campaign_id: int) -> bool:
    t = _search_threads.get(campaign_id)
    return bool(t and t.is_alive())


def enrich_running(job_id: int) -> bool:
    t = _enrich_threads.get(job_id)
    return bool(t and t.is_alive())


def _fresh_status(db, model, row_id: int) -> str:
    """Re-read status written by another thread/request."""
    return db.query(model.status).filter(model.id == row_id).scalar() or ""


# ---------------------------------------------------------------------------
# search run
# ---------------------------------------------------------------------------

def run_search(campaign_id: int) -> None:
    db = SessionLocal()
    try:
        c = db.query(SearchCampaign).filter(
            SearchCampaign.id == campaign_id).first()
        if not c or c.status != "PENDING":
            return
        c.status = "RUNNING"
        c.started_at = datetime.utcnow()
        c.error = None
        db.commit()

        keywords = _jloads(c.keywords, [])
        countries = _jloads(c.countries, [])
        cities = _jloads(c.cities, [])
        business_types = _jloads(c.business_types, [])
        categories = _jloads(c.categories, [])
        filters = _jloads(c.filters_json, {})

        queries = build_queries(keywords, countries, c.state, cities,
                                business_types, categories)
        if not queries:
            c.status = "FAILED"
            c.error = "No searchable query — add at least one keyword"
            c.finished_at = datetime.utcnow()
            db.commit()
            return

        # dedup state: place_id / domain / name+city(+country)
        places_seen: set[str] = set()
        domains_seen: set[str] = set()
        names_seen: set[str] = set()
        for row in db.query(BusinessLead.place_id, BusinessLead.website_domain,
                            BusinessLead.company_name, BusinessLead.city,
                            BusinessLead.country).filter(
                BusinessLead.user_id == c.user_id).all():
            if row[0]:
                places_seen.add(row[0])
            if row[1]:
                domains_seen.add(row[1])
            nk = dedup_key_name_city(row[2], row[3], row[4])
            if nk:
                names_seen.add(nk)

        provider = get_places_provider()
        per_query = max(1, math.ceil(c.max_results / len(queries)))
        per_query = min(per_query, settings.GB_MAX_RESULTS_PER_SEARCH)
        any_success = False

        for q in queries:
            status = _fresh_status(db, SearchCampaign, c.id)
            if status in ("CANCELLED", "PAUSED", "COMPLETED", "FAILED"):
                return  # another actor decided; leave their state intact
            if c.total_discovered >= c.max_results:
                break
            # per-user daily request budget (spec: configurable budgets)
            if api_used_today(db, c.user_id) >= settings.GB_DAILY_API_CALLS:
                c.status = "FAILED"
                c.error = (f"Daily Google request budget reached "
                           f"({settings.GB_DAILY_API_CALLS}/day) — partial "
                           f"results kept ({c.total_discovered}); retry tomorrow")
                c.finished_at = datetime.utcnow()
                db.commit()
                return
            try:
                calls_before = provider.calls
                places = provider.search(q["query"], max_results=per_query)
                # provider.calls is cumulative on the instance — count the delta
                c.api_calls += provider.calls - calls_before
                c.estimated_cost_usd = round(
                    c.api_calls * settings.GB_COST_PER_CALL_USD, 4)
                any_success = True
            except PlacesQuotaError as e:
                c.api_errors += 1
                c.status = "FAILED"
                c.error = str(e)[:900]
                c.finished_at = datetime.utcnow()
                db.commit()
                return
            except PlacesConfigError as e:
                c.api_errors += 1
                c.status = "FAILED"
                c.error = str(e)[:900]
                c.finished_at = datetime.utcnow()
                db.commit()
                return
            except PlacesAPIError as e:
                c.api_errors += 1
                c.error = str(e)[:500]
                db.commit()
                logger.warning("gb search query failed: %s", e)
                continue

            for p in places:
                if c.total_discovered >= c.max_results:
                    break
                if not p.get("name"):
                    c.total_skipped += 1
                    continue
                # ---- dedup (spec: place id -> domain -> name+location) ----
                pid = p.get("place_id")
                domain = normalize_domain(p.get("website"))
                nkey = dedup_key_name_city(
                    p.get("name"), p.get("city") or q.get("city"),
                    p.get("country") or q.get("country"))
                if (pid and pid in places_seen) or \
                   (domain and domain in domains_seen) or \
                   (nkey and nkey in names_seen):
                    c.duplicate_count += 1
                    continue
                # ---- discovery filters ----
                if filters.get("has_website") and not p.get("website"):
                    c.total_skipped += 1
                    continue
                if filters.get("has_phone") and not p.get("phone"):
                    c.total_skipped += 1
                    continue
                if filters.get("min_rating") and \
                        (p.get("rating") or 0) < float(filters["min_rating"]):
                    c.total_skipped += 1
                    continue
                if filters.get("min_reviews") and \
                        (p.get("review_count") or 0) < int(filters["min_reviews"]):
                    c.total_skipped += 1
                    continue

                listing_text = " ".join(filter(None, [
                    p.get("name"), p.get("business_category"),
                    p.get("description") or ""]))
                score, label, reasons = score_relevance(
                    keywords, p.get("name"), p.get("business_category"),
                    [("Google listing", listing_text)])
                reasons.insert(0, f"Found via Google search: \"{q['query']}\"")
                btype, bev = classify_business_type(
                    [("Google listing", listing_text)])

                lead = BusinessLead(
                    user_id=c.user_id, campaign_id=c.id, place_id=pid,
                    source_provider="google_places",
                    company_name=p["name"],
                    business_category=p.get("business_category"),
                    primary_type=p.get("primary_type"),
                    types_json=json.dumps(p.get("types") or []),
                    business_description=p.get("description"),
                    website=p.get("website"),
                    website_domain=domain,
                    phone=p.get("phone"),
                    country=p.get("country") or q.get("country"),
                    state=p.get("state") or q.get("state"),
                    city=p.get("city") or q.get("city"),
                    address=p.get("address"),
                    latitude=p.get("latitude"),
                    longitude=p.get("longitude"),
                    google_maps_url=p.get("google_maps_url"),
                    rating=p.get("rating"),
                    review_count=p.get("review_count"),
                    relevance_score=score, relevance_label=label,
                    match_reasons=json.dumps(reasons[:12], ensure_ascii=False),
                    business_type=btype,
                    business_type_evidence=json.dumps(bev, ensure_ascii=False),
                    enrichment_status=("NO_WEBSITE" if not p.get("website")
                                       else "NOT_ENRICHED"),
                    lead_status="NEW")
                db.add(lead)
                if pid:
                    places_seen.add(pid)
                if domain:
                    domains_seen.add(domain)
                if nkey:
                    names_seen.add(nkey)
                c.total_discovered += 1

            db.commit()  # incremental: every finished query is durable

        # ---- finish ----
        if any_success:
            c.status = "COMPLETED"
            c.error = None
        else:
            c.status = "FAILED"
            c.error = c.error or "Every Google request failed — see api_errors"
        c.finished_at = datetime.utcnow()
        db.commit()

        # auto-enqueue website enrichment when the campaign asked for it
        if c.status == "COMPLETED" and filters.get("enrich_after_search") \
                and settings.GB_ENRICHMENT_ENABLED:
            job = GBJob(user_id=c.user_id, kind="enrich", campaign_id=c.id,
                        status="PENDING")
            db.add(job)
            db.commit()
            db.refresh(job)
            start_enrich(job.id)
    except Exception as e:  # noqa: BLE001 — job boundary
        logger.exception("gb search job %s crashed", campaign_id)
        try:
            c = db.query(SearchCampaign).filter(
                SearchCampaign.id == campaign_id).first()
            if c and c.status == "RUNNING":
                c.status = "FAILED"
                c.error = str(e)[:900]
                c.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# enrichment run (retryable GBJob)
# ---------------------------------------------------------------------------

def run_enrich(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.query(GBJob).filter(GBJob.id == job_id).first()
        if not job or job.status != "PENDING":
            return
        job.status = "RUNNING"
        job.started_at = datetime.utcnow()
        job.attempts += 1
        job.error = None
        db.commit()

        campaign = None
        if job.campaign_id:
            campaign = db.query(SearchCampaign).filter(
                SearchCampaign.id == job.campaign_id).first()

        if job.lead_ids_json:
            ids = _jloads(job.lead_ids_json, [])
            leads = db.query(BusinessLead).filter(
                BusinessLead.id.in_(ids),
                BusinessLead.user_id == job.user_id,
                # idempotent: re-running/retrying never re-crawls a finished
                # lead (matches the retry endpoint's contract)
                BusinessLead.enrichment_status != "ENRICHED").all()
        else:
            leads = db.query(BusinessLead).filter(
                BusinessLead.user_id == job.user_id,
                BusinessLead.campaign_id == job.campaign_id,
                BusinessLead.website.isnot(None),
                BusinessLead.enrichment_status != "ENRICHED").all()
        job.total = len(leads)
        db.commit()

        provider = get_places_provider()
        from app.gb_enricher import default_fetcher

        for lead in leads:
            status = _fresh_status(db, GBJob, job.id)
            if status in ("CANCELLED", "PAUSED", "COMPLETED", "FAILED"):
                return
            try:
                r = enrich_lead(lead, db, job.user_id,
                                fetcher=default_fetcher, provider=provider,
                                campaign=campaign)
                job.processed += 1
                if lead.enrichment_status == "ENRICHED":
                    job.enriched += 1
                else:
                    job.skipped += 1
                if r.get("errors"):
                    job.errors += 1
                if campaign is not None and provider.calls:
                    campaign.api_calls += provider.calls
                    campaign.total_enriched += 1
                    provider.calls = 0
                elif campaign is not None and lead.enrichment_status == "ENRICHED":
                    campaign.total_enriched += 1
            except Exception as e:  # noqa: BLE001 — per-lead boundary
                job.errors += 1
                job.processed += 1
                logger.warning("gb enrich lead %s failed: %s", lead.id, e)
                try:
                    lead.enrichment_status = "FAILED"
                    db.commit()
                except Exception:
                    db.rollback()
            db.commit()

        job.status = "COMPLETED"
        job.finished_at = datetime.utcnow()
        db.commit()
    except Exception as e:  # noqa: BLE001 — job boundary
        logger.exception("gb enrich job %s crashed", job_id)
        try:
            job = db.query(GBJob).filter(GBJob.id == job_id).first()
            if job and job.status == "RUNNING":
                job.status = "FAILED"
                job.error = str(e)[:900]
                job.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
