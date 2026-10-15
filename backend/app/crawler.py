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

# One headless browser per worker thread (renders run in parallel).
# Small containers should set CRAWL_WORKERS=1 to stay light.
_thread_state = threading.local()
_browser_lock = threading.Lock()


def _ensure_browser():
    """Launch this thread's Chromium on first use. None when disabled."""
    if not settings.PLAYWRIGHT_ENABLED:
        return None
    browser = getattr(_thread_state, "browser", None)
    if browser is not None:
        try:
            if browser.is_connected():
                return browser
        except Exception:
            pass
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        _thread_state.browser = browser
        _thread_state.playwright = pw
        logger.info("Chromium renderer ready (worker thread)")
        return browser
    except Exception as e:
        logger.warning(f"Browser rendering unavailable: {str(e)[:150]}")
        return None


def _render_with_browser(url: str) -> tuple[str | None, str | None]:
    """Re-fetch a JS shell with headless Chromium. Returns (html, error)."""
    browser = _ensure_browser()
    if browser is None:
        return None, "browser rendering unavailable"
    _, err = validate_url(url)
    if err:
        return None, err
    with _browser_lock:
        try:
            page = browser.new_page(user_agent=UA)
            try:
                page.goto(url, timeout=30000, wait_until="networkidle")
            except Exception:
                pass  # networkidle often times out on chatty pages; DOM may be ready
            page.wait_for_timeout(2500)
            html = page.content()
            return html, None
        except Exception as e:
            return None, f"render error: {str(e)[:150]}"
        finally:
            try:
                page.close()
            except Exception:
                pass


def _looks_like_js_shell(html: str | None, data: dict) -> bool:
    """A page that needs rendering: almost no text/contacts but app markers."""
    if not html:
        return False
    if data["emails"] or data["phones"]:
        return False
    low = html.lower()
    markers = ('id="root"', 'id="app"', "__next_data__", "ng-version",
               "data-reactroot", "nuxt", "/_next/static/", "sitecore",
               "jss", "webpack", "vite/client", "react-root", "angular",
               "vue-app", "__nuxt", "gatsby")
    has_marker = any(m in low for m in markers)
    text_len = data["text_len"] or 0
    # Tiny shells always render; larger ones only with app markers
    # (nav/footer boilerplate can be several KB of nothing).
    return text_len < 1500 or (has_marker and text_len < 15000)


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
    # 8-digit YYYYMMDD runs are dates/booth codes, not phones (e.g. 20260916)
    if (len(digits) == 8 and digits[:2] in ("19", "20")
            and "01" <= digits[4:6] <= "12" and "01" <= digits[6:8] <= "31"):
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


_robots_cache: dict[str, list[tuple[str, str]]] = {}


def _robots_rules(base: str) -> list[tuple[str, str]] | None:
    """Fetch + parse robots.txt ourselves (None = unreachable, fail-open).

    Returns [(agent, path), ...] for Disallow lines only. We honor explicit
    Disallows and ignore everything else — never default-deny on parse quirks.
    """
    if base in _robots_cache:
        return _robots_cache[base]
    try:
        import httpx as _hx
        resp = _hx.get(urljoin(base, "/robots.txt"),
                       headers={"User-Agent": UA}, timeout=TIMEOUT)
        if resp.status_code != 200 or len(resp.content) > 100_000:
            _robots_cache[base] = []
            return []
        rules: list[tuple[str, str]] = []
        agents: list[str] = []
        for raw in resp.text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "user-agent":
                agents = [val.lower()]
            elif key == "disallow" and val:
                rules.extend((a, val) for a in agents)
        _robots_cache[base] = rules
        return rules
    except Exception:
        return None


def _robots_allowed(base: str, url: str) -> bool:
    rules = _robots_rules(base)
    if not rules:
        return True  # unreachable/unparseable/empty: allow (still rate-limited)
    path = urlparse(url).path or "/"
    ua = UA.lower()
    for agent, dis in rules:
        if agent in ("*", "leadpilottrialbot") or ua.startswith(agent):
            if dis == "/":
                return False
            if path.startswith(dis):
                return False
    return True


def _fetch(client: httpx.Client, url: str) -> tuple[str | None, str | None]:
    """GET with manual redirect validation. Returns (html, error).

    One retry after 3s on transport failures (timeouts, resets) — cheap
    sites often answer the second knock. HTTP status errors are final.
    """
    current = url
    for _ in range(6):  # max redirect hops
        _, err = validate_url(current)
        if err:
            return None, err
        resp = None
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = client.get(current, headers={"User-Agent": UA},
                                  timeout=TIMEOUT, follow_redirects=False)
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt == 1:
                    time.sleep(3)
        if resp is None:
            return None, f"fetch error: {str(last_exc)[:120]}"
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
    og_title = ""
    ogt = soup.find("meta", property="og:title")
    if ogt and ogt.get("content"):
        og_title = ogt["content"].strip()[:500]
    h1 = ""
    h1tag = soup.find("h1")
    if h1tag:
        h1 = h1tag.get_text(" ", strip=True)[:500]
    desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        desc = md["content"].strip()[:2000]

    emails = sorted({_norm_email(e) for e in EMAIL_RE.findall(text)} - {None})
    phones = sorted({_norm_phone(p) for p in PHONE_RE.findall(text)} - {None})
    low = text.lower()
    # A number counts as WhatsApp ONLY when explicitly tied to the word:
    # either directly after it ("whatsapp: +123…") or within 150 chars.
    # Positions come from regex spans, so separators can't break matching.
    wa_confirmed = []
    for pat in (r"(?:whatsapp|wa)[\s:]*([\+]?\d[\d\s\-.()]{7,})",
                r"whatsapp\s*(?:number|contact|business)?[\s:]*([\+]?\d[\d\s\-.()]{7,})"):
        for m in re.finditer(pat, low):
            norm = _norm_phone(m.group(1))
            if norm and norm not in wa_confirmed:
                wa_confirmed.append(norm)
    wa_spans = [m.start() for m in re.finditer(r"whatsapp", low)]
    for m in PHONE_RE.finditer(text):
        norm = _norm_phone(m.group(0))
        if norm and norm not in wa_confirmed \
                and any(abs(m.start() - w) <= 150 for w in wa_spans):
            wa_confirmed.append(norm)

    links = []
    external: list[tuple[str, str, str]] = []  # (host, url, anchor)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(url, href)
        anchor = (a.get_text(strip=True) or "")[:120]
        links.append((full, anchor))
        h = (urlparse(full).hostname or "").lower()
        if h and h != (urlparse(url).hostname or "").lower():
            external.append((h, full, anchor))
    social = ("facebook.", "linkedin.", "twitter.", "instagram.", "youtube.",
              "pinterest.", "tiktok.", "whatsapp.", "telegram.", "xing.",
              "line.me", "t.me", "wa.me")
    # Explicit WhatsApp contact links (wa.me/<number>) ARE business contacts
    # per spec — but share-widget links without numbers are ignored.
    wa_link_re = re.compile(
        r"(?:wa\.me/|api\.whatsapp\.com/send\/?\?[^#]*?phone=)(\+?\d[\d\s().-]{6,}\d)",
        re.IGNORECASE)
    wa_linked = []
    for a in soup.find_all("a", href=True):
        m = wa_link_re.search(a["href"])
        if m:
            norm = _norm_phone(m.group(1))
            if norm and norm not in wa_confirmed:
                wa_linked.append(norm)
    wa_confirmed.extend([w for w in wa_linked if w not in wa_confirmed])
    official = [e for e in external
                if not any(s in e[0] for s in social)
                and not any(s in e[1].lower() for s in ("google.", "apple.com", "microsoft."))]
    from app.extract_profile import extract_profile
    profile = extract_profile(soup, url)
    return {
        "title": title, "site_name": og_site, "og_title": og_title, "h1": h1,
        "description": desc,
        "emails": emails[:20], "phones": phones[:20],
        "whatsapp": sorted(set(wa_confirmed))[:10],
        "links": links, "official_links": official[:10],
        "text_len": len(text),
        **{f"profile_{k}" if k in ("name", "description") else k: v
           for k, v in profile.items()},
    }


PROFILE_PATH_RE = re.compile(
    r"/(exhibitor|exhibitors|company|companies|profile|profiles|listing|listings|"
    r"vendor|vendors|supplier|suppliers|member|members|brand|brands)/[^/?#]+",
    re.IGNORECASE)

# Pages that are never companies and never worth following.
# /line/ sub-pages are brand lines living under an exhibitor (no standalone
# contacts, template-only names) — skip them, the exhibitor page is the prize.
SKIP_PATH_RE = re.compile(
    r"/(login|log-in|signin|sign-in|signup|sign-up|register|account|auth|"
    r"identity|password|checkout|cart|search\?|admin|wp-admin|api/|line/)",
    re.IGNORECASE)

BOTCHECK_RE = re.compile(
    r"(sign you in|access denied|forbidden|are you a robot|captcha|"
    r"just a moment|attention required|403 forbidden|verify you are human)",
    re.IGNORECASE)


def _profile_links(data: dict, host: str) -> list[str]:
    """Same-host links that look like entity profile/detail pages."""
    out = []
    for href, _anchor in data["links"]:
        if (urlparse(href).hostname or "") != host:
            continue
        path = urlparse(href).path or ""
        if SKIP_PATH_RE.search(path):
            continue
        m = PROFILE_PATH_RE.search(path)
        if not m:
            continue
        # The slug must look like an entity id/slug, not a section index:
        # require a digit or a long slug (excludes /exhibitor/exhibitor-directory).
        slug = m.group(0).rstrip("/").split("/")[-1]
        if not (re.search(r"\d", slug) or len(slug) > 12):
            continue
        out.append(href.split("#")[0])
    return sorted(set(out))


INDEX_WORDS = {"directory", "index", "list", "lists", "all", "search",
               "browse", "page", "exhibitors", "companies", "profiles"}


def _looks_like_profile_url(url: str) -> bool:
    """Is this URL itself an entity profile page (vs a section index)?"""
    if SKIP_PATH_RE.search(urlparse(url).path or ""):
        return False
    m = PROFILE_PATH_RE.search(urlparse(url).path or "")
    if not m:
        return False
    slug = m.group(0).rstrip("/").split("/")[-1]
    if re.search(r"\d", slug):
        return True
    low = slug.lower()
    return len(slug) > 12 and not any(w in low for w in INDEX_WORDS)


def _is_listing_page(data: dict, host: str) -> bool:
    """A directory/listing page: many profile links, not a company itself."""
    return len(_profile_links(data, host)) >= 10


def _is_company_candidate(data: dict) -> bool:
    return bool(data["title"]) and (bool(data["emails"]) or bool(data["phones"])
                                    or (data["text_len"] or 0) > 500)


def _save_company(db, user_id: int, source_id: int, url: str, data: dict,
                 depth: int, lock=None) -> Company | None:
    """Persist one company (insert or update). Pass the job lock from workers:
    find+create becomes atomic per job so concurrent takes of the same
    profile URL collapse into one row instead of racing duplicates."""
    import contextlib
    with lock or contextlib.nullcontext():
        return _save_company_inner(db, user_id, source_id, url, data, depth)


def _save_company_inner(db, user_id: int, source_id: int | None, url: str,
                        data: dict, depth: int) -> Company | None:
    def _clean(s: str) -> str:
        cleaned = s.split("|")[0].split(" - ")[0].split(" – ")[0].strip()[:500]
        # Reject symbol-only junk some templates put in meta tags (e.g. og:title="*")
        if cleaned and not re.search(r"[A-Za-z0-9]", cleaned):
            return ""
        return cleaned

    h1 = data.get("h1") or ""
    name = (data.get("site_name")
            or _clean(data.get("profile_name") or "")
            or (_clean(h1) if len(h1) <= 80 else "")
            or _clean(data.get("title") or "")
            or _clean(data.get("og_title") or ""))
    # Unrendered front-end template placeholders are not company names
    # (e.g. "{lineName} shown by {exhibitorName}").
    if not name or "{" in name or "}" in name or "%s" in name:
        return None
    host = urlparse(url).hostname or ""
    # Identity: exact profile URL first; homepage URLs additionally match by
    # domain (so re-crawls of a homepage update one row — profiles on a
    # shared directory domain never collapse into each other).
    existing = _find_company(db, user_id, url, host)
    company = existing or Company(user_id=user_id, source_id=source_id, name=name[:500])
    if not existing:
        db.add(company)
        db.flush()
    if not company.website:
        company.website = host
    # Fill-only merges: never erase crawled facts, only complete them.
    best_desc = data.get("profile_description") or data.get("description")
    if best_desc and not company.product_summary:
        company.product_summary = best_desc[:2000]
    if data.get("industry") and not company.category:
        company.category = data["industry"]
    if data.get("country") and not company.country:
        company.country = data["country"][:100]
        if data.get("city") and not company.city:
            company.city = data["city"][:100]
    if data.get("address") and not getattr(company, "address", None):
        company.address = data["address"][:2000]
    if data.get("hall") and not getattr(company, "hall", None):
        company.hall = data["hall"][:100]
    if data.get("booth") and not getattr(company, "booth", None):
        company.booth = data["booth"][:100]
    if data.get("products_json") and not getattr(company, "products_json", None):
        company.products_json = data["products_json"]
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
    # Old Stage-3 rule: validate + pick the own-domain primary.
    apply_contact_validation(db, company)
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


SITE_ANCHOR_RE = re.compile(r"visit|website|official|web site|homepage", re.IGNORECASE)
CONTACT_PAGE_RE = re.compile(r"contact|about|team|support|impressum|kontakt|reach-us|locations", re.IGNORECASE)


def _find_company(db, user_id: int, url: str, host: str):
    """Find the existing row for a page (profile URL first, homepage by domain)."""
    existing = db.query(Company).filter(
        Company.user_id == user_id, Company.profile_url == url).first()
    if not existing:
        path = urlparse(url).path or "/"
        if path in ("", "/"):
            existing = db.query(Company).filter(
                Company.user_id == user_id, Company.website == host).first()
    return existing


def company_is_complete(db, company) -> bool:
    """Complete = product summary + at least one valid (scraped) email.

    Complete companies are never re-crawled; incomplete ones are updated
    in place when their pages are seen again.
    """
    if not (company.product_summary or "").strip():
        return False
    return db.query(Contact).filter(
        Contact.company_id == company.id, Contact.kind == "EMAIL",
        Contact.verification == "scraped").count() > 0


def _save_allowed(data: dict, canon: str, is_list: bool,
                  shared: dict) -> tuple[bool, str]:
    """Save gate. In directory mode ONLY profile-URL pages with contacts
    or an official site become companies (organizer section pages and
    dead profiles are refused — they can never yield details)."""
    if not shared.get("directory_mode"):
        return True, ""
    if not _looks_like_profile_url(canon) \
            and (urlparse(canon).path or "/") not in ("", "/"):
        return False, " skipped: not a profile page"
    if _looks_like_profile_url(canon) \
            and not data["emails"] and not data["phones"] \
            and _pick_official_site(data.get("official_links"),
                                    urlparse(canon).hostname or "") is None:
        return False, " skipped: profile without contacts or independent site"
    return True, ""


def _should_drop(db, user_id: int, url: str, host: str, data: dict) -> bool:
    """Drop brand-new junk: no contacts + no official site + not a homepage.

    Existing rows are never dropped (they update in place). Homepage crawls
    keep EXCLUDED_NO_CONTACT so a later re-crawl can fill them.
    """
    if _find_company(db, user_id, url, host) is not None:
        return False
    if data.get("emails") or data.get("phones"):
        return False
    if data.get("official_links"):
        return False
    path = urlparse(url).path or "/"
    return path not in ("", "/")


def apply_contact_validation(db, company) -> str | None:
    """Validation pass over a company's email rows (old Stage-3 rules).

    Flags invalid rows, picks the own-domain match as primary. Rows are
    never deleted. Returns the primary email or None.
    """
    from app.validation import (validate_contact_email, normalize_email,
                                pick_best_email)
    rows = db.query(Contact).filter(
        Contact.company_id == company.id, Contact.kind == "EMAIL").all()
    for r in rows:
        r.normalized = normalize_email(r.value) or r.normalized
        r.verification = ("scraped"
                          if validate_contact_email(r.value)["valid"]
                          else "invalid")
    best = pick_best_email([r.value for r in rows], company.website)
    best_norm = normalize_email(best) if best else ""
    for r in rows:
        r.is_primary = bool(best_norm and r.normalized == best_norm)
    db.flush()
    return best


def _pick_official_site(official_links: list[tuple[str, str, str]],
                        page_host: str = "") -> tuple[str, str] | None:
    """Choose the exhibitor's own site from outbound links.

    The site must be an INDEPENDENT domain — sister/parent domains of the
    fair itself (e.g. *.messefrankfurt.com on an ambiente page) are the
    organizer talking about itself, not an exhibitor. Prefers links whose
    anchor names the destination ('Visit aspenhome.net').
    """
    from app.validation import domain_of, _base_domain
    cands = []
    for host, url, anchor in official_links or []:
        try:
            same_family = _base_domain(domain_of(host)) == \
                _base_domain(domain_of(page_host)) if page_host else False
        except Exception:
            same_family = False
        if not same_family:
            cands.append((host, url, anchor))
    if not cands:
        return None
    for host, url, anchor in cands:
        bare = host[4:] if host.startswith("www.") else host
        if bare and bare.lower() in (anchor or "").lower():
            return host, url
    for host, url, anchor in cands:
        if SITE_ANCHOR_RE.search(anchor or ""):
            return host, url
    return None


def _enrich_from_official_site(db, user_id: int, company: Company,
                               site_url: str, client: httpx.Client,
                               render_budget: list[int],
                               job_id: int | None = None) -> tuple[int, int]:
    """Stage 3 (old pipeline): crawl the official site's key pages.

    Visits homepage + up to 3 contact/about pages, merges EVERY public
    email/phone with its exact source URL, then runs the validation pass.
    Returns (new_contacts, pages_used). Never overwrites: official site
    becomes company.website, profile URL stays as provenance.
    """
    from app.validation import normalize_email, normalize_phone
    host = urlparse(site_url).hostname or ""
    base = f"{urlparse(site_url).scheme}://{host}"
    if not _robots_allowed(base, site_url):
        return 0, 0
    pages_used = 0

    def _one(url: str) -> dict | None:
        nonlocal pages_used
        html, err = _fetch(client, url)
        if err and job_id is not None:
            db.add(CrawlEvent(
                user_id=user_id, job_id=job_id, url=url, status="error",
                detail=f"[S3] {err}"[:500]))
            db.commit()
        data = extract_page(url, html) if html and not err else None
        thin = data is not None and not data["emails"] and not data["phones"]
        if (data is None or (thin and _looks_like_js_shell(html, data))) \
                and render_budget[0] > 0:
            html2, err2 = _render_with_browser(url)
            if html2 and not err2:
                render_budget[0] -= 1
                data = extract_page(url, html2)
        pages_used += 1
        if job_id is not None and data is not None:
            db.add(CrawlEvent(
                user_id=user_id, job_id=job_id, url=url, status="ok",
                detail=(f"[S3] emails={len(data['emails'])} "
                        f"phones={len(data['phones'])} "
                        f"page={_page_type(url)}")[:500]))
            db.commit()
        return data

    home = _one(site_url)
    if home is None:
        return 0, pages_used
    found: list[tuple[str, str, list[str], list[str], list[str]]] = [(
        site_url, _page_type(site_url),
        home["emails"], home["phones"], home["whatsapp"])]
    # Contact/About pages carry the emails homepages hide
    sub = []
    for href, _anchor in home["links"][:80]:
        if (urlparse(href).hostname or "").lower() != host.lower():
            continue
        if CONTACT_PAGE_RE.search(urlparse(href).path or ""):
            clean = href.split("#")[0]
            if clean != site_url and clean not in sub:
                sub.append(clean)
        if len(sub) >= 3:
            break
    for curl in sub:
        d = _one(curl)
        if d and (d["emails"] or d["phones"] or d["whatsapp"]):
            found.append((curl, _page_type(curl),
                          d["emails"], d["phones"], d["whatsapp"]))

    have = {(c.kind, c.normalized) for c in
            db.query(Contact).filter(Contact.company_id == company.id).all()}
    added = 0
    for src_url, page, emails, phones, wa in found:
        for e in emails:
            norm = normalize_email(e)
            if norm and ("EMAIL", norm) not in have:
                db.add(Contact(user_id=user_id, company_id=company.id,
                               kind="EMAIL", value=norm, normalized=norm,
                               source_url=src_url, source_page=page))
                have.add(("EMAIL", norm))
                added += 1
        for p in phones:
            norm = normalize_phone(p)
            if norm and ("PHONE", norm) not in have:
                db.add(Contact(user_id=user_id, company_id=company.id,
                               kind="PHONE", value=norm, normalized=norm,
                               source_url=src_url, source_page=page))
                have.add(("PHONE", norm))
                added += 1
        for w in wa:
            norm = normalize_phone(w)
            if norm and ("WHATSAPP", norm) not in have:
                db.add(Contact(user_id=user_id, company_id=company.id,
                               kind="WHATSAPP", value=norm, normalized=norm,
                               source_url=src_url, source_page=page,
                               whatsapp_status="identified"))
                have.add(("WHATSAPP", norm))
                added += 1
    if added:
        company.website = host
        if company.lifecycle == "EXCLUDED_NO_CONTACT":
            company.lifecycle = "active"
        company.last_verified = datetime.utcnow()
    apply_contact_validation(db, company)
    db.commit()
    return added, pages_used


def _process_url(db, client, job, canon: str, depth: int,
                 base: str, base_host: str, shared: dict) -> None:
    """Fetch + extract + save ONE url (worker thread). All writes tenant-scoped."""
    lock = shared["lock"]
    # Already-complete companies are never re-crawled (costs no budget).
    # Incomplete ones flow through and get updated in place.
    host0 = urlparse(canon).hostname or ""
    path0 = urlparse(canon).path or "/"
    with lock:
        dir_mode = shared.get("directory_mode")
    # Directory mode: don't even fetch non-profile section pages (budget
    # goes to exhibitors). Seed page (depth 0) always runs.
    if (dir_mode and depth > 0 and not _looks_like_profile_url(canon)
            and path0 not in ("", "/")):
        db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                          url=canon, status="skipped",
                          detail="directory mode — not a profile page"))
        db.commit()
        return
    existing0 = _find_company(db, job.user_id, canon, host0)
    if existing0 is not None and company_is_complete(db, existing0):
        db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                          url=canon, status="skipped",
                          detail="already complete — not re-crawled "
                                 f"(company #{existing0.id})"))
        db.commit()
        return
    with lock:
        if shared["pages"] >= job.max_pages or shared["cancel"].is_set():
            return
        shared["pages"] += 1
        job.pages_visited = shared["pages"]
    if SKIP_PATH_RE.search(urlparse(canon).path or ""):
        db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                          url=canon, status="skipped",
                          detail="non-entity page (login/line/etc.)"))
        db.commit()
        return
    if depth == 0 and not _robots_allowed(base, canon):
        db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                          url=canon, status="blocked",
                          detail="robots.txt disallow"))
        db.commit()
        return
    html, err = _fetch(client, canon)
    if err:
        db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                          url=canon, status="error", detail=err[:500]))
        with lock:
            shared["errors"].append(f"{canon}: {err}"[:200])
        db.commit()
        return
    data = extract_page(canon, html or "")
    via = "static"
    with lock:
        can_render = shared["render_budget"][0] > 0
    if _looks_like_js_shell(html, data) and can_render:
        rendered_html, render_err = _render_with_browser(canon)
        if rendered_html:
            with lock:
                shared["render_budget"][0] -= 1
            data = extract_page(canon, rendered_html)
            via = "rendered"
        elif render_err == "browser rendering unavailable":
            with lock:
                shared["errors"].append(
                    f"{canon}: JS shell — browser rendering unavailable"[:200])
    host = urlparse(canon).hostname or ""
    if BOTCHECK_RE.search(f"{data['title']} {(data['h1'] or '')}"):
        db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                          url=canon, status="blocked",
                          detail="bot-check/denied page — skipped save"))
        db.commit()
        time.sleep(PAGE_DELAY)
        return
    detail = (f"emails={len(data['emails'])} phones={len(data['phones'])} "
              f"via={via}")
    is_list = _is_listing_page(data, host)
    if is_list:
        nprof = len(_profile_links(data, host))
        detail += f" listing:{nprof}-profiles-queued"
        if depth == 0:
            with lock:
                shared["directory_mode"] = True
    # Save when it's a company candidate AND (not a pure index,
    # or a showroom page that happens to list its lines).
    save_ok, save_note = _save_allowed(data, canon, is_list, shared)
    detail += save_note
    if _is_company_candidate(data) and (
            not is_list or _looks_like_profile_url(canon)) and save_ok:
        # Drop rule: a BRAND-NEW company with no contacts and no official
        # website to enrich from is junk — don't save it. Existing rows are
        # never dropped, only updated; rows WITH a site stay EXCLUDED_NO_CONTACT.
        if _should_drop(db, job.user_id, canon, host, data):
            detail += " dropped: no contacts and no official website"
            db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                              url=canon, status="skipped", detail=detail[:500]))
            db.commit()
            time.sleep(PAGE_DELAY)
            return
        comp = _save_company(db, job.user_id, job.source_id,
                             canon, data, depth, lock=lock)
        if comp:
            with lock:
                shared["companies"] += 1
                shared["contacts"] += len(data["emails"]) + len(data["phones"])
            # Exhibitor profile without contacts? Try its official site.
            if (not data["emails"] and not data["phones"]
                    and data.get("official_links")):
                picked = _pick_official_site(data["official_links"], host)
                if picked:
                    extra, e_pages = _enrich_from_official_site(
                        db, job.user_id, comp, picked[1], client,
                        render_budget=shared["render_budget"],
                        job_id=job.id)
                    with lock:
                        shared["pages"] += e_pages
                        shared["contacts"] += extra
                    if extra:
                        detail += f" +enriched:{extra}-from-{picked[0]}"
    stage = "[S1]" if is_list else "[S2]"
    db.add(CrawlEvent(user_id=job.user_id, job_id=job.id,
                      url=canon, status="ok",
                      detail=f"{stage} {detail}"[:500]))
    if depth < job.max_depth:
        profiles = _profile_links(data, host)
        # Profile links first: entity pages before site wandering.
        with lock:
            for href in profiles:
                if href not in shared["seen"]:
                    shared["seen"].add(href)
                    shared["queue"].put((href, depth + 1))
        # From a listing page, follow ONLY profiles (no wandering).
        if not _is_listing_page(data, host):
            with lock:
                for href, _label in data["links"][:60]:
                    h = urlparse(href).hostname or ""
                    p = urlparse(href).path or ""
                    clean = href.split("#")[0]
                    if (h == base_host and not SKIP_PATH_RE.search(p)
                            and clean not in shared["seen"]):
                        shared["seen"].add(clean)
                        shared["queue"].put((clean, depth + 1))
    with lock:
        job.companies_found = shared["companies"]
        job.contacts_found = shared["contacts"]
        job.pages_visited = shared["pages"]
    db.commit()
    time.sleep(PAGE_DELAY)


def _worker(job_id: int, user_id: int, source_id: int | None,
            max_pages: int, max_depth: int, base: str, base_host: str,
            shared: dict) -> None:
    """One crawl worker: own HTTP client, own DB session, own browser."""
    from queue import Empty
    db = SessionLocal()
    client = httpx.Client()
    try:
        while not shared["cancel"].is_set():
            try:
                url, depth = shared["queue"].get(timeout=3)
            except Empty:
                continue
            try:
                # De-duplicated at enqueue time (seen+lock there); every
                # take here is unique work. A second check would skip
                # EVERYTHING (enqueue already marks seen) — that bug
                # once zeroed whole directory crawls.
                canon = url.split("#")[0]
                # Trailing-slash variants are the same page (/x/ == /x).
                # Without this, directories spawn duplicate company rows.
                if len(canon) > 8 and canon.endswith("/"):
                    canon = canon.rstrip("/")
                # Re-read the job row in this worker's session (fresh state)
                job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
                if not job:
                    return
                _process_url(db, client, job, canon, depth,
                             base, base_host, shared)
            except Exception as e:
                logger.warning(f"Worker error on {url}: {str(e)[:150]}")
            finally:
                shared["queue"].task_done()
    finally:
        try:
            client.close()
        except Exception:
            pass
        db.close()


def run_job(job_id: int) -> None:
    """Background crawl: adapter discovery (S1) + worker pool (S2/S3)."""
    from queue import Queue, Empty
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        if not job or job.status not in ("PENDING",):
            return
        user_id, source_id = job.user_id, job.source_id
        max_pages, max_depth, seed_url = job.max_pages, job.max_depth, job.url
        job.status = "CRAWLING"
        job.started_at = datetime.utcnow()
        db.commit()
        cancel = _cancel_flags.setdefault(job_id, threading.Event())

        base_host = urlparse(seed_url).hostname or ""
        base = f"{urlparse(seed_url).scheme}://{base_host}"
        shared: dict = {
            "lock": threading.Lock(),
            "queue": Queue(),
            "seen": set(),
            "pages": 0, "companies": 0, "contacts": 0,
            "render_budget": [max_pages],  # one render max per page, total capped
            "errors": [],
            "cancel": cancel,
            # directory_mode: seed was a fair listing (or adapter hit) — only
            # profile-URL pages become companies from then on.
            "directory_mode": False,
        }
        # Stage 1: adapter enumeration (whole directory in seconds) or seed URL
        from app.fair_adapters import discover_profiles
        profiles, adapter_err = discover_profiles(seed_url)
        if profiles:
            with shared["lock"]:
                shared["directory_mode"] = True
                for href in profiles[:max_pages * 2]:
                    if href not in shared["seen"]:
                        shared["seen"].add(href)
                        shared["queue"].put((href, 1))
            db.add(CrawlEvent(user_id=user_id, job_id=job_id, url=seed_url,
                              status="ok",
                              detail=f"[S1] adapter: {len(profiles)} profiles queued"))
            db.commit()
        else:
            if adapter_err:
                db.add(CrawlEvent(user_id=user_id, job_id=job_id, url=seed_url,
                                  status="ok",
                                  detail=f"[S1] generic crawl ({adapter_err[:100]})"))
                db.commit()
            shared["queue"].put((seed_url, 0))

        workers = max(1, settings.CRAWL_WORKERS)
        threads = [threading.Thread(
            target=_worker,
            args=(job_id, user_id, source_id, max_pages, max_depth,
                  base, base_host, shared),
            daemon=True) for _ in range(workers)]
        for t in threads:
            t.start()
        # Wait until budgeted pages are done, queue drains, or cancel.
        while True:
            time.sleep(5)
            with shared["lock"]:
                done = (shared["pages"] >= max_pages
                        or shared["queue"].unfinished_tasks == 0)
            if done or cancel.is_set():
                break
        # Drain: stop workers after current items finish
        cancel_soft = cancel.is_set()
        if not cancel_soft:
            # Let workers finish queued items up to budget, then stop them
            deadline = time.time() + max(60, max_pages * 2)
            while time.time() < deadline:
                with shared["lock"]:
                    if shared["queue"].unfinished_tasks == 0:
                        break
                time.sleep(5)

        job = db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        with shared["lock"]:
            pages, companies = shared["pages"], shared["companies"]
            errs = list(shared["errors"])
        job.status = "CANCELLED" if cancel.is_set() else "COMPLETED"
        job.pages_visited = pages
        job.companies_found = companies
        job.contacts_found = shared["contacts"]
        job.finished_at = datetime.utcnow()
        job.errors = "\n".join(errs[-10:]) or None
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
