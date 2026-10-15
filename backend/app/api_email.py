"""Campaigns, email activity, follow-ups, unsubscribe, provider events."""
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (User, Lead, Contact, Company, Campaign, CampaignRecipient,
                        EmailMessage, ProviderEvent, Followup, Suppression)
from app.schemas import CampaignCreate, CampaignSend, TestSend
from app.deps import get_current_user, rate_limit, audit
from app.config import settings
from app.emailing import (get_provider, normalize_email, is_suppressed,
                          previously_contacted, personalize, send_campaign,
                          process_due_followups, emails_sent_today)

router = APIRouter(prefix="/api", tags=["email"])


def _build_recipients(db: Session, user: User, campaign: Campaign,
                      lead_ids: list[int], allow_resend: bool = False) -> dict:
    added = skipped_suppressed = skipped_contacted = skipped_noemail = 0
    for lid in dict.fromkeys(lead_ids):
        lead = db.query(Lead).filter(
            Lead.id == lid, Lead.user_id == user.id).first()
        if not lead:
            continue
        contact = None
        if lead.contact_id:
            contact = db.query(Contact).filter(Contact.id == lead.contact_id).first()
        if not contact or contact.kind != "EMAIL":
            contact = db.query(Contact).filter(
                Contact.company_id == lead.company_id,
                Contact.kind == "EMAIL").order_by(
                Contact.is_primary.desc()).first()
        if not contact:
            skipped_noemail += 1
            continue
        email = normalize_email(contact.value)
        if is_suppressed(db, user.id, email):
            skipped_suppressed += 1
            continue
        if not allow_resend and previously_contacted(db, user.id, email):
            skipped_contacted += 1
            continue
        key = f"{campaign.id}:{lead.id}:{email}"
        exists = db.query(CampaignRecipient).filter(
            CampaignRecipient.idempotency_key == key).first()
        if exists:
            continue
        db.add(CampaignRecipient(user_id=user.id, campaign_id=campaign.id,
                                 lead_id=lead.id, email=email,
                                 idempotency_key=key))
        added += 1
    db.commit()
    return {"added": added, "skipped_suppressed": skipped_suppressed,
            "skipped_contacted": skipped_contacted, "skipped_noemail": skipped_noemail}


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Campaign).filter(Campaign.user_id == user.id).order_by(
        Campaign.id.desc()).all()
    return [{"id": c.id, "name": c.name, "status": c.status,
             "followup_enabled": c.followup_enabled,
             "created_at": c.created_at} for c in rows]


@router.post("/campaigns", status_code=201)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    c = Campaign(user_id=user.id, name=payload.name, subject=payload.subject,
                 body=payload.body, followup_enabled=payload.followup_enabled,
                 followup_delay_hours=payload.followup_delay_hours)
    db.add(c)
    db.commit()
    db.refresh(c)
    info = _build_recipients(db, user, c, payload.lead_ids)
    audit(db, user.id, "campaign_create", "campaign", c.id, payload.name[:200])
    db.commit()
    return {"id": c.id, "status": c.status, **info}


@router.get("/campaigns/{campaign_id}/preview")
def campaign_preview(campaign_id: int, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    c = db.query(Campaign).filter(
        Campaign.id == campaign_id, Campaign.user_id == user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    recs = db.query(CampaignRecipient).filter(
        CampaignRecipient.campaign_id == c.id).all()
    rows = []
    for r in recs:
        lead = db.query(Lead).filter(Lead.id == r.lead_id).first()
        company = db.query(Company).filter(
            Company.id == lead.company_id).first() if lead else None
        rows.append({
            "lead_id": r.lead_id,
            "company": company.name if company else "",
            "to_email": r.email, "status": r.status,
            "subject": personalize(c.subject, company.name if company else "",
                                   company.country if company else "", r.email),
            "body": personalize(c.body, company.name if company else "",
                                company.country if company else "", r.email),
        })
    return {"id": c.id, "name": c.name, "status": c.status,
            "provider": get_provider().name,
            "provider_connected": (
                True if settings.EMAIL_PROVIDER == "dryrun"
                else bool((settings.SMTP_HOST and settings.SMTP_FROM)
                          or (settings.RESEND_API_KEY and settings.RESEND_FROM))),
            "recipient_count": len(rows), "recipients": rows,
            "daily_quota": {"used": emails_sent_today(db, user.id),
                            "limit": settings.DAILY_EMAIL_LIMIT}}


@router.post("/campaigns/{campaign_id}/test")
def campaign_test(campaign_id: int, payload: TestSend,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = db.query(Campaign).filter(
        Campaign.id == campaign_id, Campaign.user_id == user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    to_email = str(payload.to_email or user.email)
    from app.emailing import get_provider_for_user
    provider = get_provider_for_user(user)
    subject = personalize(c.subject, "Test Company", "", to_email)
    body = personalize(c.body, "Test Company", "", to_email)
    result = provider.send(to_email, subject, body)
    msg = EmailMessage(user_id=user.id, campaign_id=c.id, to_email=to_email,
                       subject=subject, body=body,
                       status="accepted" if result.get("ok") else "failed",
                       provider=provider.name, is_test=True,
                       provider_message_id=result.get("provider_message_id"),
                       error=None if result.get("ok") else result.get("error"),
                       sent_at=None)
    if result.get("ok"):
        from datetime import datetime
        msg.sent_at = datetime.utcnow()
    db.add(msg)
    audit(db, user.id, "campaign_test", "campaign", c.id, to_email)
    db.commit()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error"))
    return {"message": "Test email processed", "to": to_email,
            "provider": provider.name, "dry_run": provider.name == "dryrun"}


@router.post("/campaigns/{campaign_id}/send")
def campaign_send(campaign_id: int, payload: CampaignSend,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Live send. Requires DOUBLE confirmation: confirm=true AND the exact
    preview recipient count. No confirmation → 422, nothing sends."""
    c = db.query(Campaign).filter(
        Campaign.id == campaign_id, Campaign.user_id == user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if c.status not in ("DRAFT",):
        raise HTTPException(status_code=409, detail=f"Campaign already {c.status}")
    pending = db.query(CampaignRecipient).filter(
        CampaignRecipient.campaign_id == c.id,
        CampaignRecipient.status == "PENDING").count()
    if not payload.confirm or payload.recipient_count_ack != pending:
        raise HTTPException(
            status_code=422,
            detail=(f"Explicit confirmation required: resend with "
                    f'{{"confirm": true, "recipient_count_ack": {pending}}}'))
    c.status = "SENDING"
    db.commit()
    audit(db, user.id, "campaign_send", "campaign", c.id, f"{pending} recipients")
    result = send_campaign(db, user, c)
    db.commit()
    return {"campaign_id": c.id, "status": c.status, **result}


@router.get("/email-events")
def email_events(status: str | None = None, limit: int = Query(50, ge=1, le=200),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(EmailMessage).filter(EmailMessage.user_id == user.id)
    if status:
        q = q.filter(EmailMessage.status == status)
    rows = q.order_by(EmailMessage.id.desc()).limit(limit).all()
    return [{
        "id": m.id, "to_email": m.to_email, "subject": m.subject,
        "status": m.status, "provider": m.provider,
        "provider_message_id": m.provider_message_id,
        "is_test": m.is_test, "is_followup": m.is_followup,
        "error": m.error, "sent_at": m.sent_at, "created_at": m.created_at,
        "campaign_id": m.campaign_id, "lead_id": m.lead_id} for m in rows]


@router.post("/email-events/inbound")
def inbound_event(payload: dict, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Record reply/bounce/unsubscribe (provider webhook or manual entry).

    Replies + unsubscribes stop follow-ups; bounces/unsubscribes suppress.
    """
    etype = (payload.get("event_type") or "").lower()
    if etype not in ("reply", "bounce", "unsubscribe", "delivered"):
        raise HTTPException(status_code=422, detail="event_type must be reply|bounce|unsubscribe|delivered")
    email = normalize_email(payload.get("email") or "")
    if "@" not in email:
        raise HTTPException(status_code=422, detail="valid email required")
    msg = db.query(EmailMessage).filter(
        EmailMessage.user_id == user.id,
        EmailMessage.to_email == email).order_by(
        EmailMessage.id.desc()).first()
    if msg:
        db.add(ProviderEvent(user_id=user.id, message_id=msg.id,
                             event_type=etype, payload=str(payload)[:1000]))
        if etype == "reply":
            msg.status = "replied"
            if msg.lead_id:
                lead = db.query(Lead).filter(Lead.id == msg.lead_id).first()
                if lead:
                    lead.status = "REPLIED"
                    lead.next_followup_at = None
                for fu in db.query(Followup).filter(
                        Followup.lead_id == msg.lead_id,
                        Followup.status == "SCHEDULED").all():
                    fu.status = "SKIPPED"
                    fu.skip_reason = "reply"
        elif etype in ("bounce", "unsubscribe"):
            msg.status = "bounced" if etype == "bounce" else msg.status
            if not db.query(Suppression).filter(
                    Suppression.user_id == user.id,
                    Suppression.value == email).first():
                db.add(Suppression(user_id=user.id, value=email,
                                   reason=etype, source="inbound-event"))
            # Cancel pending follow-ups for this lead
            if msg.lead_id:
                for fu in db.query(Followup).filter(
                        Followup.lead_id == msg.lead_id,
                        Followup.status == "SCHEDULED").all():
                    fu.status = "SKIPPED"
                    fu.skip_reason = etype
                lead = db.query(Lead).filter(Lead.id == msg.lead_id).first()
                if lead:
                    lead.next_followup_at = None
        elif etype == "delivered":
            if msg.status == "accepted":
                msg.status = "delivered"
    else:
        if etype in ("bounce", "unsubscribe"):
            db.add(Suppression(user_id=user.id, value=email, reason=etype,
                               source="inbound-event"))
    audit(db, user.id, f"inbound_{etype}", "email", msg.id if msg else None, email)
    db.commit()
    return {"message": f"{etype} recorded", "suppressed": etype in ("bounce", "unsubscribe")}


@router.get("/followups")
def list_followups(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Followup).filter(Followup.user_id == user.id).order_by(
        Followup.scheduled_at.desc()).limit(100).all()
    out = []
    for f in rows:
        lead = db.query(Lead).filter(Lead.id == f.lead_id).first()
        company = db.query(Company).filter(
            Company.id == lead.company_id).first() if lead else None
        out.append({"id": f.id, "lead_id": f.lead_id,
                    "company": company.name if company else None,
                    "scheduled_at": f.scheduled_at, "status": f.status,
                    "skip_reason": f.skip_reason, "sent_at": f.sent_at})
    return out


@router.post("/followups/process")
def process_followups(db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    from app.emailing import process_due_followups
    result = process_due_followups(db, user.id)
    audit(db, user.id, "followups_process", detail=str(result))
    db.commit()
    return result


@router.get("/suppressions")
def list_suppressions(db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    rows = db.query(Suppression).filter(
        Suppression.user_id == user.id).order_by(Suppression.id.desc()).limit(200).all()
    return [{"id": s.id, "value": s.value, "reason": s.reason,
             "source": s.source, "created_at": s.created_at} for s in rows]


@router.post("/unsubscribe",
             dependencies=[Depends(rate_limit("unsub", settings.RATE_SENSITIVE_PER_MIN))])
def unsubscribe(payload: dict, db: Session = Depends(get_db)):
    """Public one-click unsubscribe (link in every live email). Rate-limited."""
    email = normalize_email((payload.get("email") or ""))
    if "@" not in email:
        raise HTTPException(status_code=422, detail="valid email required")
    # Suppress under every account that mailed this address (tenant-safe:
    # only the address itself is stored, nothing else leaks).
    owners = db.query(EmailMessage.user_id).filter(
        EmailMessage.to_email == email).distinct().all()
    for (uid,) in owners:
        if not db.query(Suppression).filter(
                Suppression.user_id == uid, Suppression.value == email).first():
            db.add(Suppression(user_id=uid, value=email,
                               reason="unsubscribe", source="unsubscribe-link"))
    db.commit()
    return {"message": "Unsubscribed"}
