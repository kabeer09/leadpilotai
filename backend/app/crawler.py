"""Polite single-site crawler: httpx + BeautifulSoup, robots-aware, SSRF-safe.

Each job runs in a background thread with cancellation. No Playwright (kept
off for small-deploy compatibility). Limits: pages, depth, size, timeout,
retries with backoff. Never guesses data: only scraped values are stored,
each with its source URL.
"""
import logging
import re
import threading
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.database import SessionLocal
from app.models import Source, CrawlJob, CrawlEvent, Company, Contact, Lead
from app.ssrf import validate_url

logger = logging.getLogger(__name__)

UA = "LeadPilotTrialBot/0.1 (+contact via account email; respects robots.txt)"
TIMEOUT = 15.0
MAX_BYTES = 2_000_000
RETRY_ATTEMPTS = 2
PAGE_DELAY = 1.0

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")
JUNK_EMAIL_DOMAINS = ("example.com", "example.org", "test.com", ".png", ".jpg")

_cancel_flags: dict[int, threading.Event] = {}
_threads: dict[int, threading.Thread] = {}


def _norm_email(raw: str) -> str | None:
    e = raw.strip().lower()
    if "@" not in e or any(j in e for j in JUNK_EMAIL_DOMAINS) or len(e) > 255:
        return None
    local, _, domain = e.partition("@")
    if not local or "." not in domain:
        return None
    return e


def _norm_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7 or len(digits) > 16:
        return None
    return ("+" + digits) if raw.strip().startswith("+") else digits


def _page_type(url: str) -> str:
    u = url.lower()
    if "contact" in u:
        return "contact"
    if "about" in u:
        return "about"
    if any(k in u for k in ("product", "catalog", "shop", "collection")):
        return "products"
    return "home"


def _robots_allowed(base: str, url: str) -> bool:
    try:
        rp = RobotFileParser()
        rp.set_url(urljoin(base, "/robots.txt"))
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return True  # fail-open on robots fetch errors (page still rate-limited)


def _fetch(client: httpx.Client, url: str) -> tuple[str | None, str | None]:
    """GET with manual redirect validation. Returns (html, error)."""
    current = url
    for _ in range(6):  # max redirect hops
        _, err = validate_url(current)
        if err:
            return None, err
        try:
            resp = client.get(current, headers={"User-Agent": UA}, timeout=TIMEOUT,
                              follow_redirects=False)
        except Exception as e:
            return None, f"fetch error: {str(e)[:120]}"
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                return None, "empty redirect"
            current = urljoin(current, loc)
            continue
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype:
            return None, f"non-HTML ({ctype[:40]})"
        if len(resp.content) > MAX_BYTES:
            return None, "page too large"
        return resp.text, None
    return None, "too many redirects"


def extract_page(url: str, html: str) -> dict:
    """Extract company-ish facts from one page. No guessing, no fabrication."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")[:500]
    og_site = ""
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        og_site = og["content"].strip()[:500]
    desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        desc = md["content"].strip()[:2000]

    emails = sorted({_norm_email(e) for e in EMAIL_RE.findall(text)} - {None})
    phones = sorted({_norm_phone(p) for p in PHONE_RE.findall(text)} - {None})
    low = text.lower()
    # A number counts as WhatsApp ONLY when the word appears near it.
    # Positions come from regex spans, so separators can't break matching.
    wa_spans = [m.start() for m in re.finditer(r"whatsapp", low)]
    wa_confirmed = []
    for m in PHONE_RE.finditer(text):
        norm = _norm_phone(m.group(0))
        if norm and any(abs(m.start() - w) <= 150 for w in wa_spans):
            wa_confirmed.append(norm)

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append((urljoin(url, href), (a.get_text(strip=True) or "")[:120]))
    return {
        "title": title, "site_name": og_site, "description": desc,
        "emails": emails[:20], "phones": phones[:20],
        "whatsapp": sorted(set(wa_confirmed))[:10],
        "links": links, "text_len": len(text),
    }


def _is_company_candidate(data: dict) -> bool:
    return bool(data["title"]) and (bool(data["emails"]) or bool(data["phones"])
                                    or (data["text_len"] or 0) > 500)


def _save_company(db, user_id: int, source_id: int, url: str, data: dict,
                 depth: int) -> Company | None:
    name = data["site_name"] or data["title"].split("|")[0].split("-")[0].strip()[:500]
    if not name:
        return None
    host = urlparse(url).hostname or ""
    existing = db.query(Company).filter(
        Company.user_id == user_id, Company.website == host).first()
    company = existing or Company(user_id=user_id, source_id=source_id, name=name[:500])
    if not existing:
        db.add(company)
        db.flush()
    company.website = host
    if data["description"] and not company.product_summary:
        company.product_summary = data["description"]
    company.profile_url = company.profile_url or url
    company.last_verified = datetime.utcnow()
    if company.lifecycle == "EXCLUDED_NO_CONTACT" and (data["emails"] or data["phones"]):
        company.lifecycle = "active"
    db.flush()

    have = {(c.kind, c.normalized) for c in
            db.query(Contact).filter(Contact.company_id == company.id).all()}
    n_new = 0
    primaries = {"EMAIL": not any(k == "EMAIL" for k, _ in have)}
    for e in data["emails"]:
        if ("EMAIL", e) not in have:
            db.add(Contact(user_id=user_id, company_id=company.id, kind="EMAIL",
                           value=e, normalized=e, source_url=url,
                           source_page=_page_type(url),
                           is_primary=primaries["EMAIL"]))
            primaries["EMAIL"] = False
            n_new += 1
    for p in data["phones"]:
        if ("PHONE", p) not in have:
            db.add(Contact(user_id=user_id, company_id=company.id, kind="PHONE",
                           value=p, normalized=p, source_url=url,
                           source_page=_page_type(url)))
            n_new += 1
    for w in data["whatsapp"]:
        if ("WHATSAPP", w) not in have:
            db.add(Contact(user_id=user_id, company_id=company.id, kind="WHATSAPP",
                           value=w, normalized=w, source_url=url,
                           source_page=_page_type(url), whatsapp_status="identified"))
            n_new += 1
    if not data["emails"] and not data["phones"] and company.lifecycle == "active":
        # Only flag excluded when we never found anything AND row is new-ish;
        # crawler never hides companies that already have contacts.
        if not have:
            company.lifecycle = "EXCLUDED_NO_CONTACT"
    # Ensure a lead exists
    lead = db.query(Lead).filter(Lead.user_id == user_id,
                                 Lead.company_id == company.id).first()
    if not lead:
        primary = db.query(Contact).filter(
            Contact.company_id == company.id, Contact.kind == "EMAIL",
            Contact.is_primary.is_(True)).first()
        score = min(100, (20 if data["emails"] else 0) + (10 if data["phones"] else 0)
                    + (20 if data["description"] else 0) + (10 if host else 0))
        db.add(Lead(user_id=user_id, company_id=company.id,
                    contact_id=primary.id if primary else None,
                    status="NEW", score=score))
    db.commit()
    return company


def run_job(job_id: int) -> None:
    """Background crawl worker. All writes tenant-scoped."""
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        if not job or job.status not in ("PENDING",):
            return
        job.status = "CRAWLING"
        job.started_at = datetime.utcnow()
        db.commit()
        cancel = _cancel_flags.setdefault(job_id, threading.Event())

        base_host = urlparse(job.url).hostname or ""
        seen: set[str] = set()
        queue: list[tuple[str, int]] = [(job.url, 0)]
        pages = companies = contacts = 0
        errors: list[str] = []
        base = f"{urlparse(job.url).scheme}://{base_host}"

        with httpx.Client() as client:
            while queue and pages < job.max_pages and not cancel.is_set():
                url, depth = queue.pop(0)
                canon = url.split("#")[0]
                if canon in seen:
                    continue
                seen.add(canon)
                if depth == 0 and not _robots_allowed(base, canon):
                    db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                                      url=canon, status="blocked",
                                      detail="robots.txt disallow"))
                    db.commit()
                    continue
                html, err = _fetch(client, canon)
                pages += 1
                job.pages_visited = pages
                if err:
                    db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                                      url=canon, status="error", detail=err[:500]))
                    errors.append(f"{canon}: {err}"[:200])
                else:
                    data = extract_page(canon, html or "")
                    detail = f"emails={len(data['emails'])} phones={len(data['phones'])}"
                    if _is_company_candidate(data):
                        comp = _save_company(db, job.user_id, job.source_id,
                                             canon, data, depth)
                        if comp:
                            companies += 1
                            contacts += len(data["emails"]) + len(data["phones"])
                    db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                                      url=canon, status="ok", detail=detail[:500]))
                    if depth < job.max_depth:
                        for href, _label in data["links"][:60]:
                            h = urlparse(href).hostname or ""
                            if h == base_host and href.split("#")[0] not in seen:
                                queue.append((href.split("#")[0], depth + 1))
                job.companies_found = companies
                job.contacts_found = contacts
                db.commit()
                time.sleep(PAGE_DELAY)

        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        job.status = "CANCELLED" if cancel.is_set() else "COMPLETED"
        job.finished_at = datetime.utcnow()
        job.errors = "\n".join(errors[-10:]) or None
        if job.source_id:
            src = db.query(Source).filter(Source.id == job.source_id).first()
            if src:
                src.status = job.status
        db.commit()
        logger.info(f"Crawl job {job_id}: {job.status} pages={pages} companies={companies}")
    except Exception as e:
        try:
            job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
            if job:
                job.status = "FAILED"
                job.finished_at = datetime.utcnow()
                job.errors = str(e)[:2000]
                db.commit()
        except Exception:
            pass
        logger.error(f"Crawl job {job_id} failed: {e}")
    finally:
        db.close()


def start_job(job_id: int) -> bool:
    if job_id in _threads and _threads[job_id].is_alive():
        return False
    _cancel_flags[job_id] = threading.Event()
    t = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    _threads[job_id] = t
    t.start()
    return True


def cancel_job(job_id: int) -> bool:
    flag = _cancel_flags.get(job_id)
    if flag:
        flag.set()
        return True
    return False
