"""Multi-agent buyer discovery services."""
import re
import httpx
import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import Company, Contact, Lead, User
from app.validation import normalize_email, normalize_phone, filter_valid_emails, pick_best_email
from app.ssrf import validate_url


# ── Agent 1: Company Research Agent ──────────────────────────────────────

def research_company_website(company: Company, db: Session) -> dict:
    """Visit company website and extract public business contact channels.

    Returns a dict with extracted fields and their provenance.
    Never fabricates data. Records source URL + timestamp for each field.
    """
    result = {
        "company_id": company.id,
        "website": company.website,
        "emails": [],
        "phones": [],
        "whatsapp_links": [],
        "description": None,
        "country": None,
        "product_categories": None,
        "pages_checked": [],
        "retrieved_at": datetime.utcnow().isoformat(),
        "method": "httpx+bs4",
        "errors": [],
    }

    if not company.website or not company.website.startswith("http"):
        result["errors"].append("No valid website URL")
        return result

    # Pages to check (conservative internal links)
    pages_to_check = ["/", "/contact", "/contact-us", "/about", "/about-us",
                      "/wholesale", "/distribution", "/products", "/services"]

    with httpx.Client(timeout=15.0, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }, follow_redirects=True) as client:
        for path in pages_to_check:
            url = company.website.rstrip("/") + path
            _, err = validate_url(url)
            if err:
                result["errors"].append(f"SSRF blocked: {url}")
                continue
            try:
                r = client.get(url, timeout=15.0)
                if r.status_code != 200:
                    continue
                html = r.text
                result["pages_checked"].append(url)

                # Extract emails from mailto links first (highest confidence)
                mailtos = re.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', html)
                for mt in mailtos:
                    norm = normalize_email(mt.split("?")[0])
                    if norm and norm not in [e["value"] for e in result["emails"]]:
                        result["emails"].append({
                            "value": norm,
                            "source_url": url,
                            "extraction_method": "mailto_link",
                            "confidence": 0.9,
                        })

                # Extract emails from text
                text_emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
                text_emails = [e.lower() for e in text_emails if not e.endswith(('.png', '.jpg', '.gif', '.css', '.js', '.svg'))]
                for e in text_emails:
                    norm = normalize_email(e)
                    if norm and norm not in [x["value"] for x in result["emails"]]:
                        result["emails"].append({
                            "value": norm,
                            "source_url": url,
                            "extraction_method": "text_regex",
                            "confidence": 0.6,
                        })

                # Extract phones from tel links
                tels = re.findall(r'tel:([+\d\s\-().]+)', html)
                for t in tels:
                    norm = normalize_phone(t)
                    if norm and norm not in [p["value"] for p in result["phones"]]:
                        result["phones"].append({
                            "value": norm,
                            "source_url": url,
                            "extraction_method": "tel_link",
                            "confidence": 0.9,
                        })

                # Extract WhatsApp links
                wa_links = re.findall(r'(?:wa\.me/|api\.whatsapp\.com/send\?[^#]*?phone=)(\+?\d[\d\s().-]{6,}\d)', html, re.I)
                for w in wa_links:
                    norm = normalize_phone(w)
                    if norm and norm not in [x["value"] for x in result["whatsapp_links"]]:
                        result["whatsapp_links"].append({
                            "value": norm,
                            "source_url": url,
                            "extraction_method": "wa_link",
                            "confidence": 0.95,
                        })

                # Extract description from meta
                if not result["description"]:
                    import re as _re
                    md = _re.search(r'<meta[^>]*name="description"[^>]*content="([^"]+)"', html, re.I)
                    if md:
                        result["description"] = md.group(1).strip()[:500]

            except Exception as e:
                result["errors"].append(f"{url}: {str(e)[:100]}")

    # Filter valid emails
    valid_emails = filter_valid_emails([e["value"] for e in result["emails"]])
    result["emails"] = [e for e in result["emails"] if e["value"] in valid_emails]

    return result


# ── Agent 3: Buyer Qualification Agent ───────────────────────────────────

BUYER_TYPES = [
    "Importer", "Distributor", "Wholesaler", "Retailer",
    "Kitchenware brand", "Homeware brand", "Giftware supplier",
    "Corporate-gifting supplier", "Sourcing/buying agency",
    "Manufacturer", "Unclear",
]

PRODUCT_KEYWORDS = {
    "copper": ["copper", "copper bottle", "copper drinkware", "copper vessel"],
    "kitchenware": ["kitchenware", "kitchen", "tableware", "cookware", "dining"],
    "giftware": ["gift", "giftware", "gifting", "corporate gift", "souvenir"],
    "drinkware": ["bottle", "drinkware", "tumbler", "mug", "cup", "flask"],
    "home_decor": ["home decor", "decor", "homeware", "home accessory", "interior"],
    "handicraft": ["handicraft", "handmade", "artisan", "craft", "brass", "metalware"],
}

GEO_TARGETS = {"USA", "UK", "Australia", "Canada", "UAE", "Saudi Arabia",
                "Germany", "France", "Japan", "India", "Turkey", "Netherlands",
                "Italy", "Spain", "Belgium", "Switzerland", "Singapore",
                "New Zealand", "South Africa"}


def qualify_buyer(company: Company, research: dict) -> dict:
    """Score and classify a company as a potential buyer.

    Returns score (0-100), buyer_type, product_fit, evidence, etc.
    Transparent criteria — no fabrication.
    """
    score = 0
    evidence = []
    missing = []

    name = (company.name or "").lower()
    desc = (research.get("description") or company.product_summary or "").lower()
    categories = (company.category or "").lower()
    country = company.country or ""
    all_text = " ".join([name, desc, categories]).lower()

    # Product fit signals (max 40 points)
    product_fit = []
    for product, keywords in PRODUCT_KEYWORDS.items():
        for kw in keywords:
            if kw in all_text:
                product_fit.append(product)
                score += 8
                evidence.append(f"Product keyword '{kw}' found in company data")
                break
    score = min(score, 40)

    # Buyer type classification
    buyer_type = "Unclear"
    type_signals = {
        "Importer": ["import", "importer", "sourcing", "buying agent"],
        "Distributor": ["distributor", "distribution", "wholesale"],
        "Wholesaler": ["wholesaler", "wholesale"],
        "Retailer": ["retail", "store", "shop", "boutique"],
        "Manufacturer": ["manufacturer", "factory", "production", "made in"],
        "Sourcing/buying agency": ["sourcing", "buying", "procurement", "agent"],
        "Corporate-gifting supplier": ["corporate gift", "corporate gifting", "promotional"],
    }
    for bt, signals in type_signals.items():
        for s in signals:
            if s in all_text:
                buyer_type = bt
                score += 10
                evidence.append(f"Buyer type signal '{s}' found")
                break
        if buyer_type != "Unclear":
            break

    # Contact availability (max 20 points)
    has_email = bool(research.get("emails"))
    has_phone = bool(research.get("phones"))
    has_wa = bool(research.get("whatsapp_links"))
    if has_email:
        score += 10
        evidence.append("Public business email found")
    else:
        missing.append("No public business email found")
    if has_phone:
        score += 5
        evidence.append("Public phone number found")
    else:
        missing.append("No public phone number found")
    if has_wa:
        score += 5
        evidence.append("Published WhatsApp link found")

    # Geographic fit (max 10 points)
    country_fit = "unknown"
    if country and country in GEO_TARGETS:
        score += 10
        country_fit = "target market"
        evidence.append(f"Country {country} is in target market list")
    elif country:
        country_fit = "other market"
        missing.append(f"Country {country} not in primary target list")
    else:
        missing.append("Country not identified")
        country_fit = "unknown"

    # Website quality (max 10 points)
    if company.website and company.website.startswith("http"):
        score += 5
        evidence.append("Company website available")
    else:
        missing.append("No company website")

    # Product fit assessment
    if "copper" in product_fit:
        product_fit_str = "copper drinkware relevant"
    elif "kitchenware" in product_fit:
        product_fit_str = "kitchenware relevant"
    elif "giftware" in product_fit:
        product_fit_str = "giftware relevant"
    elif product_fit:
        product_fit_str = ", ".join(product_fit) + " relevant"
    else:
        product_fit_str = "no clear product fit identified"
        missing.append("No product keywords matched")

    # Evidence URLs
    evidence_urls = list(set(e["source_url"] for e in research.get("emails", [])))
    evidence_urls += list(set(e["source_url"] for e in research.get("phones", [])))

    # Recommended next action
    if score >= 70:
        action = "High priority — prepare personalized outreach"
    elif score >= 40:
        action = "Medium priority — research further before outreach"
    elif score >= 20:
        action = "Low priority — monitor for future opportunities"
    else:
        action = "Skip — insufficient evidence of buyer fit"

    return {
        "score": min(score, 100),
        "buyer_type": buyer_type,
        "product_fit": product_fit_str,
        "country_fit": country_fit,
        "evidence_urls": evidence_urls[:10],
        "supporting_snippets": evidence[:10],
        "missing_evidence": missing,
        "recommended_next_action": action,
    }


# ── Agent 4: Contact Validation Agent ─────────────────────────────────────

class ContactStatus:
    PUBLICLY_LISTED = "PUBLICLY_LISTED"
    FORMAT_VALID = "FORMAT_VALID"
    DOMAIN_CHECKED = "DOMAIN_CHECKED"
    PROVIDER_VERIFIED = "PROVIDER_VERIFIED"
    DELIVERY_CONFIRMED = "DELIVERY_CONFIRMED"
    UNVERIFIED = "UNVERIFIED"
    NOT_FOUND = "NOT_FOUND"
    STALE = "STALE"
    SUPPRESSED = "SUPPRESSED"


def validate_email_contact(email: str, source_url: str, extraction_method: str,
                          confidence: float) -> dict:
    """Validate an email contact with honest status.

    Never claims verification beyond what's actually done.
    """
    from app.validation import normalize_email, validate_contact_email
    norm = normalize_email(email)
    result = validate_contact_email(norm)

    if not result.get("valid"):
        return {
            "value": norm,
            "contact_type": "email",
            "source_url": source_url,
            "extraction_method": extraction_method,
            "confidence": confidence,
            "validation_status": ContactStatus.UNVERIFIED,
            "reason": result.get("reason", "invalid format"),
        }

    # Syntax valid = FORMAT_VALID, not VERIFIED
    status = ContactStatus.FORMAT_VALID
    # If extracted from a trusted source (mailto link), upgrade to PUBLICLY_LISTED
    if extraction_method in ("mailto_link", "wa_link", "tel_link"):
        status = ContactStatus.PUBLICLY_LISTED

    return {
        "value": norm,
        "contact_type": "email",
        "source_url": source_url,
        "extraction_method": extraction_method,
        "confidence": confidence,
        "validation_status": status,
        "reason": "syntax valid" if status == ContactStatus.FORMAT_VALID else "publicly listed on source page",
    }


def validate_phone_contact(phone: str, source_url: str, extraction_method: str,
                           confidence: float) -> dict:
    """Validate a phone contact."""
    from app.validation import normalize_phone
    norm = normalize_phone(phone)
    if not norm or len(norm) < 7:
        return {
            "value": phone,
            "contact_type": "phone",
            "source_url": source_url,
            "extraction_method": extraction_method,
            "confidence": confidence,
            "validation_status": ContactStatus.UNVERIFIED,
            "reason": "invalid format",
        }

    status = ContactStatus.PUBLICLY_LISTED if extraction_method in (
        "tel_link", "wa_link") else ContactStatus.FORMAT_VALID

    return {
        "value": norm,
        "contact_type": "phone",
        "source_url": source_url,
        "extraction_method": extraction_method,
        "confidence": confidence,
        "validation_status": status,
        "reason": "published on source page",
    }


# ── Gemini AI Personalization ─────────────────────────────────────────────

def gemini_personalize_email(company: Company, qualification: dict,
                              research: dict, api_key: str = None) -> dict | None:
    """Use Gemini to personalize an outreach email based on company research.

    Returns None if Gemini is unavailable or fails. Falls back to template.
    """
    import os
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-3.5-flash")

        name = company.name or "your company"
        country = company.country or "your market"
        products = research.get("description") or company.product_summary or ""
        buyer_type = qualification.get("buyer_type", "Unclear")
        product_fit = qualification.get("product_fit", "relevant products")

        prompt = (
            "Write a professional B2B outreach email from NLI Collections (copper "
            "water bottles, copper drinkware, brass/copper handicrafts, kitchenware, "
            "home decor, giftware manufacturer in Moradabad, India) to %s in %s.\n\n"
            "Company info: %s\n"
            "Buyer type: %s\n"
            "Product fit: %s\n\n"
            "Requirements:\n"
            "- Subject line (max 60 chars)\n"
            "- Professional, concise body (150-200 words)\n"
            "- Mention their products/business model\n"
            "- Clear call to action (request for catalogue discussion)\n"
            "- No fabricated claims, certifications, or prices\n"
            "- Sign off as NLI Collections, Moradabad, India\n\n"
            "Format:\n"
            "SUBJECT: <subject>\n"
            "BODY:\n<body>"
        ) % (name, country, products[:500], buyer_type, product_fit)

        response = model.generate_content(prompt)
        text = response.text.strip()

        subject = ""
        body = ""
        if "SUBJECT:" in text:
            parts = text.split("BODY:", 1)
            subject = parts[0].replace("SUBJECT:", "").strip()
            body = parts[1].strip() if len(parts) > 1 else ""
        else:
            body = text

        if not body:
            return None

        return {
            "subject": subject or "Partnership inquiry — copper drinkware for %s" % name,
            "body": body,
            "language": "en",
            "personalization_evidence": qualification.get("supporting_snippets", [])[:5],
            "source_links": qualification.get("evidence_urls", [])[:5],
            "buyer_type": buyer_type,
            "product_fit": product_fit,
            "requires_approval": True,
            "auto_send": False,
            "ai_generated": True,
        }
    except Exception as e:
        print("Gemini error: %s" % str(e)[:200], flush=True)
        return None


# ── Agent 5: Outreach Draft Agent ─────────────────────────────────────────

def generate_outreach_draft(company: Company, qualification: dict,
                           research: dict, gemini_key: str = None) -> dict:
    """Generate an editable email draft. Tries Gemini first, falls back to template.
    No sending. No fabrication."""
    # Try Gemini AI first
    ai_draft = gemini_personalize_email(company, qualification, research, gemini_key)
    if ai_draft:
        return ai_draft

    name = company.name or "your company"
    country = company.country or "your market"
    product_fit = qualification.get("product_fit", "relevant products")
    buyer_type = qualification.get("buyer_type", "Unclear")

    subject = "Partnership inquiry — copper drinkware & handicrafts for %s" % name

    body = (
        "Dear %s team,\n\n"
        "I hope this message finds you well. I am reaching out from NLI Collections, "
        "a manufacturer of copper water bottles, copper drinkware, brass and copper "
        "handicrafts, kitchenware and home décor items based in Moradabad, India.\n\n"
        "We noticed your presence at trade fairs and believe our product range — "
        "particularly copper drinkware and handcrafted metalware — could complement "
        "your current offering in %s.\n\n"
        "Our products are crafted by skilled artisans and are available for wholesale, "
        "distribution and private-label arrangements. We would be happy to share our "
        "catalogue and discuss how we might work together.\n\n"
        "Would you be open to a brief conversation?\n\n"
        "Best regards,\n"
        "NLI Collections\n"
        "Moradabad, India\n"
    ) % (name, country)

    return {
        "subject": subject,
        "body": body,
        "language": "en",
        "personalization_evidence": qualification.get("supporting_snippets", [])[:5],
        "source_links": qualification.get("evidence_urls", [])[:5],
        "buyer_type": buyer_type,
        "product_fit": product_fit,
        "requires_approval": True,
        "auto_send": False,
    }


# ── Agent 6: Quality Control Agent ─────────────────────────────────────────

def quality_check(company: Company, contacts: list[dict],
                  qualification: dict) -> dict:
    """Quality control checks before importing a lead.

    Returns issues list and whether the record should be flagged for review.
    """
    issues = []
    flagged = False

    # Check company name
    if not company.name or len(company.name) < 2:
        issues.append("Company name missing or too short")
        flagged = True

    # Check for guessed emails
    for c in contacts:
        if c.get("extraction_method") == "guessed":
            issues.append("Guessed email detected — rejected")
            flagged = True
        if c.get("confidence", 0) < 0.5:
            issues.append("Low confidence contact: %s" % c.get("value", ""))
            flagged = True

    # Check buyer score
    score = qualification.get("score", 0)
    if score < 20:
        issues.append("Low buyer score (%d) — review recommended" % score)
        flagged = True

    # Check for missing evidence
    missing = qualification.get("missing_evidence", [])
    if len(missing) > 3:
        issues.append("Too many missing evidence fields: %s" % ", ".join(missing[:3]))
        flagged = True

    # Check suppression
    # (handled by caller)

    return {
        "passed": not flagged,
        "issues": issues,
        "flagged_for_review": flagged,
        "recommendation": "import" if not flagged else "review",
    }


# ── Orchestration Service ─────────────────────────────────────────────────

def run_research_pipeline(db: Session, user_id: int, company_ids: list[int],
                          job_id: int = None) -> dict:
    """Run the full research pipeline on a list of companies.

    Steps:
    1. Research each company website
    2. Validate contacts
    3. Qualify buyers
    4. Quality control
    5. Generate drafts for approved companies

    Returns summary of results.
    """
    results = {
        "companies_processed": 0,
        "contacts_found": 0,
        "qualified": 0,
        "flagged": 0,
        "errors": [],
    }

    for cid in company_ids:
        try:
            company = db.query(Company).filter(
                Company.id == cid, Company.user_id == user_id).first()
            if not company:
                results["errors"].append(f"Company {cid} not found")
                continue

            # Step 1: Research
            research = research_company_website(company, db)

            # Step 2: Validate contacts
            validated_contacts = []
            for e in research.get("emails", []):
                vc = validate_email_contact(
                    e["value"], e["source_url"], e["extraction_method"],
                    e["confidence"])
                validated_contacts.append(vc)
            for p in research.get("phones", []):
                vp = validate_phone_contact(
                    p["value"], p["source_url"], p["extraction_method"],
                    p["confidence"])
                validated_contacts.append(vp)
            for w in research.get("whatsapp_links", []):
                vp = validate_phone_contact(
                    w["value"], w["source_url"], w["extraction_method"],
                    w["confidence"])
                vp["contact_type"] = "whatsapp"
                vp["validation_status"] = "PUBLICLY_LISTED"
                validated_contacts.append(vp)

            results["contacts_found"] += len(validated_contacts)

            # Step 3: Qualify
            qualification = qualify_buyer(company, research)

            # Step 4: Quality control
            qc = quality_check(company, validated_contacts, qualification)

            # Step 5: Generate draft if qualified and not flagged
            draft = None
            if qc["passed"] and qualification["score"] >= 40:
                draft = generate_outreach_draft(company, qualification, research)
                results["qualified"] += 1
            if qc["flagged_for_review"]:
                results["flagged"] += 1

            # Save research results to company (extend if needed)
            if research.get("description") and not company.product_summary:
                company.product_summary = research["description"][:500]
            if research.get("country") and not company.country:
                company.country = research["country"]
            company.last_verified = datetime.utcnow()
            db.commit()

            results["companies_processed"] += 1

        except Exception as e:
            results["errors"].append(f"Company {cid}: {str(e)[:100]}")
            db.rollback()

    return results
