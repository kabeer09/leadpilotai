"""Company website enrichment — visit company websites to extract contacts."""
import re
import httpx
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import Company, Contact, Lead, User
from app.validation import normalize_email, normalize_phone, filter_valid_emails
from app.ssrf import validate_url


def enrich_company_website(company: Company, db: Session, user_id: int) -> dict:
    """Visit a company's website and extract additional contact details.

    Returns dict with found contacts and status.
    Only extracts from permitted public pages. Never fabricates data.
    """
    result = {"company_id": company.id, "emails": [], "phones": [], "whatsapp": [], "pages_checked": [], "errors": []}

    if not company.website:
        result["errors"].append("No valid website URL")
        return result
    website = company.website.strip()
    if not website.startswith("http"):
        website = "https://" + website
    company.website = website

    # Pages to check
    paths = ["/", "/contact", "/contact-us", "/about", "/about-us", "/wholesale",
             "/distribution", "/export", "/b2b", "/sales", "/customer-service"]

    with httpx.Client(timeout=15.0, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }, follow_redirects=True) as client:
        for path in paths:
            url = company.website.rstrip("/") + path
            _, err = validate_url(url)
            if err:
                result["errors"].append("SSRF blocked: %s" % url)
                continue
            try:
                r = client.get(url, timeout=15.0)
                if r.status_code != 200:
                    continue
                html = r.text
                result["pages_checked"].append(url)

                # Extract emails from mailto links (high confidence)
                mailtos = re.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', html)
                for mt in mailtos:
                    norm = normalize_email(mt.split("?")[0])
                    if norm and norm not in [e["value"] for e in result["emails"]]:
                        from app.validation import validate_contact_email
                        vr = validate_contact_email(norm)
                        if vr.get("valid"):
                            result["emails"].append({"value": norm, "source_url": url, "method": "mailto_link", "confidence": 0.9})

                # Extract emails from text
                text_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
                text_emails = [e.lower() for e in text_emails if not e.endswith(('.png', '.jpg', '.gif', '.css', '.js', '.svg', '.ico'))]
                for e in text_emails:
                    norm = normalize_email(e)
                    if norm and norm not in [x["value"] for x in result["emails"]]:
                        from app.validation import validate_contact_email
                        vr = validate_contact_email(norm)
                        if vr.get("valid"):
                            result["emails"].append({"value": norm, "source_url": url, "method": "text_regex", "confidence": 0.6})

                # Extract phones from tel links
                tels = re.findall(r'tel:([+\d\s\-().]+)', html)
                for t in tels:
                    norm = normalize_phone(t)
                    if norm and len(norm) >= 7 and norm not in [p["value"] for p in result["phones"]]:
                        result["phones"].append({"value": norm, "source_url": url, "method": "tel_link", "confidence": 0.9})

                # Extract WhatsApp links
                wa_links = re.findall(r'(?:wa\.me/|api\.whatsapp\.com/send\?[^#]*?phone=)(\+?\d[\d\s().-]{6,}\d)', html, re.I)
                for w in wa_links:
                    norm = normalize_phone(w)
                    if norm and norm not in [x["value"] for x in result["whatsapp"]]:
                        result["whatsapp"].append({"value": norm, "source_url": url, "method": "wa_link", "confidence": 0.95})

            except Exception as e:
                result["errors"].append("%s: %s" % (url, str(e)[:100]))

    # Save found contacts to database
    saved = 0
    for e in result["emails"]:
        existing = db.query(Contact).filter(Contact.company_id == company.id, Contact.kind == "EMAIL", Contact.value == e["value"]).first()
        if not existing:
            db.add(Contact(user_id=user_id, company_id=company.id, kind="EMAIL", value=e["value"], normalized=e["value"], source_url=e["source_url"], is_primary=True))
            saved += 1
    for p in result["phones"]:
        existing = db.query(Contact).filter(Contact.company_id == company.id, Contact.kind == "PHONE", Contact.value == p["value"]).first()
        if not existing:
            db.add(Contact(user_id=user_id, company_id=company.id, kind="PHONE", value=p["value"], normalized=p["value"], source_url=p["source_url"], is_primary=True))
            saved += 1
    for w in result["whatsapp"]:
        existing = db.query(Contact).filter(Contact.company_id == company.id, Contact.kind == "WHATSAPP", Contact.value == w["value"]).first()
        if not existing:
            db.add(Contact(user_id=user_id, company_id=company.id, kind="WHATSAPP", value=w["value"], normalized=w["value"], source_url=w["source_url"], whatsapp_status="identified"))
            saved += 1
    if saved > 0:
        db.commit()

    return result


def enrich_companies_batch(db: Session, user_id: int, company_ids: list[int]) -> dict:
    """Enrich a batch of companies by visiting their websites."""
    results = {"enriched": 0, "emails_found": 0, "phones_found": 0, "whatsapp_found": 0, "errors": []}
    for cid in company_ids:
        try:
            company = db.query(Company).filter(Company.id == cid, Company.user_id == user_id).first()
            if not company or not company.website:
                continue
            if not company.website.startswith("http"):
                company.website = "https://" + company.website.strip()
            r = enrich_company_website(company, db, user_id)
            results["enriched"] += 1
            results["emails_found"] += len(r["emails"])
            results["phones_found"] += len(r["phones"])
            results["whatsapp_found"] += len(r["whatsapp"])
            results["errors"].extend(r["errors"])
        except Exception as e:
            results["errors"].append("Company %d: %s" % (cid, str(e)[:100]))
    return results
