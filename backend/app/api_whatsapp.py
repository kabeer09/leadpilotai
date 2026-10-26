"""WhatsApp automation: click-to-chat links + phone validation."""
import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Lead, Contact, Company
from app.deps import get_current_user

router = APIRouter(prefix="/api", tags=["whatsapp"])


def normalize_phone(value: str) -> str:
    """Extract digits, keep leading + if present."""
    if not value:
        return ""
    value = value.strip()
    has_plus = value.startswith("+")
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    return ("+" if has_plus else "") + digits


def is_valid_whatsapp_number(phone: str) -> bool:
    """Basic validation: 8-15 digits, starts with country code."""
    digits = re.sub(r"\D", "", phone)
    return 8 <= len(digits) <= 15


def wa_link(phone: str, message: str) -> str:
    """Generate WhatsApp click-to-chat link."""
    import urllib.parse
    digits = re.sub(r"\D", "", phone)
    # wa.me requires country code without +
    if digits.startswith("00"):
        digits = digits[2:]
    return f"https://wa.me/{digits}?text={urllib.parse.quote(message)}"


@router.get("/whatsapp/leads")
def whatsapp_leads(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Return all leads with phone numbers. Shows verification status.
    
    Note: Without WhatsApp Business API, we cannot definitively confirm
    a number is on WhatsApp. Numbers are marked 'likely' based on format.
    The user's own verified number is marked 'verified'.
    """
    leads = db.query(Lead).filter(Lead.user_id == user.id).order_by(Lead.id).all()
    results = []
    for l in leads:
        contact = db.query(Contact).filter(
            Contact.company_id == l.company_id,
            Contact.kind == "PHONE").order_by(
            Contact.is_primary.desc()).first()
        if not contact or not contact.value:
            continue
        phone = normalize_phone(contact.value)
        if not is_valid_whatsapp_number(phone):
            continue
        company = db.query(Company).filter(Company.id == l.company_id).first()
        is_own = (user.whatsapp_number and
                  normalize_phone(user.whatsapp_number) == phone)
        results.append({
            "lead_id": l.id,
            "company": company.name if company else "",
            "phone": phone,
            "whatsapp_link": wa_link(phone, ""),
            "status": "verified" if is_own else "likely",
        })
    return {"count": len(results), "leads": results}


@router.post("/whatsapp/send")
def whatsapp_send(payload: dict, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Generate WhatsApp links for selected leads with a message template."""
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message required")
    lead_ids = payload.get("lead_ids") or []
    if not lead_ids:
        lead_ids = [l.id for l in db.query(Lead.id).filter(
            Lead.user_id == user.id).all()]
    links = []
    for lid in lead_ids:
        lead = db.query(Lead).filter(
            Lead.id == lid, Lead.user_id == user.id).first()
        if not lead:
            continue
        contact = db.query(Contact).filter(
            Contact.company_id == lead.company_id,
            Contact.kind == "PHONE").order_by(
            Contact.is_primary.desc()).first()
        if not contact or not contact.value:
            continue
        phone = normalize_phone(contact.value)
        if not is_valid_whatsapp_number(phone):
            continue
        company = db.query(Company).filter(Company.id == lead.company_id).first()
        personalized = message.replace("{company}", company.name if company else "")
        personalized = personalized.replace("{phone}", phone)
        links.append({
            "lead_id": lid,
            "company": company.name if company else "",
            "phone": phone,
            "link": wa_link(phone, personalized),
        })
    return {"count": len(links), "links": links}
