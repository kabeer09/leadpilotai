"""Companies + Leads: search, filters, CSV, duplicates, merge preview/apply."""
import csv
import io
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (User, Company, Contact, Lead, Source, CrawlJob,
                        EmailMessage, AuditEvent)
from app.deps import get_current_user, audit

router = APIRouter(prefix="/api", tags=["crm"])

LIFECYCLES = ("active", "EXCLUDED_NO_CONTACT", "ARCHIVED", "MERGED")


def _contact_map(db: Session, company_ids: list[int]):
    rows = db.query(Contact).filter(Contact.company_id.in_(company_ids)).all()
    out: dict[int, list] = {}
    for c in rows:
        out.setdefault(c.company_id, []).append(c)
    return out


@router.get("/companies")
def list_companies(
    search: str | None = None, country: str | None = None,
    category: str | None = None, source_id: int | None = None,
    has_email: bool | None = None, has_phone: bool | None = None,
    lifecycle: str = "active", lead_status: str | None = None,
    sort: str = "updated", order: str = "desc",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    q = db.query(Company).filter(Company.user_id == user.id)
    if lifecycle != "all":
        q = q.filter(Company.lifecycle == (lifecycle if lifecycle in LIFECYCLES else "active"))
    if search:
        like = f"%{search}%"
        q = q.filter(Company.name.ilike(like))
    if country:
        q = q.filter(Company.country == country)
    if category:
        q = q.filter(Company.category == category)
    if source_id:
        q = q.filter(Company.source_id == source_id)
    if lead_status:
        q = q.filter(Company.lead_status == lead_status)
    if has_email is True:
        q = q.filter(Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user.id, Contact.kind == "EMAIL")))
    if has_email is False:
        q = q.filter(~Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user.id, Contact.kind == "EMAIL")))
    if has_phone is True:
        q = q.filter(Company.id.in_(
            db.query(Contact.company_id).filter(
                Contact.user_id == user.id, Contact.kind.in_(["PHONE", "WHATSAPP"]))))
    col = {"name": Company.name, "updated": Company.updated_at,
           "country": Company.country}.get(sort, Company.updated_at)
    q = q.order_by(col.asc() if order == "asc" else col.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    cmap = _contact_map(db, [c.id for c in rows])
    items = []
    for c in rows:
        cs = cmap.get(c.id, [])
        emails = sorted({x.value for x in cs if x.kind == "EMAIL"})
        phones = sorted({x.value for x in cs if x.kind in ("PHONE", "WHATSAPP")})
        items.append({
            "id": c.id, "name": c.name, "product_summary": (c.product_summary or "")[:220],
            "country": c.country, "city": c.city, "website": c.website,
            "category": c.category, "lifecycle": c.lifecycle,
            "lead_status": c.lead_status, "confidence": c.confidence,
            "emails": emails, "phones": phones,
            "contact_complete": bool(emails or phones),
            "source_id": c.source_id, "updated_at": c.updated_at,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/companies/export")
def export_companies(lifecycle: str = "active", db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    q = db.query(Company).filter(Company.user_id == user.id)
    if lifecycle != "all":
        q = q.filter(Company.lifecycle == lifecycle)
    rows = q.order_by(Company.name).limit(5000).all()
    cmap = _contact_map(db, [c.id for c in rows])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "name", "product_summary", "country", "city", "website",
                "category", "emails", "phones", "lifecycle", "updated_at"])
    for c in rows:
        cs = cmap.get(c.id, [])
        w.writerow([c.id, c.name, c.product_summary or "", c.country or "",
                    c.city or "", c.website or "", c.category or "",
                    "; ".join(x.value for x in cs if x.kind == "EMAIL"),
                    "; ".join(x.value for x in cs if x.kind != "EMAIL"),
                    c.lifecycle, c.updated_at.isoformat() if c.updated_at else ""])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=companies.csv"})


@router.get("/companies/{company_id}")
def get_company(company_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    c = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Company not found")
    contacts = db.query(Contact).filter(Contact.company_id == c.id).all()
    leads = db.query(Lead).filter(Lead.user_id == user.id,
                                  Lead.company_id == c.id).all()
    lead_ids = [l.id for l in leads]
    emails = []
    if lead_ids:
        emails = db.query(EmailMessage).filter(
            EmailMessage.user_id == user.id,
            EmailMessage.lead_id.in_(lead_ids)).order_by(
            EmailMessage.created_at.desc()).limit(50).all()
    crawls = []
    if c.source_id:
        crawls = db.query(CrawlJob).filter(
            CrawlJob.user_id == user.id, CrawlJob.source_id == c.source_id).order_by(
            CrawlJob.id.desc()).limit(10).all()
    return {
        "id": c.id, "name": c.name, "product_summary": c.product_summary,
        "country": c.country, "city": c.city, "website": c.website,
        "category": c.category, "profile_url": c.profile_url,
        "lifecycle": c.lifecycle, "lead_status": c.lead_status,
        "confidence": c.confidence, "first_seen": c.first_seen,
        "last_verified": c.last_verified,
        "contacts": [{
            "id": x.id, "kind": x.kind, "value": x.value,
            "source_url": x.source_url, "source_page": x.source_page,
            "is_primary": x.is_primary, "verification": x.verification,
            "whatsapp_status": x.whatsapp_status} for x in contacts],
        "leads": [{"id": l.id, "status": l.status, "score": l.score} for l in leads],
        "email_history": [{
            "id": e.id, "to_email": e.to_email, "subject": e.subject,
            "status": e.status, "is_test": e.is_test, "is_followup": e.is_followup,
            "sent_at": e.sent_at} for e in emails],
        "crawl_history": [{
            "id": j.id, "status": j.status, "pages_visited": j.pages_visited,
            "finished_at": j.finished_at} for j in crawls],
    }


@router.get("/duplicates")
def duplicate_candidates(db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Candidate pairs sharing a normalized email/domain (review only, never auto-merge)."""
    emails = db.query(Contact.normalized).filter(
        Contact.user_id == user.id, Contact.kind == "EMAIL").distinct().all()
    out = []
    for (norm,) in emails:
        if not norm:
            continue
        owners = db.query(Contact.company_id).filter(
            Contact.user_id == user.id, Contact.normalized == norm).distinct().all()
        cids = sorted({o[0] for o in owners})
        if len(cids) > 1:
            names = db.query(Company.id, Company.name).filter(Company.id.in_(cids)).all()
            out.append({"shared_email": norm,
                        "companies": [{"id": i, "name": n} for i, n in names]})
        if len(out) >= 50:
            break
    return out


@router.post("/companies/merge-preview")
def merge_preview(payload: dict, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    keep_id, dup_id = payload.get("keep_id"), payload.get("duplicate_id")
    keep = db.query(Company).filter(
        Company.id == keep_id, Company.user_id == user.id).first()
    dup = db.query(Company).filter(
        Company.id == dup_id, Company.user_id == user.id).first()
    if not keep or not dup or keep_id == dup_id:
        raise HTTPException(status_code=422, detail="Invalid company pair")
    leads = db.query(Lead).filter(Lead.user_id == user.id,
                                  Lead.company_id == dup_id).count()
    msgs = 0
    if leads:
        lids = [l.id for l in db.query(Lead.id).filter(
            Lead.user_id == user.id, Lead.company_id == dup_id).all()]
        msgs = db.query(EmailMessage).filter(
            EmailMessage.user_id == user.id, EmailMessage.lead_id.in_(lids)).count()
    return {"keep": {"id": keep.id, "name": keep.name},
            "duplicate": {"id": dup.id, "name": dup.name},
            "leads_to_move": leads, "messages_preserved": msgs,
            "dry_run": True}


@router.post("/companies/merge")
def merge_companies(payload: dict, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Reversible merge: repoint leads/contacts, flag MERGED (never delete)."""
    keep_id, dup_id = payload.get("keep_id"), payload.get("duplicate_id")
    keep = db.query(Company).filter(
        Company.id == keep_id, Company.user_id == user.id).first()
    dup = db.query(Company).filter(
        Company.id == dup_id, Company.user_id == user.id).first()
    if not keep or not dup or keep_id == dup_id:
        raise HTTPException(status_code=422, detail="Invalid company pair")
    if dup.lifecycle == "MERGED":
        raise HTTPException(status_code=409, detail="Already merged")
    moved_leads = db.query(Lead).filter(
        Lead.user_id == user.id, Lead.company_id == dup_id).update({"company_id": keep_id})
    moved_contacts = db.query(Contact).filter(
        Contact.user_id == user.id, Contact.company_id == dup_id).update({"company_id": keep_id})
    for field in ("product_summary", "country", "city", "category"):
        if not getattr(keep, field) and getattr(dup, field):
            setattr(keep, field, getattr(dup, field))
    dup.lifecycle = "MERGED"
    dup.merged_into_id = keep_id
    audit(db, user.id, "merge", "company", dup_id,
          f"merged into {keep_id}: {moved_leads} leads, {moved_contacts} contacts moved")
    db.commit()
    return {"merged": dup_id, "canonical": keep_id,
            "leads_moved": moved_leads, "contacts_moved": moved_contacts}


# ---- Leads ----
@router.get("/leads")
def list_leads(
    status: str | None = None, search: str | None = None,
    followup_due: bool = False, suppressed: bool = False,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    from app.models import Suppression, Followup
    q = db.query(Lead).filter(Lead.user_id == user.id)
    if status:
        q = q.filter(Lead.status == status)
    if search:
        like = f"%{search}%"
        q = q.join(Company, Company.id == Lead.company_id).filter(Company.name.ilike(like))
    if followup_due:
        q = q.filter(Lead.next_followup_at.isnot(None),
                     Lead.next_followup_at <= datetime.utcnow())
    total = q.count()
    rows = q.order_by(Lead.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for l in rows:
        c = db.query(Company).filter(Company.id == l.company_id).first()
        contact = None
        if l.contact_id:
            contact = db.query(Contact).filter(Contact.id == l.contact_id).first()
        fu = db.query(Followup).filter(
            Followup.lead_id == l.id).order_by(Followup.id.desc()).first()
        sup = False
        if contact and contact.kind == "EMAIL":
            sup = db.query(Suppression).filter(
                Suppression.user_id == user.id,
                Suppression.value == contact.normalized).first() is not None
        if suppressed and not sup:
            continue
        items.append({
            "id": l.id, "status": l.status, "score": l.score,
            "company": c.name if c else None, "company_id": l.company_id,
            "country": c.country if c else None,
            "email": contact.value if contact else None,
            "last_contacted_at": l.last_contacted_at,
            "next_followup_at": l.next_followup_at,
            "followups": 1 if fu else 0,
            "suppressed": sup, "notes": (l.notes or "")[:200],
        })
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    from app.models import Suppression, Followup, ProviderEvent
    l = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Lead not found")
    c = db.query(Company).filter(Company.id == l.company_id).first()
    contacts = db.query(Contact).filter(Contact.company_id == l.company_id).all()
    msgs = db.query(EmailMessage).filter(
        EmailMessage.user_id == user.id, EmailMessage.lead_id == l.id).order_by(
        EmailMessage.created_at.asc()).all()
    out_msgs = []
    for m in msgs:
        evts = db.query(ProviderEvent).filter(
            ProviderEvent.message_id == m.id).order_by(
            ProviderEvent.created_at.asc()).all()
        out_msgs.append({
            "id": m.id, "to_email": m.to_email, "subject": m.subject,
            "status": m.status, "provider": m.provider,
            "provider_message_id": m.provider_message_id,
            "is_test": m.is_test, "is_followup": m.is_followup,
            "error": m.error, "sent_at": m.sent_at, "created_at": m.created_at,
            "events": [{"type": e.event_type, "at": e.created_at} for e in evts]})
    fus = db.query(Followup).filter(Followup.lead_id == l.id).order_by(
        Followup.scheduled_at.asc()).all()
    notes = db.query(AuditEvent).filter(
        AuditEvent.user_id == user.id, AuditEvent.entity == "lead",
        AuditEvent.entity_id == l.id).order_by(AuditEvent.created_at.asc()).all()
    supps = db.query(Suppression).filter(Suppression.user_id == user.id).all()
    sup_vals = {s.value for s in supps}
    return {
        "id": l.id, "status": l.status, "score": l.score, "notes": l.notes,
        "last_contacted_at": l.last_contacted_at, "next_followup_at": l.next_followup_at,
        "company": {"id": c.id, "name": c.name, "country": c.country,
                    "website": c.website} if c else None,
        "contacts": [{
            "id": x.id, "kind": x.kind, "value": x.value,
            "source_url": x.source_url, "verification": x.verification,
            "suppressed": x.normalized in sup_vals} for x in contacts],
        "messages": out_msgs,
        "followups": [{
            "id": f.id, "scheduled_at": f.scheduled_at, "status": f.status,
            "skip_reason": f.skip_reason, "sent_at": f.sent_at} for f in fus],
        "timeline": [{
            "at": n.created_at, "kind": "note",
            "text": n.detail} for n in notes],
    }


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: int, payload: dict, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    l = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Lead not found")
    if "status" in payload and payload["status"] in (
            "NEW", "CONTACTED", "REPLIED", "QUALIFIED", "ARCHIVED"):
        l.status = payload["status"]
    if "notes" in payload and isinstance(payload["notes"], str):
        l.notes = payload["notes"][:5000]
        audit(db, user.id, "lead_note", "lead", l.id, payload["notes"][:200])
    db.commit()
    return {"id": l.id, "status": l.status}
