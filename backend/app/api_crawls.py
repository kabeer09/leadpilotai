"""Crawl routes: create/list/get/cancel/retry. Tenant-scoped, quota-guarded."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Source, CrawlJob
from app.schemas import CrawlCreate
from app.deps import get_current_user, rate_limit, audit
from app.config import settings
from app.ssrf import validate_url
from app import crawler

router = APIRouter(prefix="/api/crawls", tags=["crawls"])


def _pages_used_today(db: Session, user_id: int) -> int:
    from sqlalchemy import func
    day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.coalesce(func.sum(CrawlJob.pages_visited), 0)).filter(
        CrawlJob.user_id == user_id, CrawlJob.created_at >= day).scalar() or 0


@router.post("", status_code=201,
             dependencies=[Depends(rate_limit("crawl", settings.RATE_SENSITIVE_PER_MIN))])
def create_crawl(payload: CrawlCreate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    if _pages_used_today(db, user.id) >= settings.DAILY_CRAWL_PAGES:
        raise HTTPException(status_code=429, detail="Daily crawl quota reached")
    _, err = validate_url(payload.url)
    if err:
        raise HTTPException(status_code=422, detail=f"URL not allowed: {err}")
    source = Source(user_id=user.id, url=payload.url,
                    name=payload.name or payload.url, status="CRAWLING")
    db.add(source)
    db.flush()
    job = CrawlJob(user_id=user.id, source_id=source.id, url=payload.url,
                   max_pages=min(payload.max_pages, settings.MAX_CRAWL_PAGES),
                   max_depth=payload.max_depth, status="PENDING")
    db.add(job)
    db.commit()
    db.refresh(job)
    audit(db, user.id, "crawl_start", "crawl_job", job.id, payload.url[:200])
    db.commit()
    crawler.start_job(job.id)
    return {"id": job.id, "status": job.status, "url": job.url}


@router.get("")
def list_crawls(limit: int = 50, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    jobs = (db.query(CrawlJob).filter(CrawlJob.user_id == user.id)
            .order_by(CrawlJob.id.desc()).limit(min(limit, 100)).all())
    return [{
        "id": j.id, "url": j.url, "status": j.status,
        "pages_visited": j.pages_visited, "companies_found": j.companies_found,
        "contacts_found": j.contacts_found,
        "started_at": j.started_at, "finished_at": j.finished_at,
        "errors": (j.errors or "").split("\n")[:5] if j.errors else [],
    } for j in jobs]


@router.get("/{job_id}")
def get_crawl(job_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    job = db.query(CrawlJob).filter(
        CrawlJob.id == job_id, CrawlJob.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Crawl not found")
    return {
        "id": job.id, "url": job.url, "status": job.status,
        "max_pages": job.max_pages, "max_depth": job.max_depth,
        "pages_visited": job.pages_visited, "companies_found": job.companies_found,
        "contacts_found": job.contacts_found,
        "started_at": job.started_at, "finished_at": job.finished_at,
        "errors": (job.errors or "").split("\n") if job.errors else [],
        "quota_pages_used_today": _pages_used_today(db, user.id),
        "quota_pages_daily": settings.DAILY_CRAWL_PAGES,
    }


@router.post("/{job_id}/cancel")
def cancel_crawl(job_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    job = db.query(CrawlJob).filter(
        CrawlJob.id == job_id, CrawlJob.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Crawl not found")
    if job.status in ("COMPLETED", "FAILED", "CANCELLED"):
        return {"id": job.id, "status": job.status}
    crawler.cancel_job(job.id)
    job.status = "CANCELLED"
    job.finished_at = datetime.utcnow()
    audit(db, user.id, "crawl_cancel", "crawl_job", job.id)
    db.commit()
    return {"id": job.id, "status": job.status}
