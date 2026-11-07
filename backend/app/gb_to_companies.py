"""Sync Business Lead Finder (GB) leads into the CRM Companies list.

Every value written here comes from crawled public information already
stored on the BusinessLead and its evidence rows — nothing is guessed.
Conventions are borrowed from the fair crawler (crawler._save_company_inner)
so both pipelines produce identical Company/Contact/Lead shapes:

- identity: exact profile URL -> name -> shared email/phone -> domain
  (crawler._find_company, so re-syncs and re-crawls collapse into one row);
- fill-only: crawled facts are never overwritten, only completed;
- contacts dedup on (kind, normalized) and carry their source_url
  (the exact page the value was published on);
- the primary email is picked by the old Stage-3 validation rule;
- a pipeline Lead row is created when the company has none.

GB leads are search results the user explicitly asked to keep, so the
crawler's EXCLUDED_NO_CONTACT flag is deliberately NOT applied — every
synced company stays visible in the Companies list even when it has no
email/phone yet.

The sync only flushes — the caller owns the commit, so a search run and
an enrichment run persist their lead and the CRM mirror in one
transaction.
"""
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.crawler import (SOCIAL_KINDS, _find_company, _page_type,
                         apply_contact_validation)
from app.models import Company, Contact, Lead
from app.models_gbleads import BusinessLead, LeadEvidence
from app.validation import normalize_email, normalize_phone
from app.whatsapp import phone_e164, source_default_region

# evidence field_name -> CRM Contact kind (socials handled separately)
_EVIDENCE_KINDS = {"email": "EMAIL", "phone": "PHONE", "whatsapp": "WHATSAPP"}


def sync_lead_to_company(db: Session, lead: BusinessLead) -> Company | None:
    """Mirror one GB lead into Company + Contact + Lead rows (flush only).

    Idempotent: a second call finds the same company (profile URL / name /
    email / phone / domain) and the (kind, normalized) contact dedup drops
    every value that is already stored.
    """
    name = (lead.company_name or "").strip()
    if not name:
        return None
    url = (lead.google_maps_url or lead.website or "").strip()
    find_url = url or f"gblead/lead/{lead.id}"
    host = (urlparse(lead.website).hostname or "") if lead.website else ""

    # ---- identity (crawler rules) --------------------------------------
    norm_email = normalize_email(lead.email) if lead.email else None
    existing = _find_company(db, lead.user_id, find_url, host, name,
                             [norm_email] if norm_email else [],
                             [lead.phone] if lead.phone else [])
    company = existing or Company(user_id=lead.user_id, source_id=None,
                                  name=name[:500])
    if not existing:
        db.add(company)
        db.flush()

    # ---- fill-only field merge (never erase crawled facts) -------------
    if not company.website and lead.website:
        company.website = lead.website[:2048]
    if not company.product_summary and lead.business_description:
        company.product_summary = lead.business_description[:2000]
    if not company.category and lead.business_category:
        company.category = lead.business_category[:255]
    if not company.country and lead.country:
        company.country = lead.country[:100]
    if not company.city and lead.city:
        company.city = lead.city[:100]
    if not company.address and lead.address:
        company.address = lead.address[:2000]
    if not company.profile_url and url.startswith("http"):
        company.profile_url = url[:2048]
    company.last_verified = datetime.utcnow()
    db.flush()

    # ---- contacts: evidence rows first (they own the source URL) -------
    have = {(c.kind, c.normalized) for c in
            db.query(Contact).filter(Contact.company_id == company.id).all()}
    region = source_default_region(lead.website)

    def _add_contact(kind: str, raw, source_url) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        if kind == "EMAIL":
            norm = normalize_email(raw)
        else:
            e164, _how = phone_e164(raw, lead.country, region)
            norm = e164 or normalize_phone(raw)
        if not norm or (kind, norm) in have:
            return
        src = source_url if (source_url or "").startswith("http") else None
        db.add(Contact(user_id=lead.user_id, company_id=company.id, kind=kind,
                       value=norm[:500], normalized=norm[:500],
                       source_url=(src or "")[:2048] or None,
                       source_page=(_page_type(src) if src else None),
                       is_primary=(kind == "EMAIL"
                                   and not any(k == "EMAIL" for k, _ in have)),
                       **({"whatsapp_status": "identified"}
                          if kind == "WHATSAPP" else {})))
        have.add((kind, norm))

    evidence = (db.query(LeadEvidence)
                .filter(LeadEvidence.lead_id == lead.id).all())
    for ev in evidence:
        fn = (ev.field_name or "").lower()
        if fn in _EVIDENCE_KINDS:
            _add_contact(_EVIDENCE_KINDS[fn], ev.field_value, ev.source_url)
        elif fn.startswith("social_"):
            kind = SOCIAL_KINDS.get(fn[7:].lower())
            su = (ev.field_value or "").strip()
            if kind and su.startswith("http") and (kind, su) not in have:
                db.add(Contact(user_id=lead.user_id, company_id=company.id,
                               kind=kind, value=su[:500], normalized=su[:500],
                               source_url=ev.source_url[:2048],
                               source_page=_page_type(ev.source_url)))
                have.add((kind, su))
    # listing-level fallback when no evidence row exists yet (dedup drops
    # anything the evidence pass already stored)
    _add_contact("EMAIL", lead.email, url)
    _add_contact("PHONE", lead.phone, url)

    # ---- primary email by the old Stage-3 rule -------------------------
    apply_contact_validation(db, company)

    # ---- pipeline Lead row (same rule as the crawler) ------------------
    if not db.query(Lead).filter(Lead.user_id == lead.user_id,
                                 Lead.company_id == company.id).first():
        primary = db.query(Contact).filter(
            Contact.company_id == company.id, Contact.kind == "EMAIL",
            Contact.is_primary.is_(True)).first()
        has_email = any(k == "EMAIL" for k, _ in have)
        has_phone = any(k in ("PHONE", "WHATSAPP") for k, _ in have)
        score = min(100, (20 if has_email else 0) + (10 if has_phone else 0)
                    + (20 if lead.business_description else 0)
                    + (10 if host else 0))
        db.add(Lead(user_id=lead.user_id, company_id=company.id,
                    contact_id=primary.id if primary else None,
                    status="NEW", score=score))
    db.flush()
    return company
