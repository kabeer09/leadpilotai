"""Exhibitor Dashboard API — extraction job status, search, export, launch.

Extends the existing crawl machinery (app.crawler + Source/CrawlJob) rather
than duplicating a second job engine. Fair sources are matched against the
capability registry (app.source_registry) so launches are honest: fairs the
registry marks non-automatable are refused with the registry's reason and
fallback instead of starting a crawl that is guaranteed to fail.

Endpoints (prefix /api/exhibitors):
  GET  /            searchable exhibitor dashboard (hall/booth/fair/status)
  GET  /overview    per-fair registry status + extraction job counts
  GET  /export      CSV with source-attribution header
  POST /extract     launch an extraction for a registered fair
"""
from datetime import datetime
from urllib.parse import urlparse

import csv
import io
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, false
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Company, Contact, Source, CrawlJob
from app.deps import get_current_user, audit, rate_limit
from app.config import settings
from app.ssrf import validate_url
from app import crawler
from app.source_registry import SOURCES, get_all_sources

router = APIRouter(prefix="/api/exhibitors", tags=["exhibitors"])

RECORD_TYPE = "fair_directory_listing"
DISCLAIMER = (
    "Exhibitor listing from the named trade-fair directory. Participation "
    "indicates presence at that fair — NOT current buying/selling activity "
    "and not verified contactability. Verify before outreach."
)

# CrawlJob status -> dashboard label (Pending/Running/Completed/Failed…)
STATUS_LABEL = {
    "PENDING": "Pending",
    "CRAWLING": "Running",
    "COMPLETED": "Completed",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
}
STATUS_ORDER = ["Pending", "Running", "Completed", "Failed", "Cancelled"]


def _label(status: str | None) -> str:
    return STATUS_LABEL.get((status or "").upper(), "Not run")


def _host(url: str) -> str:
    h = (urlparse(url).netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def _find_registry(url: str) -> dict | None:
    host = _host(url)
    if not host:
        return None
    for s in SOURCES:
        bh = _host(s["base_url"])
        if host == bh or host.endswith("." + bh) or bh.endswith("." + host):
            return s
    return None


def _jobs_by_source(db: Session, user_id: int) -> dict[int, CrawlJob]:
    """Latest crawl job per source (extraction status of its exhibitors)."""
    rows = (db.query(CrawlJob)
            .filter(CrawlJob.user_id == user_id)
            .order_by(CrawlJob.id.desc()).all())
    out: dict[int, CrawlJob] = {}
    for j in rows:
        if j.source_id and j.source_id not in out:
            out[j.source_id] = j
    return out


def _contact_counts(db: Session, company_ids: list[int]) -> dict[int, dict]:
    rows = db.query(Contact).filter(Contact.company_id.in_(company_ids)).all()
    out: dict[int, dict] = {}
    for c in rows:
        cell = out.setdefault(c.company_id, {"emails": set(), "phones": set()})
        if c.kind == "EMAIL":
            cell["emails"].add(c.value)
        elif c.kind in ("PHONE", "WHATSAPP"):
            cell["phones"].add(c.value)
    return out


# ---------------------------------------------------------------------------
# GET /  — searchable exhibitor dashboard
# ---------------------------------------------------------------------------

def _exhibitor_query(db, user, *, q=None, source_id=None, hall=None,
                     country=None, has_email=None, has_phone=None,
                     status=None, jobs: dict[int, CrawlJob] | None = None):
    qry = (db.query(Company)
           .filter(Company.user_id == user.id,
                   Company.lifecycle == "active",
                   (Company.source_id.isnot(None)) |
                   (Company.hall.isnot(None)) |
                   (Company.booth.isnot(None))))
    if q:
        like = f"%{q}%"
        qry = qry.filter(Company.name.ilike(like) |
                         Company.product_summary.ilike(like) |
                         Company.category.ilike(like) |
                         Company.hall.ilike(like) |
                         Company.booth.ilike(like))
    if source_id:
        qry = qry.filter(Company.source_id == source_id)
    if hall:
        qry = qry.filter(Company.hall.ilike(f"%{hall}%"))
    if country:
        qry = qry.filter(Company.country == country)
    if has_email is True:
        qry = qry.filter(Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user.id, Contact.kind == "EMAIL")))
    if has_email is False:
        qry = qry.filter(~Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user.id, Contact.kind == "EMAIL")))
    if has_phone is True:
        qry = qry.filter(Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user.id,
                Contact.kind.in_(["PHONE", "WHATSAPP"]))))
    if has_phone is False:
        qry = qry.filter(~Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user.id,
                Contact.kind.in_(["PHONE", "WHATSAPP"]))))
    if status:
        jobs = jobs if jobs is not None else _jobs_by_source(db, user.id)
        all_src = {s.id for s in db.query(Source.id).filter(
            Source.user_id == user.id).all()}
        if status == "Not run":
            wanted = all_src - set(jobs.keys())
            cond = (Company.source_id.is_(None))
            if wanted:
                cond = cond | (Company.source_id.in_(list(wanted)))
        else:
            wanted = {sid for sid, j in jobs.items()
                      if _label(j.status) == status}
            cond = (Company.source_id.in_(list(wanted))
                    if wanted else false())
        qry = qry.filter(cond)
    return qry


def _item_out(c, smap, ccounts, jobs) -> dict:
    job = jobs.get(c.source_id) if c.source_id else None
    cc = ccounts.get(c.id, {"emails": set(), "phones": set()})
    try:
        products = json.loads(c.products_json or "[]")
    except Exception:
        products = []
    return {
        "id": c.id, "name": c.name, "country": c.country,
        "city": c.city, "website": c.website, "category": c.category,
        "hall": c.hall, "booth": c.booth, "address": c.address,
        "product_summary": (c.product_summary or "")[:220],
        "products": products if isinstance(products, list) else [],
        "profile_url": c.profile_url,
        "source_id": c.source_id,
        "fair": smap[c.source_id].name if c.source_id in smap else None,
        "extraction_status": _label(job.status) if job else "Not run",
        "extraction_finished_at": job.finished_at if job else None,
        "emails": sorted(cc["emails"]), "phones": sorted(cc["phones"]),
        "lead_status": c.lead_status, "updated_at": c.updated_at,
    }


@router.get("")
def list_exhibitors(
    q: str | None = None, source_id: int | None = None,
    hall: str | None = None, country: str | None = None,
    has_email: bool | None = None, has_phone: bool | None = None,
    status: str | None = None,          # extraction status filter label
    sort: str = "name", order: str = "asc",
    page: int = 1, page_size: int = 25,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    jobs = _jobs_by_source(db, user.id)
    qry = _exhibitor_query(db, user, q=q, source_id=source_id, hall=hall,
                           country=country, has_email=has_email,
                           has_phone=has_phone, status=status, jobs=jobs)
    col = {"name": Company.name, "hall": Company.hall,
           "country": Company.country, "updated": Company.updated_at
           }.get(sort, Company.name)
    qry = qry.order_by(col.asc() if order == "asc" else col.desc())
    total = qry.count()
    rows = qry.offset((page - 1) * page_size).limit(page_size).all()

    smap = {s.id: s for s in db.query(Source).filter(
        Source.user_id == user.id).all()}
    ccounts = _contact_counts(db, [c.id for c in rows])
    return {
        "record_type": RECORD_TYPE,
        "source": {
            "name": "Trade-fair exhibitor directories (crawled)",
            "record_type": RECORD_TYPE,
            "coverage": "companies in this workspace linked to a crawled "
                        "fair/directory source",
        },
        "disclaimer": DISCLAIMER,
        "total": total, "page": page, "page_size": page_size,
        "status_labels": STATUS_ORDER + ["Not run"],
        "items": [_item_out(c, smap, ccounts, jobs) for c in rows],
    }


# ---------------------------------------------------------------------------
# GET /overview  — registry + job status per fair
# ---------------------------------------------------------------------------

def _registry_for_source(src: Source) -> dict | None:
    return _find_registry(src.url)


@router.get("/overview")
def overview(db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    sources = db.query(Source).filter(Source.user_id == user.id).all()
    jobs = (db.query(CrawlJob).filter(CrawlJob.user_id == user.id)
            .order_by(CrawlJob.id.desc()).all())

    # group sources + jobs by registry entry
    reg_groups: dict[str, dict] = {}
    for reg in get_all_sources():
        reg_groups[reg["id"]] = {"registry": {
            "id": reg["id"], "name": reg["name"],
            "base_url": reg["base_url"], "status": reg["status"].value,
            "access_method": reg["access_method"].value,
            "automation_permitted": reg["automation_permitted"],
            "credentials": reg["credentials"], "notes": reg["notes"],
            "fallback": reg["fallback"],
        }, "sources": [], "jobs": []}
    unregistered = {"sources": [], "jobs": []}

    for s in sources:
        reg = _registry_for_source(s)
        bucket = reg_groups[reg["id"]] if reg else unregistered
        bucket["sources"].append(s)
    for j in jobs:
        if j.source_id is None:
            continue
        src = next((s for s in sources if s.id == j.source_id), None)
        if src is None:
            continue
        reg = _registry_for_source(src)
        bucket = reg_groups[reg["id"]] if reg else unregistered
        bucket["jobs"].append(j)

    def _bucket_stats(bucket) -> dict:
        src_ids = [s.id for s in bucket["sources"]]
        stats = {"sources": [{
            "id": s.id, "name": s.name, "url": s.url, "status": s.status,
            "created_at": s.created_at} for s in bucket["sources"]],
            "jobs_by_status": {k: 0 for k in STATUS_ORDER},
            "exhibitors": 0, "with_email": 0, "with_phone": 0,
            "last_extraction": None,
        }
        for j in bucket["jobs"]:
            stats["jobs_by_status"][_label(j.status)] += 1
        if src_ids:
            stats["exhibitors"] = db.query(func.count(Company.id)).filter(
                Company.user_id == user.id, Company.lifecycle == "active",
                Company.source_id.in_(src_ids)).scalar() or 0
            stats["with_email"] = db.query(func.count(Company.id)).filter(
                Company.user_id == user.id, Company.lifecycle == "active",
                Company.source_id.in_(src_ids),
                Company.id.in_(db.query(Contact.company_id).filter(
                    Contact.user_id == user.id,
                    Contact.kind == "EMAIL"))).scalar() or 0
            stats["with_phone"] = db.query(func.count(Company.id)).filter(
                Company.user_id == user.id, Company.lifecycle == "active",
                Company.source_id.in_(src_ids),
                Company.id.in_(db.query(Contact.company_id).filter(
                    Contact.user_id == user.id,
                    Contact.kind.in_(["PHONE", "WHATSAPP"])))) \
                .scalar() or 0
        if bucket["jobs"]:
            j = bucket["jobs"][0]  # already id-desc
            stats["last_extraction"] = {
                "job_id": j.id, "status": _label(j.status),
                "url": j.url, "pages_visited": j.pages_visited,
                "companies_found": j.companies_found,
                "contacts_found": j.contacts_found,
                "started_at": j.started_at, "finished_at": j.finished_at,
                "errors": (j.errors or "").split("\n")[:3] if j.errors else [],
            }
        stats["recent_jobs"] = [{
            "job_id": j.id, "status": _label(j.status), "url": j.url,
            "pages_visited": j.pages_visited,
            "companies_found": j.companies_found,
            "finished_at": j.finished_at} for j in bucket["jobs"][:3]]
        return stats

    fairs = []
    for reg in reg_groups.values():
        st = _bucket_stats(reg)
        fairs.append({**reg["registry"], **{
            "can_extract": bool(reg["registry"]["automation_permitted"]),
            **st}})
    total_jobs = {k: 0 for k in STATUS_ORDER}
    for f in fairs:
        for k, v in f["jobs_by_status"].items():
            total_jobs[k] += v
    unreg_stats = _bucket_stats(unregistered)
    for k, v in unreg_stats["jobs_by_status"].items():
        total_jobs[k] += v
    return {
        "record_type": "exhibitor_extraction_overview",
        "disclaimer": DISCLAIMER,
        "summary": {
            "fairs_registered": len(fairs),
            "fairs_with_data": sum(1 for f in fairs if f["sources"]),
            "exhibitors_total": sum(f["exhibitors"] for f in fairs)
                                 + unreg_stats["exhibitors"],
            "jobs_by_status": total_jobs,
        },
        "fairs": fairs,
        "unregistered_sources": unreg_stats,
    }


# ---------------------------------------------------------------------------
# GET /export
# ---------------------------------------------------------------------------

@router.get("/export")
def export_exhibitors(q: str | None = None, source_id: int | None = None,
                      hall: str | None = None, country: str | None = None,
                      has_email: bool | None = None,
                      has_phone: bool | None = None,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    jobs = _jobs_by_source(db, user.id)
    qry = _exhibitor_query(db, user, q=q, source_id=source_id, hall=hall,
                           country=country, has_email=has_email,
                           has_phone=has_phone, jobs=jobs)
    rows = qry.order_by(Company.name.asc()).limit(5000).all()
    smap = {s.id: s for s in db.query(Source).filter(
        Source.user_id == user.id).all()}
    ccounts = _contact_counts(db, [c.id for c in rows])
    items = [_item_out(c, smap, ccounts, jobs) for c in rows]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Source: Trade-fair exhibitor directories (crawled by "
                "LeadPilot; capability registry in-app)"])
    w.writerow(["# Record type: fair_directory_listing"])
    w.writerow([f"# Retrieved: {datetime.utcnow().isoformat()}Z"])
    w.writerow([f"# Rows: {len(items)}"])
    w.writerow(["# Disclaimer: Exhibitor presence at a fair is NOT "
                "evidence of current buying/selling activity or verified "
                "contactability. Verify before outreach."])
    w.writerow(["ID", "Name", "Fair/Source", "Extraction Status", "Hall",
                "Booth", "Country", "City", "Category", "Website",
                "Profile URL", "Products", "Emails", "Phones",
                "Product Summary", "Updated At"])
    for r in items:
        w.writerow([
            r["id"], r["name"], r["fair"] or "", r["extraction_status"],
            r["hall"] or "", r["booth"] or "", r["country"] or "",
            r["city"] or "", r["category"] or "", r["website"] or "",
            r["profile_url"] or "",
            "; ".join(str(p.get("name", "")) if isinstance(p, dict)
                      else str(p) for p in r["products"]),
            "; ".join(r["emails"]), "; ".join(r["phones"]),
            r["product_summary"],
            r["updated_at"].isoformat() if r["updated_at"] else "",
        ])
    buf.seek(0)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=exhibitors-{stamp}.csv"})


# ---------------------------------------------------------------------------
# POST /extract  — launch extraction for a registered fair
# ---------------------------------------------------------------------------

class ExtractIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    max_pages: int = Field(default=50, ge=1, le=500)


@router.post("/extract", status_code=201,
             dependencies=[Depends(rate_limit(
                 "exhibitor_extract", settings.RATE_SENSITIVE_PER_MIN))])
def extract(payload: ExtractIn, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    from datetime import datetime as _dt
    url = payload.url.strip()
    _, err = validate_url(url)
    if err:
        raise HTTPException(status_code=422, detail=f"URL not allowed: {err}")

    reg = _find_registry(url)
    if reg is None:
        hosts = ", ".join(sorted({_host(s["base_url"]) for s in SOURCES}))
        raise HTTPException(
            status_code=422,
            detail="URL is not a registered trade-fair source. Registered "
                   f"fairs: {hosts}. Use Website Crawling for other URLs.")
    if not reg["automation_permitted"]:
        raise HTTPException(
            status_code=422,
            detail=f"{reg['name']} cannot be automated: {reg['notes']} "
                   f"Recommended: {reg['fallback']}.")

    day = _dt.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    from sqlalchemy import func as _f
    used = _f.coalesce(_f.sum(CrawlJob.pages_visited), 0)
    pages_today = (db.query(used).filter(
        CrawlJob.user_id == user.id, CrawlJob.created_at >= day).scalar()
        or 0)
    if pages_today >= settings.DAILY_CRAWL_PAGES:
        raise HTTPException(status_code=429,
                            detail="Daily crawl quota reached")

    source = Source(user_id=user.id, url=url,
                    name=f"{reg['name']} extraction", status="CRAWLING")
    db.add(source)
    db.flush()
    job = CrawlJob(user_id=user.id, source_id=source.id, url=url,
                   max_pages=min(payload.max_pages, settings.MAX_CRAWL_PAGES),
                   status="PENDING")
    db.add(job)
    db.commit()
    db.refresh(job)
    audit(db, user.id, "exhibitor_extract_start", "crawl_job", job.id,
          f"{reg['id']}: {url[:200]}")
    db.commit()
    crawler.start_job(job.id)
    return {"job_id": job.id, "status": "Pending", "url": url,
            "fair": reg["name"], "registry_status": reg["status"].value,
            "source_id": source.id}
