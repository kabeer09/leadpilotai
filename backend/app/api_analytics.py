"""Analytics: overview cards + trends. Every number from live tenant queries."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (User, Company, Contact, Lead, CrawlJob, EmailMessage,
                        ProviderEvent, Followup)
from app.deps import get_current_user

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/overview")
def overview(days: int = 30, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))
    companies = db.query(func.count(Company.id)).filter(
        Company.user_id == user.id, Company.lifecycle != "MERGED").scalar() or 0
    with_email = db.query(func.count(func.distinct(Contact.company_id))).filter(
        Contact.user_id == user.id, Contact.kind == "EMAIL").scalar() or 0
    with_phone = db.query(func.count(func.distinct(Contact.company_id))).filter(
        Contact.user_id == user.id, Contact.kind.in_(["PHONE", "WHATSAPP"])).scalar() or 0
    wa_identified = db.query(func.count(Contact.id)).filter(
        Contact.user_id == user.id, Contact.kind == "WHATSAPP",
        Contact.whatsapp_status == "identified").scalar() or 0
    qualified = db.query(func.count(Lead.id)).filter(
        Lead.user_id == user.id, Lead.status == "QUALIFIED").scalar() or 0
    new_cos = db.query(func.count(Company.id)).filter(
        Company.user_id == user.id, Company.created_at >= since).scalar() or 0
    attempted = db.query(func.count(EmailMessage.id)).filter(
        EmailMessage.user_id == user.id, EmailMessage.is_test.is_(False)).scalar() or 0
    delivered = db.query(func.count(EmailMessage.id)).filter(
        EmailMessage.user_id == user.id, EmailMessage.status == "delivered",
        EmailMessage.is_test.is_(False)).scalar() or 0
    replies = db.query(func.count(ProviderEvent.id)).filter(
        ProviderEvent.user_id == user.id,
        ProviderEvent.event_type == "reply").scalar() or 0
    bounces = db.query(func.count(EmailMessage.id)).filter(
        EmailMessage.user_id == user.id, EmailMessage.status == "bounced").scalar() or 0
    bounces += db.query(func.count(ProviderEvent.id)).filter(
        ProviderEvent.user_id == user.id,
        ProviderEvent.event_type == "bounce").scalar() or 0
    failed = db.query(func.count(EmailMessage.id)).filter(
        EmailMessage.user_id == user.id, EmailMessage.status == "failed").scalar() or 0
    due = db.query(func.count(Followup.id)).filter(
        Followup.user_id == user.id, Followup.status == "SCHEDULED",
        Followup.scheduled_at <= datetime.utcnow()).scalar() or 0
    crawl_ok = db.query(func.count(CrawlJob.id)).filter(
        CrawlJob.user_id == user.id, CrawlJob.status == "COMPLETED").scalar() or 0
    crawl_fail = db.query(func.count(CrawlJob.id)).filter(
        CrawlJob.user_id == user.id,
        CrawlJob.status.in_(["FAILED", "CANCELLED"])).scalar() or 0
    recent = db.query(ProviderEvent).filter(
        ProviderEvent.user_id == user.id).order_by(
        ProviderEvent.id.desc()).limit(10).all()
    return {
        "companies": companies, "with_email": with_email, "with_phone": with_phone,
        "whatsapp_identified": wa_identified, "qualified_leads": qualified,
        "new_companies": new_cos, "emails_attempted": attempted,
        "delivered": delivered, "replies": replies,
        "bounces": bounces, "failed": failed, "followups_due": due,
        "crawls_ok": crawl_ok, "crawls_failed": crawl_fail,
        "recent_activity": [{
            "type": e.event_type, "message_id": e.message_id,
            "at": e.created_at} for e in recent],
    }


@router.get("/trends")
def trends(days: int = 30, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    days = max(1, min(days, 365))
    since = datetime.utcnow() - timedelta(days=days)

    def per_day(model, date_col, extra=()):
        rows = db.query(func.date(date_col).label("d"),
                        func.count(model.id)).filter(
            model.user_id == user.id, date_col >= since, *extra
        ).group_by(func.date(date_col)).all()
        return {str(d): c for d, c in rows}

    from app.models import Company as _C, EmailMessage as _E, Lead as _L
    discovered = per_day(_C, _C.created_at)
    sent = per_day(_E, _E.created_at, (_E.is_test.is_(False),))
    by_country = dict(db.query(Company.country, func.count(Company.id)).filter(
        Company.user_id == user.id).group_by(Company.country).all())
    out = []
    for i in range(days):
        day = (datetime.utcnow() - timedelta(days=days - 1 - i)).date().isoformat()
        out.append({"date": day, "discovered": discovered.get(day, 0),
                    "sent": sent.get(day, 0)})
    return {"daily": out,
            "by_country": [{"country": k or "Unknown", "leads": v}
                           for k, v in sorted(by_country.items(),
                                              key=lambda x: -x[1])[:15]]}
