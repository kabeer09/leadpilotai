"""Official-website enrichment for Google Business Leads (bounded, sourced).

Honesty rules (spec section 4):
- Emails/phones are extracted ONLY when literally published on the page.
  Nothing is guessed, constructed or pattern-filled.
- Every extracted value stores its exact source_url + method + method-based
  confidence (confidence = how the value was found, NOT a verification claim).
- Bounded: at most GB_MAX_PAGES_PER_SITE pages per site, GB_REQUEST_DELAY
  seconds between requests, robots.txt Disallow respected, login/paywall/
  captcha URLs never attempted, SSRF-validated before every fetch.
"""
import logging
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.config import settings
from app.models_gbleads import BusinessLead, LeadEvidence
from app.ssrf import validate_url
from app.validation import (normalize_email, normalize_phone,
                            validate_contact_email)
from app.whatsapp import phone_e164
from app.gb_scoring import (score_relevance, classify_business_type,
                            normalize_domain)

logger = logging.getLogger(__name__)

UA = ("LeadPilotTrialBot/0.2 (+contact via account email; respects robots.txt)")

# pages worth visiting (spec section 4), matched against path + anchor text
WANT_RE = re.compile(
    r"(about|contact|imprint|product|service|wholesale|trade|export|"
    r"dealer|distributor|partner|company|who-we-are|store|shop)", re.I)
# never attempt these (spec: no login/paywall/captcha bypass)
NEVER_RE = re.compile(
    r"(wp-login|/login|/signin|/sign-in|/account|/checkout|/cart|"
    r"subscribe|paywall|captcha|/admin)", re.I)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TEL_RE = re.compile(r"(?:tel:)?(\+?\d[\d\s().-]{6,}\d)")
WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?[^\"'<>]*?phone=)"
                   r"(\+?\d[\d\s().-]{6,}\d)", re.I)
BAD_EMAIL_EXT = (".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".svg",
                 ".webp", ".ico", ".woff", ".mp4")
LINKEDIN_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/"
                         r"(?:company|school)/[A-Za-z0-9_%-]+", re.I)
INSTAGRAM_RE = re.compile(r"https?://(?:www\.)?instagram\.com/"
                          r"[A-Za-z0-9_.]+", re.I)

# method-based confidence (how the value was found — not a verification claim)
CONF = {"mailto_link": 0.9, "text_regex": 0.6, "tel_link": 0.9,
        "tel_text": 0.6, "wa_link": 0.95, "meta_description": 0.7,
        "social_anchor": 0.8, "anchor_text": 0.6, "body_text": 0.5}


# ---------------------------------------------------------------------------
# fetch seam (tests inject a fake fetcher; no paid calls, no network)
# ---------------------------------------------------------------------------

def default_fetcher(url: str) -> tuple[int, str, str]:
    """Real HTTP fetch. Returns (status_code, html, final_url)."""
    _, err = validate_url(url)
    if err:
        return 0, "", url
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        }) as client:
            r = client.get(url)
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype:
                return r.status_code, "", str(r.url)
            return r.status_code, r.text[:500_000], str(r.url)
    except Exception as e:
        logger.info("fetch failed %s: %s", url, str(e)[:120])
        return 0, "", url


def _robots_allows(base_url: str, path: str,
                   fetcher=default_fetcher) -> bool:
    """Minimal robots.txt check for our UA (best-effort, fail-open on
    missing file; Disallow blocks only when explicitly listed)."""
    try:
        parsed = urlparse(base_url)
        status, text, _ = fetcher(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    except Exception:
        return True
    if status != 200 or not text:
        return True
    # split per user-agent group; ours (or the wildcard group)
    groups: list[tuple[list[str], list[str]]] = []
    cur_agents: list[str] = []
    cur_rules: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("user-agent:"):
            if cur_agents and cur_rules:
                groups.append((cur_agents, cur_rules))
            cur_agents = [a.strip() for a in line.split(":", 1)[1].split(",")]
            cur_rules = []
        elif line.lower().startswith(("allow:", "disallow:")):
            key, val = line.split(":", 1)
            cur_rules.append((key.strip().lower(), val.strip()))
    if cur_agents and cur_rules:
        groups.append((cur_agents, cur_rules))
    for agents, rules in groups:
        if not any(a in ("*", "leadpilottrialbot") for a in agents):
            continue
        for key, val in rules:
            if not val:
                continue
            if key == "disallow" and path.startswith(val):
                return False
    return True


# ---------------------------------------------------------------------------
# extraction (pure-ish over one page's HTML)
# ---------------------------------------------------------------------------

def _page_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    root = (soup.find("main") or soup.find("article")
            or soup.select_one("[role=main]") or soup.body or soup)
    return re.sub(r"\s+", " ", root.get_text(" ", strip=True))[:200_000]


def extract_page(html: str, url: str) -> dict:
    """Extract published facts from one page (never guesses)."""
    out = {"emails": [], "phones": [], "whatsapp": [], "socials": [],
           "title": "", "description": "", "links": [], "text": ""}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return out

    if soup.title and soup.title.string:
        out["title"] = soup.title.string.strip()[:500]
    for m in soup.find_all("meta"):
        if (m.get("name") or "").lower() in ("description",) or \
                (m.get("property") or "").lower() in ("og:description",):
            val = (m.get("content") or "").strip()
            if val and not out["description"]:
                out["description"] = val[:1000]
            break

    seen_e: set[str] = set()
    # mailto (highest confidence)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            raw = href[7:].split("?")[0].strip()
            norm = normalize_email(raw)
            if norm and norm not in seen_e:
                vr = validate_contact_email(norm)
                if vr.get("valid"):
                    out["emails"].append({"value": norm, "source_url": url,
                                          "method": "mailto_link",
                                          "confidence": CONF["mailto_link"]})
                    seen_e.add(norm)
        if href.lower().startswith("tel:"):
            norm = normalize_phone(href[4:])
            if norm and 7 <= len(re.sub(r"\D", "", norm)) <= 15:
                out["phones"].append({"value": norm, "source_url": url,
                                      "method": "tel_link",
                                      "confidence": CONF["tel_link"]})

    # literal emails in HTML (never constructed — only what's displayed)
    for m in EMAIL_RE.findall(html):
        if m.lower().endswith(BAD_EMAIL_EXT):
            continue
        norm = normalize_email(m)
        if norm and norm not in seen_e:
            vr = validate_contact_email(norm)
            if vr.get("valid"):
                out["emails"].append({"value": norm, "source_url": url,
                                      "method": "text_regex",
                                      "confidence": CONF["text_regex"]})
                seen_e.add(norm)

    # phones in text/tel-ish patterns (precision-first: a formatted grouping
    # or '+' is required, year ranges like 2023-2024 are rejected)
    seen_p: set[str] = set()
    for raw in TEL_RE.findall(html):
        raw = raw.strip()
        digits = re.sub(r"\D", "", raw)
        if not (7 <= len(digits) <= 15) or digits.startswith(("1900", "1800")):
            continue
        if not any(c in raw for c in "+( ") and "-" not in raw:
            continue  # bare digit run — could be an id/price, not a phone
        if re.fullmatch(r"(?:19|20)\d{2}[-\s]?(?:19|20)\d{2}", raw):
            continue  # year pair
        norm = ("+" + digits) if raw.startswith("+") else normalize_phone(raw)
        if norm and norm not in seen_p:
            out["phones"].append({"value": norm, "source_url": url,
                                  "method": "tel_text",
                                  "confidence": CONF["tel_text"]})
            seen_p.add(norm)

    for raw in WA_RE.findall(html):
        norm = normalize_phone(raw)
        if norm and norm not in {w["value"] for w in out["whatsapp"]}:
            out["whatsapp"].append({"value": norm, "source_url": url,
                                    "method": "wa_link",
                                    "confidence": CONF["wa_link"]})

    # social links (only when the page links them)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = LINKEDIN_RE.search(href) or INSTAGRAM_RE.search(href)
        if m:
            link = m.group(0)
            if link not in out["socials"]:
                out["socials"].append(link)

    out["text"] = _page_text(soup)

    # internal links for bounded discovery
    base_netloc = urlparse(url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#", 1)[0]
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absu = urljoin(url, href)
        p = urlparse(absu)
        if p.netloc and p.netloc != base_netloc:
            continue
        if NEVER_RE.search(p.path):
            continue
        label = (a.get_text(" ", strip=True) or "") + " " + p.path
        if WANT_RE.search(label):
            out["links"].append(absu)
    # de-dup, keep order
    seen_l: set[str] = set()
    out["links"] = [l for l in out["links"]
                    if not (l in seen_l or seen_l.add(l))]
    return out


# ---------------------------------------------------------------------------
# per-lead enrichment
# ---------------------------------------------------------------------------

def _add_evidence(db: Session, lead: BusinessLead, field: str, value: str,
                  source: str, method: str, confidence: float) -> None:
    exists = db.query(LeadEvidence).filter(
        LeadEvidence.lead_id == lead.id,
        LeadEvidence.field_name == field,
        LeadEvidence.field_value == value[:1000]).first()
    if not exists:
        db.add(LeadEvidence(user_id=lead.user_id, lead_id=lead.id,
                            field_name=field, field_value=value[:1000],
                            source_url=source[:2048],
                            extraction_method=method,
                            confidence=confidence))


def enrich_lead(lead: BusinessLead, db, user_id: int,
                fetcher=default_fetcher, provider=None,
                campaign=None) -> dict:
    """Visit the lead's official site (bounded) and store sourced evidence.

    fetcher: callable(url) -> (status, html, final_url) — injected in tests.
    provider: optional Places provider for a one-time details() backfill of
    a missing website/phone (counted as an API call on the campaign).
    Returns counters dict.
    """
    res = {"pages": 0, "emails": 0, "phones": 0, "whatsapp": 0,
           "socials": 0, "errors": []}

    # --- backfill missing website via Place Details (once, if possible) ---
    if not lead.website and provider is not None and lead.place_id:
        try:
            calls_before = provider.calls
            det = provider.details(lead.place_id)
            # account the actual attempts (retries included), even on failure
            attempted = provider.calls - calls_before
            if det:
                if det.get("website") and not lead.website:
                    lead.website = det["website"]
                    lead.website_domain = normalize_domain(det["website"])
                    _add_evidence(db, lead, "website", det["website"],
                                  f"places/{lead.place_id}", "place_details",
                                  0.95)
                if det.get("phone") and not lead.phone:
                    lead.phone = det["phone"]
                    _add_evidence(db, lead, "phone", det["phone"],
                                  f"places/{lead.place_id}", "place_details",
                                  0.95)
            if campaign is not None and attempted:
                campaign.api_calls += attempted
                campaign.estimated_cost_usd = round(
                    campaign.api_calls * settings.GB_COST_PER_CALL_USD, 4)
        except Exception as e:
            res["errors"].append(f"details: {str(e)[:120]}")

    if not lead.website:
        lead.enrichment_status = "NO_WEBSITE"
        db.commit()
        return res

    base = lead.website.strip()
    if not base.startswith("http"):
        base = "https://" + base
    # SSRF check before any request
    checked, err = validate_url(base)
    if err:
        lead.enrichment_status = "FAILED"
        res["errors"].append(f"blocked: {err}")
        db.commit()
        return res
    base = checked

    pages: list[str] = [base]
    evidence_texts: list[tuple[str, str]] = []  # (text, source) for scoring
    fetched_ok = 0
    contact_url = ""

    # homepage first, then bounded discovery of relevant internal pages
    idx = 0
    while idx < len(pages) and len(pages) <= settings.GB_MAX_PAGES_PER_SITE:
        url = pages[idx]
        idx += 1
        path = urlparse(url).path or "/"
        if not _robots_allows(base, path, fetcher):
            res["errors"].append(f"robots disallowed: {url}")
            continue
        if idx > 1:
            time.sleep(max(0.0, settings.GB_REQUEST_DELAY))
        status, html, final_url = fetcher(url)
        if status == 429 or (status == 403 and "captcha" in html.lower()):
            res["errors"].append(f"blocked ({status}) — stopping: {url}")
            break
        if status != 200 or not html:
            res["errors"].append(f"HTTP {status}: {url}")
            continue
        fetched_ok += 1
        res["pages"] += 1
        page = extract_page(html, final_url or url)
        src = final_url or url

        if page["title"]:
            evidence_texts.append((page["title"], src))
        if page["description"]:
            evidence_texts.append((page["description"], src))
        if page["text"]:
            evidence_texts.append((page["text"], src))
            # products/services snippet as its own evidence field
            if re.search(r"product|collection|range|catalog", path, re.I) \
                    or WANT_RE.search(path):
                snippet = page["text"][:400]
                _add_evidence(db, lead, "products", snippet, src,
                              "page_text", CONF["body_text"])

        for e in page["emails"]:
            _add_evidence(db, lead, "email", e["value"], e["source_url"],
                          e["method"], e["confidence"])
            res["emails"] += 1
        for p in page["phones"]:
            _add_evidence(db, lead, "phone", p["value"], p["source_url"],
                          p["method"], p["confidence"])
            res["phones"] += 1
        for w in page["whatsapp"]:
            _add_evidence(db, lead, "whatsapp", w["value"], w["source_url"],
                          w["method"], w["confidence"])
            res["whatsapp"] += 1
        for s in page["socials"]:
            kind = "linkedin" if "linkedin" in s.lower() else "instagram"
            _add_evidence(db, lead, f"social_{kind}", s, src,
                          "social_anchor", CONF["social_anchor"])
            res["socials"] += 1

        if not contact_url and re.search(r"contact|imprint", path, re.I):
            contact_url = src

        # bounded link discovery
        for l in page["links"]:
            if l not in pages and len(pages) < settings.GB_MAX_PAGES_PER_SITE:
                pages.append(l)

    if fetched_ok == 0:
        lead.enrichment_status = "FAILED"
        db.commit()
        return res

    # --- roll up the best published contact onto the lead (sourced) ------
    evs = db.query(LeadEvidence).filter(LeadEvidence.lead_id == lead.id).all()
    emails = [e for e in evs if e.field_name == "email"]
    if emails:
        # explicit buyer-facing roles first, then generic roles — all values
        # were published; only the pick order is preference. Within the same
        # role tier, prefer the higher-confidence extraction method.
        pref = ("sales", "export", "enquiry", "inquiry",
                "info", "hello", "contact", "office")

        def _rank(value: str) -> int:
            local = value.split("@")[0]  # role lives in the local part only
            for i, p in enumerate(pref):
                if p in local:
                    return i
            return len(pref)

        emails.sort(key=lambda e: (_rank(e.field_value), -e.confidence))
        lead.email = emails[0].field_value
    if not lead.phone:
        phones = [e for e in evs if e.field_name == "phone"]
        if phones:
            # evidence keeps the as-displayed form; the lead field gets the
            # dialable E.164 form when the country resolves it
            e164, _how = phone_e164(phones[0].field_value, lead.country)
            lead.phone = e164 or phones[0].field_value

    # --- re-score + classify with website evidence ------------------------
    keywords = _campaign_keywords(lead, db)
    score, label, reasons = score_relevance(
        keywords, lead.company_name, lead.business_category, evidence_texts)
    lead.relevance_score = score
    lead.relevance_label = label
    lead.match_reasons = _json_list(reasons)
    btype, bevidence = classify_business_type(
        [("Google listing", f"{lead.company_name} {lead.business_category or ''}")]
        + evidence_texts)
    lead.business_type = btype
    lead.business_type_evidence = _json_list(bevidence)

    lead.enrichment_status = "ENRICHED"
    lead.enriched_at = datetime.utcnow()
    db.commit()
    return res


def _campaign_keywords(lead: BusinessLead, db) -> list[str]:
    import json
    if not lead.campaign_id:
        return []
    from app.models_gbleads import SearchCampaign
    c = db.query(SearchCampaign).filter(
        SearchCampaign.id == lead.campaign_id).first()
    if not c:
        return []
    try:
        return json.loads(c.keywords or "[]")
    except ValueError:
        return []


def _json_list(items: list[str]) -> str:
    import json
    return json.dumps(items[:12], ensure_ascii=False)
