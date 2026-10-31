"""WhatsApp automation: country-code fixing, availability checks, providers.

Mirrors the email stack: config endpoint (like health), batch normalize
(fix country codes offline), check (format free / live probe via Meta with
double confirmation), send (links mode = manual click-to-chat, provider mode
= automated sends with quota + double confirmation, like campaigns).
"""
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User, Lead, Contact, Company, Source, WhatsAppMessage
from app.deps import get_current_user, audit, rate_limit
from app.whatsapp import (phone_e164, inspect_phone, wa_link,
                          source_default_region, get_whatsapp_provider,
                          whatsapp_sent_today)

router = APIRouter(prefix="/api", tags=["whatsapp"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _company_region(db: Session, company: Company | None) -> str:
    """Fair's default dial region (e.g. High Point = US) when country unknown."""
    if not company or not company.source_id:
        return ""
    src = db.query(Source).filter(Source.id == company.source_id).first()
    return source_default_region(src.url if src else None)


def _company_phone(db: Session, lead: Lead) -> tuple[Contact | None, str, str]:
    """Best contact for WhatsApp: WHATSAPP row first, else primary PHONE.
    Returns (contact, e164, how)."""
    company = db.query(Company).filter(Company.id == lead.company_id).first()
    contact = None
    if lead.contact_id:
        c = db.query(Contact).filter(Contact.id == lead.contact_id).first()
        if c and c.kind in ("WHATSAPP", "PHONE"):
            contact = c
    if not contact:
        contact = db.query(Contact).filter(
            Contact.company_id == lead.company_id,
            Contact.kind == "WHATSAPP").order_by(
            Contact.is_primary.desc()).first()
    if not contact:
        contact = db.query(Contact).filter(
            Contact.company_id == lead.company_id,
            Contact.kind == "PHONE").order_by(
            Contact.is_primary.desc()).first()
    if not contact:
        return None, "", "none"
    region = _company_region(db, company)
    country = company.country if company else None
    e164, how = phone_e164(contact.value, country, region)
    if not e164:
        e164, how = phone_e164(contact.normalized, country, region)
    return contact, e164, how


def _select_leads(db: Session, user: User, lead_ids: list[int]) -> list[Lead]:
    q = db.query(Lead).filter(Lead.user_id == user.id)
    if lead_ids:
        q = q.filter(Lead.id.in_(lead_ids))
    return q.order_by(Lead.id).all()


# ---------------------------------------------------------------------------
# config / status (like health's email provider block)
# ---------------------------------------------------------------------------

@router.get("/whatsapp/config")
def whatsapp_config(db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    provider = get_whatsapp_provider()
    configured = (
        settings.WHATSAPP_PROVIDER == "dryrun"
        or bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID))
    return {"provider": provider.name,
            "configured": configured,
            "template": settings.WHATSAPP_TEMPLATE_NAME or None,
            "template_lang": settings.WHATSAPP_TEMPLATE_LANG,
            "daily_limit": settings.DAILY_WHATSAPP_LIMIT,
            "sent_today": whatsapp_sent_today(db, user.id),
            "note": ("dryrun records sends but never touches WhatsApp and "
                     "never claims a number is registered. Set "
                     "WHATSAPP_PROVIDER=meta (Cloud API + approved template) "
                     "for real sends and real registration answers.")}


# ---------------------------------------------------------------------------
# 1) fix country codes (offline, free, batch)
# ---------------------------------------------------------------------------

class NormalizeReq(BaseModel):
    apply: bool = True
    lead_ids: list[int] = Field(default_factory=list)


@router.post("/whatsapp/normalize")
def whatsapp_normalize(payload: NormalizeReq, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Resolve every PHONE/WHATSAPP row to strict E.164 using the company's
    country (or the fair's default region). Exact duplicates that only
    differed by '+'/country-code form are merged. Offline (libphonenumber)."""
    q = db.query(Contact).filter(Contact.user_id == user.id,
                                 Contact.kind.in_(["PHONE", "WHATSAPP"]))
    if payload.lead_ids:
        leads = _select_leads(db, user, payload.lead_ids)
        cids = [l.company_id for l in leads]
        q = q.filter(Contact.company_id.in_(cids))
    rows = q.all()
    checked = fixed = invalid = no_country = 0
    samples = []
    for r in rows:
        checked += 1
        company = db.query(Company).filter(Company.id == r.company_id).first()
        region = _company_region(db, company)
        country = company.country if company else None
        e164, how = phone_e164(r.value, country, region)
        if not e164:
            e164, how = phone_e164(r.normalized, country, region)
        if how == "no_country":
            no_country += 1
            if payload.apply and r.kind == "WHATSAPP" and r.whatsapp_status == "identified":
                r.whatsapp_status = "no_country"
            continue
        if not e164:
            invalid += 1
            if payload.apply and r.kind == "WHATSAPP":
                r.whatsapp_status = "invalid"
            continue
        if e164 != r.normalized:
            if len(samples) < 12:
                samples.append({"id": r.id, "kind": r.kind,
                                "before": r.normalized, "after": e164,
                                "how": how, "country": country})
            fixed += 1
            if payload.apply:
                r.normalized = e164
                if r.kind == "PHONE" and (r.value or "").strip():
                    r.value = e164  # phones display as dialable now
    # merge exact duplicates that surfaced after canonicalization
    merged = 0
    if payload.apply:
        seen: dict[tuple[int, str], int] = {}
        for r in sorted(rows, key=lambda x: x.id):
            if not r.normalized:
                continue
            key = (r.company_id, r.kind, r.normalized)
            if key in seen:
                # re-point any lead at the surviving row before merging
                for lnk in db.query(Lead).filter(Lead.contact_id == r.id).all():
                    lnk.contact_id = seen[key]
                db.delete(r)
                merged += 1
            else:
                seen[key] = r.id
    if payload.apply:
        db.commit()
    audit(db, user.id, "whatsapp_normalize", "contacts", None,
          f"fixed={fixed} merged={merged} apply={payload.apply}")
    db.commit()
    return {"checked": checked, "fixed": fixed, "invalid": invalid,
            "no_country": no_country, "merged": merged, "applied": payload.apply,
            "samples": samples}


# ---------------------------------------------------------------------------
# 2) availability check: free format pass, optional live Meta probe
# ---------------------------------------------------------------------------

class CheckReq(BaseModel):
    lead_ids: list[int] = Field(default_factory=list)
    live: bool = False
    confirm: bool = False


@router.post("/whatsapp/check")
def whatsapp_check(payload: CheckReq, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Offline pass (free): E.164 + region + line-type validation, updates
    whatsapp_status to invalid/no_country/identified (format only).
    Live pass (opt-in): real Meta probe per number — accepted => on_whatsapp,
    131026 => not_on_whatsapp. Costs one Meta conversation per probe, so it
    requires confirm=true AND an explicit lead_ids list (max 100/call)."""
    leads = _select_leads(db, user, payload.lead_ids)
    if not leads and payload.lead_ids:
        raise HTTPException(status_code=404, detail="No matching leads")

    fmt = {"fixed": 0, "valid": 0, "invalid": 0, "no_country": 0,
           "landline": 0, "mobile": 0}
    live_res = {"on_whatsapp": 0, "not_on_whatsapp": 0, "skipped": 0,
                "errors": []}
    targets = []  # (contact, e164, company) for the live pass

    for lead in leads:
        contact, e164, how = _company_phone(db, lead)
        if not contact:
            continue
        company = db.query(Company).filter(Company.id == lead.company_id).first()
        if not e164:
            if how == "no_country":
                fmt["no_country"] += 1
                if contact.kind == "WHATSAPP":
                    contact.whatsapp_status = "no_country"
            else:
                fmt["invalid"] += 1
                if contact.kind == "WHATSAPP":
                    contact.whatsapp_status = "invalid"
            continue
        if e164 != contact.normalized:
            fmt["fixed"] += 1
            contact.normalized = e164
        info = inspect_phone(e164)
        fmt["valid"] += 1
        if info["wa_likely"]:
            fmt["mobile"] += 1
            if contact.kind == "WHATSAPP" and contact.whatsapp_status in (
                    "unknown", "identified", "no_country", "invalid"):
                contact.whatsapp_status = "identified"
        else:
            fmt["landline"] += 1
        targets.append((contact, e164, company))

    db.commit()

    if payload.live:
        provider = get_whatsapp_provider()
        if provider.name != "whatsapp-meta":
            raise HTTPException(status_code=400, detail=(
                "Live check requires WHATSAPP_PROVIDER=meta (Cloud API). "
                "Offline format results are in this response under 'format'."))
        if not payload.confirm:
            raise HTTPException(status_code=422, detail=(
                f"Live probes cost one Meta conversation each — resend with "
                f'{{"lead_ids": [...], "live": true, "confirm": true}} '
                f"(max 100 per call, {len(targets)} in scope)"))
        if len(targets) > 100:
            raise HTTPException(status_code=422,
                                detail="Max 100 live probes per call")
        for contact, e164, company in targets:
            res = provider.check(e164)
            if res.get("registered") is True:
                contact.whatsapp_status = "on_whatsapp"
                live_res["on_whatsapp"] += 1
            elif res.get("registered") is False:
                contact.whatsapp_status = "not_on_whatsapp"
                live_res["not_on_whatsapp"] += 1
            else:
                live_res["skipped"] += 1
                if res.get("error") and len(live_res["errors"]) < 5:
                    live_res["errors"].append(str(res["error"])[:200])
        db.commit()
        audit(db, user.id, "whatsapp_check_live", "contacts", None,
              f"on={live_res['on_whatsapp']} off={live_res['not_on_whatsapp']}")
        db.commit()

    return {"provider": get_whatsapp_provider().name,
            "format": fmt, "live": live_res if payload.live else None}


# ---------------------------------------------------------------------------
# 3) leads list (click-to-chat, kept for the existing UI)
# ---------------------------------------------------------------------------

@router.get("/whatsapp/leads")
def whatsapp_leads(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """All leads with phone numbers + status. Status meanings:
    verified = your own number; identified = format-valid (offline);
    on_whatsapp / not_on_whatsapp = confirmed by Meta; invalid / no_country
    = could not be resolved to a dialable international number."""
    leads = _select_leads(db, user, [])
    results = []
    for l in leads:
        contact, phone, how = _company_phone(db, l)
        if not contact or not phone:
            continue
        company = db.query(Company).filter(Company.id == l.company_id).first()
        is_own = (user.whatsapp_number and
                  re.sub(r"\D", "", user.whatsapp_number) ==
                  re.sub(r"\D", "", phone))
        status = ("verified" if is_own else
                  contact.whatsapp_status if contact.kind == "WHATSAPP"
                  and contact.whatsapp_status not in ("unknown",) else "likely")
        results.append({
            "lead_id": l.id,
            "company": company.name if company else "",
            "phone": phone,
            "whatsapp_link": wa_link(phone, ""),
            "status": status,
        })
    return {"count": len(results), "leads": results}


# ---------------------------------------------------------------------------
# 4) send: links (manual) or provider (automated, double-confirmed)
# ---------------------------------------------------------------------------

class SendReq(BaseModel):
    message: str = Field(min_length=1)
    lead_ids: list[int] = Field(default_factory=list)
    mode: str = "links"  # links | provider
    confirm: bool = False
    recipient_count_ack: int = 0


@router.post("/whatsapp/send",
             dependencies=[Depends(rate_limit("wasend", settings.RATE_SENSITIVE_PER_MIN))])
def whatsapp_send(payload: SendReq, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """mode=links (default): generate wa.me click-to-chat links (manual).
    mode=provider: automated send through WHATSAPP_PROVIDER with the same
    double confirmation as email campaigns (confirm + exact recipient count),
    daily quota, and honest accepted/failed states."""
    message = payload.message.strip()
    leads = _select_leads(db, user, payload.lead_ids)

    # collect (lead, contact, e164) — dedup by number, skip unresolvable
    plan: list[tuple[Lead, Contact, str]] = []
    seen_phones: set[str] = set()
    skipped_invalid = skipped_status = 0
    for lead in leads:
        contact, e164, how = _company_phone(db, lead)
        if not contact or not e164:
            skipped_invalid += 1
            continue
        if e164 in seen_phones:
            continue
        if contact.kind == "WHATSAPP" and contact.whatsapp_status == "not_on_whatsapp":
            skipped_status += 1
            continue
        seen_phones.add(e164)
        plan.append((lead, contact, e164))

    # ---- links mode: original behavior (one link per lead, no dedup needed)
    if payload.mode == "links":
        links = []
        for lead, contact, e164 in plan:
            company = db.query(Company).filter(
                Company.id == lead.company_id).first()
            personalized = message.replace(
                "{company}", company.name if company else "")
            links.append({"lead_id": lead.id,
                          "company": company.name if company else "",
                          "phone": e164,
                          "link": wa_link(e164, personalized)})
        return {"count": len(links), "links": links,
                "skipped_invalid": skipped_invalid, "skipped_status": skipped_status}

    # ---- provider mode: automated sends
    if payload.mode != "provider":
        raise HTTPException(status_code=422,
                            detail="mode must be 'links' or 'provider'")
    provider = get_whatsapp_provider()
    if provider.name == "whatsapp-meta" and not settings.WHATSAPP_ACCESS_TOKEN:
        raise HTTPException(status_code=400,
                            detail="WHATSAPP_PROVIDER=meta but not configured "
                                   "(see GET /api/whatsapp/config)")
    if not payload.confirm or payload.recipient_count_ack != len(plan):
        raise HTTPException(status_code=422, detail=(
            f"Explicit confirmation required: resend with "
            f'{{"confirm": true, "recipient_count_ack": {len(plan)}}} '
            f"(mode=provider; skipped_invalid={skipped_invalid}, "
            f"skipped_not_on_whatsapp={skipped_status})"))
    used = whatsapp_sent_today(db, user.id)
    if used >= settings.DAILY_WHATSAPP_LIMIT:
        raise HTTPException(status_code=429,
                            detail="Daily WhatsApp quota reached — try tomorrow")

    from app.whatsapp import template_variables
    sent = failed = 0
    errors: list[str] = []
    for lead, contact, e164 in plan:
        if whatsapp_sent_today(db, user.id) >= settings.DAILY_WHATSAPP_LIMIT:
            errors.append("daily WhatsApp quota reached — remaining skipped")
            break
        company = db.query(Company).filter(Company.id == lead.company_id).first()
        variables = template_variables(
            company.name if company else "", company.country if company else "",
            e164, message)
        result = provider.send(e164, variables, message)
        row = WhatsAppMessage(
            user_id=user.id, lead_id=lead.id, to_phone=e164, body=message,
            provider=provider.name,
            status="accepted" if result.get("ok") else "failed",
            provider_message_id=result.get("provider_message_id"),
            error=None if result.get("ok") else str(result.get("error"))[:500],
            sent_at=None)
        db.add(row)
        db.flush()
        if result.get("ok"):
            sent += 1
            # dryrun proves nothing; only a real Meta accept marks registration
            wa_status = result.get("wa_status")
            if contact.kind == "WHATSAPP" and wa_status:
                contact.whatsapp_status = wa_status
            elif contact.kind == "PHONE" and wa_status == "on_whatsapp":
                pass  # phone rows don't carry WhatsApp claims
        else:
            failed += 1
            if contact.kind == "WHATSAPP" and result.get("wa_status"):
                contact.whatsapp_status = result["wa_status"]
            if len(errors) < 5:
                errors.append(f"{e164}: {str(result.get('error'))[:150]}")
    db.commit()
    audit(db, user.id, "whatsapp_send", "whatsapp", None,
          f"{payload.mode} sent={sent} failed={failed}")
    db.commit()
    return {"provider": provider.name, "sent": sent, "failed": failed,
            "skipped_invalid": skipped_invalid,
            "skipped_not_on_whatsapp": skipped_status,
            "sent_today": whatsapp_sent_today(db, user.id),
            "daily_limit": settings.DAILY_WHATSAPP_LIMIT,
            "errors": errors}


@router.get("/whatsapp/messages")
def whatsapp_messages(limit: int = 50, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    rows = (db.query(WhatsAppMessage).filter(WhatsAppMessage.user_id == user.id)
            .order_by(WhatsAppMessage.id.desc()).limit(min(limit, 200)).all())
    return {"count": len(rows), "messages": [
        {"id": r.id, "lead_id": r.lead_id, "to_phone": r.to_phone,
         "status": r.status, "provider": r.provider,
         "error": r.error, "sent_at": r.sent_at, "created_at": r.created_at}
        for r in rows]}
