"""Fair-directory adapters (Stage-1 discovery at yesterday's volume).

Generic crawling works on any URL but sees ~10 profiles per run. Adapters
use a directory's own public listing API (the same one its web page calls)
to enumerate EVERY exhibitor in seconds, then the generic pipeline does
stages 2-3 (profile -> official site -> contact pages -> validation).

Rules: public endpoints only, polite pacing kept, any adapter failure falls
back to generic link crawling — never a hard error, never invented rows.
"""
import logging
import string
import time

import httpx
import re

logger = logging.getLogger(__name__)

UA = "LeadPilotTrialBot/0.1 (+contact via account email; respects robots.txt)"


def _andmore_profiles(base: str, channel: str) -> tuple[list[str], str | None]:
    """ANDMORE markets (Las Vegas, Atlanta, …): A-Z exhibitor buckets via
    the platform's public API (same shared key, per-market channel)."""
    key = "391D75C6-01EE-463C-8B51-47B2748F8ACD"  # public key embedded in their own page JS
    headers = {"User-Agent": UA, "Accept": "application/json",
               "Referer": f"{base}/exhibitor/Directory",
               "Channel": channel}
    try:
        with httpx.Client(timeout=20.0) as client:
            try:
                inv = client.get(
                    f"{base}/imc-api/v2/exhibitors/invalidaz?sc_apikey={key}",
                    headers=headers)
                invalid = set(inv.json()) if inv.status_code == 200 else set()
            except Exception:
                invalid = set()
            seen: set[str] = set()
            for letter in list(string.ascii_uppercase) + [str(d) for d in range(10)]:
                if letter in invalid:
                    continue
                try:
                    r = client.get(
                        f"{base}/imc-api/v2/exhibitors/az?sc_apikey={key}&az={letter}",
                        headers=headers)
                    if r.status_code != 200:
                        continue
                    for item in (r.json().get("items") or []):
                        eid = str(item.get("exhibitorId") or "").strip()
                        if eid and eid not in seen:
                            seen.add(eid)
                except Exception as e:
                    logger.warning(f"LVM bucket {letter} failed: {str(e)[:100]}")
                    continue
                time.sleep(0.4)  # polite even on JSON APIs
        urls = [f"{base}/exhibitor/{eid}" for eid in sorted(seen)]
        return urls, None if urls else "A-Z API returned no exhibitors"
    except Exception as e:
        return [], f"LVM adapter failed: {str(e)[:150]}"


def parse_ambiente_record(ex: dict, url: str, seed_url: str) -> dict | None:
    """Map one API exhibitor record to the generic extractor schema."""
    import html as _html2
    import re as _re2

    def clean(s) -> str:
        s = _html2.unescape(str(s or ""))
        return _re2.sub(r"<[^>]+>", "", s).strip()

    name = clean(ex.get("name"))
    if not name or not _re2.search(r"[A-Za-z0-9]", name):
        return None
    addr = ex.get("address") or {}
    country = clean((addr.get("country") or {}).get("label", ""))
    city = clean(addr.get("city"))
    street = clean(addr.get("street"))
    zipc = (addr.get("zip") or "").strip()
    exhib = ex.get("exhibition") or {}
    halls = exhib.get("exhibitionHall") or []
    hall, booth = "", ""
    if halls:
        hall = halls[0].get("name") or ""
        stands = halls[0].get("stand") or []
        booth = ", ".join(s.get("name", "") for s in stands if s.get("name"))
    desc = ex.get("description") or {}
    products = []
    for p in (ex.get("products") or {}).get("products") or []:
        pname = clean(p.get("name"))
        if pname:
            products.append({"name": pname[:200], "category": ""})
    homepage = (ex.get("homepage") or "").strip()
    if homepage and not homepage.startswith(("http://", "https://")):
        homepage = "https://" + homepage
    rid = str(ex.get("rewriteId") or ex.get("id") or "").strip()
    email = (addr.get("email") or "").strip().lower() or None
    tel = (addr.get("tel") or "").strip() or None
    cats = [c.get("name", "") for c in (ex.get("categories") or [])
            if isinstance(c, dict) and c.get("name")]
    return {
        "title": name, "h1": name, "profile_name": name,
        "description": clean((desc.get("text") or ""))[:2000],
        "industry": ", ".join(cats)[:255],
        "country": country or None, "city": city or None,
        "address": ", ".join(p for p in
                             [street, f"{zipc} {city}".strip(), country] if p) or None,
        "hall": hall or None, "booth": booth or None,
        "products": products,
        "official_website": homepage or None,
        "profile_url": f"{seed_url.split('?')[0]}#exhibitor={rid or name[:40]}",
        "emails": [email] if email else [],
        "phones": [tel] if tel else [],
        "whatsapp": [],
        "official_links": [],
        "links": [], "text_len": 5000,
    }


def _ambiente_records(seed_url: str) -> tuple[list[dict] | None, str | None]:
    """Ambiente/Messe Frankfurt: full exhibitor records via the public search API.

    Each record already carries name, address, hall/booth, description,
    products, categories, email, phone and homepage — stages 1+2 in one.
    Returns (records, error); records map to the generic extractor schema.
    """
    import html as _html
    import re as _re
    from urllib.parse import urlparse as _up, parse_qs as _pqs
    api = ("https://api.messefrankfurt.com/service/esb_api/exhibitor-service"
           "/api/2.1/public/exhibitor/search")
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Referer": "https://ambiente.messefrankfurt.com/",
               "apikey": "LXnMWcYQhipLAS7rImEzmZ3CkrU033FMha9cwVSngG4vbufTsAOCQQ=="}
    try:
        qs = _pqs(_up(seed_url).query)
        page_size = max(1, min(100, int((qs.get("pagesize") or ["30"])[0])))
    except Exception:
        page_size = 30

    def page_url(n: int) -> str:
        return (f"{api}?language=en-GB&q=&orderBy=name&pageNumber={n}"
                f"&pageSize={page_size}&orSearchFallback=false"
                f"&showJumpLabels=true&findEventVariable=AMBIENTE")

    def clean(s) -> str:
        s = _html.unescape(str(s or ""))
        return _re.sub(r"<[^>]+>", "", s).strip()

    def parse_record(ex: dict, url: str) -> dict | None:
        return parse_ambiente_record(ex, url, seed_url)

    def fetch_all(get) -> tuple[list[dict], dict]:
        records: list[dict] = []
        page, total = 1, None
        while page <= 60:
            try:
                r = get(page_url(page))
            except Exception as e:
                return records, {"error": str(e)[:150]}
            if getattr(r, "status_code", 0) != 200:
                return records, {"error": f"HTTP {getattr(r, 'status_code', '?')}"}
            try:
                payload = r.json()
            except Exception:
                return records, {"error": "bad JSON"}
            result = payload.get("result") or {}
            if total is None:
                meta = result.get("metaData") or {}
                total = meta.get("totalHits", meta.get("total"))
            hits = result.get("hits") or []
            if not hits:
                break
            for hit in hits:
                rec = parse_record(hit.get("exhibitor") or {}, page_url(page))
                if rec:
                    records.append(rec)
            if len(hits) < page_size:
                break
            page += 1
            time.sleep(0.6)
        return records, {"total": total}

    try:
        with httpx.Client(timeout=25.0, headers=headers) as client:
            records, info = fetch_all(client.get)
            if records or "error" not in info:
                logger.info(f"Ambiente: {len(records)} records (direct API)")
                return (records or [], None) if records else (None, "API returned no exhibitors")
    except Exception as e:
        logger.warning(f"Ambiente direct API failed ({str(e)[:100]}), trying browser session")
    # Fallback: one browser visit to establish session cookies, then API
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, locale="en-US")
            try:
                pg = ctx.new_page()
                try:
                    pg.goto(seed_url, timeout=60000,
                            wait_until="domcontentloaded")
                except Exception:
                    pass
                pg.wait_for_timeout(8000)
                records, info = fetch_all(
                    lambda u: ctx.request.get(u, headers=headers, timeout=25000))
            finally:
                browser.close()
        if records:
            logger.info(f"Ambiente: {len(records)} records (browser session)")
            return records, None
        return None, info.get("error", "API returned no exhibitors")
    except Exception as e:
        return None, f"Ambiente adapter failed: {str(e)[:150]}"


def _lasvegas_profiles() -> tuple[list[str], str | None]:
    return _andmore_profiles("https://www.lasvegasmarket.com", "las-vegas-market")


def _atlanta_profiles() -> tuple[list[str], str | None]:
    return _andmore_profiles("https://www.atlantamarket.com", "atlanta-market")


# Exhibitor-details API per ANDMORE market: (base, channel, directory pageId).
# Same public key + call convention as the markets' own page JavaScript.
ANDMORE_DETAILS = {
    "lasvegasmarket.com": ("https://www.lasvegasmarket.com", "las-vegas-market",
                           "cfa5a5c2-9bd7-4309-b356-1acdef98a65f"),
    "atlantamarket.com": ("https://www.atlantamarket.com", "atlanta-market",
                          "B20C0E90-484D-4848-8EA6-BC3B2BCC391F"),
}
ANDMORE_KEY = "391D75C6-01EE-463C-8B51-47B2748F8ACD"
ANDMORE_PROFILE_RE = None  # lazy compiled below


def fetch_exhibitor_details(page_url: str) -> dict | None:
    """Full contact card for one ANDMORE exhibitor page via OpenDetails.

    Returns {emails, phones, contact_names, address, booth, company_name,
    city, country, website, social} with the profile URL as provenance, or
    None (not ANDMORE / no match / error). One fast JSON call (~1s), polite
    delay included. `website` is the exhibitor's OWN site (not the fair's
    domain); `social` maps platform -> URL from the directory's socialMedia.
    """
    import re as _re2
    global ANDMORE_PROFILE_RE
    if ANDMORE_PROFILE_RE is None:
        ANDMORE_PROFILE_RE = _re2.compile(r"/exhibitor/(\d+)")
    host = (page_url.split("//", 1)[-1].split("/", 1)[0] or "").lower()
    cfg = next(((b, c, p) for d, (b, c, p) in ANDMORE_DETAILS.items()
                if d in host), None)
    if cfg is None:
        return None
    base, channel, page_id = cfg
    m = ANDMORE_PROFILE_RE.search(page_url)
    if not m:
        return None
    eid = m.group(1)
    headers = {"User-Agent": UA, "Accept": "application/json",
               "Referer": f"{base}/exhibitor/directory", "Channel": channel}
    url = (f"{base}/imc-api/v2/exhibitors/OpenDetails?sc_apikey={ANDMORE_KEY}"
           f"&exhibitorIds={eid}&pageId={page_id}")
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(url, headers=headers)
            if r.status_code != 200:
                return None
            rows = (r.json().get("data") or [])
            if not rows:
                return None
            row = rows[0]
            ci = row.get("directoryContactInfo") or {}
            comp = (row.get("companyDetails") or {})
            emails, names = [], []
            for key in ("companyEmail1", "companyEmail2",
                        "directoryContactEmail"):
                val = (ci.get(key) or "").strip().lower()
                if val and "@" in val and val not in emails:
                    emails.append(val)
            first, last = (ci.get("directoryContactFirstName") or "").strip(), \
                (ci.get("directoryContactLastName") or "").strip()
            if first or last:
                names.append(f"{first} {last}".strip())
            phones = []
            for key in ("primaryPhoneNo", "tollFreePhoneNo", "faxNumber"):
                val = "".join(ch for ch in str(ci.get(key) or "") if ch.isdigit())
                if len(val) >= 7 and val not in phones:
                    phones.append(val)
            addr_parts = [ci.get("address1") or "", ci.get("address2") or ""]
            city = ci.get("city") or ""
            zipc = ci.get("zip") or ""
            address = ", ".join(p.strip() for p in
                                addr_parts + [f"{zipc} {city}".strip()] if p.strip())
            booth = ""
            try:
                leases = comp.get("activeLeases") or []
                shows = (leases[0].get("showrooms") or []) if leases else []
                if shows:
                    s = shows[0]
                    booth = " ".join(p for p in
                                     [s.get("showroomBuildingName") or "",
                                      s.get("showroomFloor") or "",
                                      s.get("showroom") or ""] if p).strip()
            except Exception:
                booth = ""
            time.sleep(0.4)
            company_name = (comp.get("companyName") or "").strip() or None
            # The directory also carries the exhibitor's OWN website and
            # social-media profiles (hidden behind interaction on the page).
            info = (row.get("companyInformation") or {})
            website = (info.get("companyWebsiteUrl") or "").strip()
            if website and not website.startswith(("http://", "https://")):
                website = "https://" + website
            country = (ci.get("country") or "").strip()
            # `country` is a numeric code; the readable name lives in
            # countries[].displayValue (take the checked/first one).
            for _c in (ci.get("countries") or []):
                if isinstance(_c, dict) and (_c.get("displayValue") or "").strip():
                    country = (_c["displayValue"] or "").strip()
                    if _c.get("checked"):
                        break
            sm = row.get("socialMedia") or {}
            social = {}
            for _pk, _sk in (("facebookUrl", "facebook"),
                             ("instagramUrl", "instagram"),
                             ("twitterUrl", "twitter"),
                             ("youtubeUrl", "youtube"),
                             ("pintrestUrl", "pinterest")):
                _v = (sm.get(_pk) or "").strip()
                if _v.startswith("http"):
                    social[_sk] = _v
            if not emails and not phones and not address and not booth \
                    and not company_name:
                return None
            return {"emails": emails, "phones": phones,
                    "contact_names": names,
                    "address": address or None, "booth": booth or None,
                    "company_name": company_name,
                    "city": city or None, "country": country or None,
                    "website": website or None, "social": social or None}
    except Exception as e:
        logger.warning(f"OpenDetails {eid} failed: {str(e)[:100]}")
        return None
    return None


def _highpoint_profiles() -> tuple[list[str], str | None]:
    """High Point Market: whole directory via the public autocomplete JSON."""
    base = "https://www.highpointmarket.org"
    try:
        with httpx.Client(timeout=25.0,
                          headers={"User-Agent": UA}) as client:
            r = client.get(f"{base}/api/exhibitors/autocomplete?term=a")
            if r.status_code != 200:
                return None, f"autocomplete HTTP {r.status_code}"
            try:
                items = r.json()
            except Exception:
                return None, "autocomplete bad JSON"
            if not isinstance(items, list) or not items:
                return None, "autocomplete empty"
            seen: set[str] = set()
            for it in items:
                if not isinstance(it, dict):
                    continue
                val = str(it.get("value") or "").strip()
                if val.startswith("/exhibitor/"):
                    seen.add(base + val)
            urls = sorted(seen)
            return (urls, None) if urls else (None, "no profile links found")
    except Exception as e:
        return None, f"High Point adapter failed: {str(e)[:150]}"


# --- High Point Market: per-profile full-record builder ---------------------
# Social hosts that may appear on a profile (exhibitor's own accounts vs the
# organizer's footer accounts — the latter are filtered below).
_HP_SOCIAL_HOSTS = ("facebook.", "fb.me", "instagram.", "twitter.",
                    "x.com", "youtube.", "youtu.be", "pinterest.",
                    "linkedin.", "tiktok.")
# Owner tokens: the organizer's social URLs sit in EVERY profile footer.
# Matching runs on the alphanumeric-only URL, so hyphens/case don't matter;
# the channel-id prefix covers their YouTube link (no name inside its URL).
_HP_OWNER_TOKENS = ("highpointmarket", "hpmarket", "ucdq7mjl")
# The exhibitor's phone only comes from its own label — the page footer also
# carries the market's phone (336.869.1000) and junk digit runs, and a shared
# phone would make _find_company collapse distinct exhibitors into one row.
_HP_PHONE_RE = re.compile(
    r"(?:Corporate\s+)?Phone\s*:\s*([+(]?\d[\d\s().\-]{6,}\d)", re.I)
# Showroom block: "… Back to Search <building/suite/floor> Shuttle Stop: …".
_HP_BOOTH_RE = re.compile(
    r"Back to Search\s+(.{3,90}?)\s+(?:Shuttle Stop|Neighborhood|"
    r"Corporate\s+Phone|Phone)\s*:", re.I)


def _social_platform(host: str) -> str | None:
    """Map a social host to the platform key used for Contact kinds."""
    if "facebook." in host or "fb.me" in host:
        return "facebook"
    if "instagram." in host:
        return "instagram"
    if "twitter." in host or host.startswith("x.com"):
        return "twitter"
    if "youtube." in host or "youtu.be" in host:
        return "youtube"
    if "pinterest." in host:
        return "pinterest"
    if "linkedin." in host:
        return "linkedin"
    if "tiktok." in host:
        return "tiktok"
    return None


def _hp_org_ld(soup) -> dict | None:
    """The profile's Organization JSON-LD (real name + real description).

    The block ships with a trailing brace and sometimes raw control chars —
    parse leniently (HTML-unescape, strip control chars, first-value
    raw_decode) and accept only the Organization node (BusinessEvent blocks
    share the same script tag and carry the event's, not the company's,
    name/description)."""
    import html as _html
    import json as _json
    for ld in soup.find_all("script", type="application/ld+json"):
        raw = (ld.string if ld.string is not None else ld.get_text()) or ""
        if '"Organization"' not in raw:
            continue
        txt = re.sub(r"[\x00-\x1f]", " ", _html.unescape(raw))
        try:
            obj, _end = _json.JSONDecoder().raw_decode(txt.lstrip())
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("@type") == "Organization":
            return obj
    return None


def parse_highpoint_profile(url: str, html: str) -> dict | None:
    """One High Point /exhibitor/{id} page -> a full exhibitor record.

    The profiles are server-rendered, so a single fetch yields the
    exhibitor's name + Organization JSON-LD description, the LABELED
    corporate phone, the showroom block, their own website and their own
    social links. The organizer's footer values never enter: the phone only
    from its 'Phone:' label, links only from non-owner domains, socials only
    non-owner tokens (frequency-corrected afterwards in _highpoint_records).
    Email isn't published on profiles — records ingest enriches it from each
    exhibitor's own site (Stage 3).

    Returns a record in the generic extractor schema, or None if empty."""
    from urllib.parse import urlparse
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    org = _hp_org_ld(soup) or {}
    h1_el = soup.find("h1")
    h1 = (h1_el.get_text(" ", strip=True) if h1_el else "")
    title_el = soup.title
    title = (title_el.get_text(strip=True) if title_el else "")
    name = (org.get("name") or h1 or
            re.sub(r"\s*-\s*High Point Market\s*$", "", title)).strip()
    if not name:
        return None
    desc = re.sub(r"\s+", " ", str(org.get("description") or "")).strip()
    phone_m = _HP_PHONE_RE.search(text)
    booth_m = _HP_BOOTH_RE.search(text)
    booth = (re.sub(r"\s+", " ", booth_m.group(1)).strip()[:100]
             if booth_m else None)

    links: list[tuple[str, str, str]] = []
    social: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        try:
            host = (urlparse(href).hostname or "").lower()
        except Exception:
            continue
        if not host:
            continue
        if any(t in host for t in _HP_SOCIAL_HOSTS):
            plat = _social_platform(host)
            key = re.sub(r"[^a-z0-9]", "", href.lower())
            if plat and plat not in social \
                    and not any(t in key for t in _HP_OWNER_TOKENS):
                social[plat] = href
            continue
        if host.endswith("highpointmarket.org"):
            continue
        if any(g in host for g in ("google.", "apple.com", "microsoft.com")):
            continue
        if all(e[0] != host for e in links):
            links.append((host, href, a.get_text(" ", strip=True)[:80]))

    # Lazy import: crawler imports this module, so keep the edge one-way at
    # call time. Picks the exhibitor's independent domain (never the fair's).
    from app.crawler import _pick_official_site
    picked = _pick_official_site(links, "www.highpointmarket.org")

    return {
        "title": name, "h1": name, "profile_name": name,
        "description": desc,
        "industry": "", "country": None, "city": None, "address": None,
        "hall": None, "booth": booth,
        "products": [],
        "official_website": picked[1] if picked else None,
        "profile_url": url,
        "emails": [],  # not published on the profile; Stage-3 fills it
        "phones": [phone_m.group(1)] if phone_m else [],
        "whatsapp": [],
        "official_links": links,
        "links": [], "text_len": len(text),
        "social": social,
    }


def _hp_drop_owner_socials(parsed: list[dict]) -> None:
    """Remove organizer-wide social links from records (in-place).

    The organizer's accounts appear in every profile footer while an
    exhibitor's own links appear on their profile only — so any URL seen on
    at least half the parsed pages is not the exhibitor's account (this
    catches accounts whose URLs carry no owner token)."""
    freq: dict[str, int] = {}
    for rec in parsed:
        for u in (rec.get("social") or {}).values():
            k = re.sub(r"[^a-z0-9]", "", u.lower())
            freq[k] = freq.get(k, 0) + 1
    thresh = max(2, int((len(parsed) + 1) / 2))
    for rec in parsed:
        rec["social"] = {
            p: u for p, u in (rec.get("social") or {}).items()
            if freq.get(re.sub(r"[^a-z0-9]", "", u.lower()), 0) < thresh}


def _highpoint_records(_seed_url: str,
                       max_pages: int | None = None
                       ) -> tuple[list[dict] | None, str | None]:
    """High Point Market: full exhibitor records from the public directory.

    Enumerates the directory (autocomplete JSON), then builds one record per
    /exhibitor/{id} profile via parse_highpoint_profile. max_pages caps how
    MANY exhibitors are built (it is the job's page budget; discovery itself
    is unbilled, and the per-record Stage-3 enrichment is billed separately
    by _ingest_adapter_records)."""
    urls, err = _highpoint_profiles()
    if not urls:
        return None, err or "High Point directory returned no exhibitor profiles"
    limit = max(1, min(int(max_pages or 1500), 1500))

    parsed: list[dict] = []
    with httpx.Client(timeout=30.0, headers={"User-Agent": UA},
                      follow_redirects=True) as client:
        for url in urls:
            if len(parsed) >= limit:
                break
            html = None
            for _attempt in range(2):
                try:
                    r = client.get(url)
                except Exception:
                    r = None
                if r is not None and r.status_code == 200 and r.text:
                    html = r.text
                    break
                time.sleep(3.0)   # throttling/5xx: one calm retry, then skip
            if not html:
                continue
            time.sleep(0.25)      # polite pacing across profile fetches
            try:
                rec = parse_highpoint_profile(url, html)
            except Exception as e:
                logger.warning(f"High Point profile failed {url}: "
                               f"{str(e)[:100]}")
                continue
            if rec:
                parsed.append(rec)
    if not parsed:
        return None, "no High Point profile could be parsed"
    _hp_drop_owner_socials(parsed)
    logger.info(f"High Point: {len(parsed)} exhibitor records built")
    return parsed, None


def _hometex_profiles() -> tuple[list[str], str | None]:
    """Hometex (Turkey): exhibitor list page with links to individual profiles."""
    base = "https://hometex.com.tr"
    try:
        with httpx.Client(timeout=25.0,
                          headers={"User-Agent": UA}) as client:
            r = client.get(f"{base}/en/2026-exhibitor-list")
            if r.status_code != 200:
                return None, f"exhibitor list HTTP {r.status_code}"
            seen: set[str] = set()
            for m in re.finditer(r'href="(/en/[a-z0-9-]+-\d+)"', r.text):
                seen.add(base + m.group(1))
            urls = sorted(seen)
            return (urls, None) if urls else (None, "no profile links found")
    except Exception as e:
        return None, f"Hometex adapter failed: {str(e)[:150]}"


def _springfair_profiles() -> tuple[list[str], str | None]:
    """Spring Fair (UK): exhibitor listing page."""
    base = "https://www.springfair.com"
    try:
        with httpx.Client(timeout=25.0,
                          headers={"User-Agent": UA}) as client:
            r = client.get(f"{base}/exhibitors")
            if r.status_code != 200:
                return None, f"exhibitor list HTTP {r.status_code}"
            seen: set[str] = set()
            for m in re.finditer(r'href="(/?exhibitors/[a-z0-9-]+)"', r.text):
                path = m.group(1)
                if not path.startswith('/'):
                    path = '/' + path
                seen.add(base + path)
            urls = sorted(seen)
            return (urls, None) if urls else (None, "no profile links found")
    except Exception as e:
        return None, f"Spring Fair adapter failed: {str(e)[:150]}"


def _topdrawer_profiles() -> tuple[list[str], str | None]:
    """Top Drawer (UK): A-Z brands page with exhibitor modal links."""
    base = "https://www.topdrawer.co.uk"
    try:
        with httpx.Client(timeout=25.0,
                          headers={"User-Agent": UA}) as client:
            r = client.get(f"{base}/atozbrands?&sortby=title%20asc&searchgroup=2032BE9D-exhibitors")
            if r.status_code != 200:
                return None, f"brands page HTTP {r.status_code}"
            seen: set[str] = set()
            for m in re.finditer(r"openRemoteModal\('([^']+)'", r.text):
                path = m.group(1).strip()
                if not path.startswith("exhibitors/"):
                    continue
                if not path.startswith("/"):
                    path = "/" + path
                seen.add(base + path)
            urls = sorted(seen)
            return (urls, None) if urls else (None, "no exhibitor links found")
    except Exception as e:
        return None, f"Top Drawer adapter failed: {str(e)[:150]}"


URL_ADAPTERS: list[tuple[str, callable]] = [
    ("lasvegasmarket.com", _lasvegas_profiles),
    ("atlantamarket.com", _atlanta_profiles),
    ("highpointmarket.org", _highpoint_profiles),
    ("hometex.com.tr", _hometex_profiles),
    ("springfair.com", _springfair_profiles),
    ("topdrawer.co.uk", _topdrawer_profiles),
]


def discover_profiles(seed_url: str) -> tuple[list[str] | None, str | None]:
    """URL-list adapters. Returns (profile_urls, error)."""
    host = (seed_url.split("//", 1)[-1].split("/", 1)[0] or "").lower()
    for domain, fn in URL_ADAPTERS:
        if domain in host:
            try:
                urls, err = fn()
                if urls:
                    logger.info(f"Adapter {domain}: {len(urls)} profiles")
                    return urls, None
                return None, err or "adapter found nothing"
            except Exception as e:
                return None, f"adapter error: {str(e)[:150]}"
    return None, None


def discover_records(seed_url: str, max_pages: int | None = None
                     ) -> tuple[list[dict] | None, str | None]:
    """Full-record adapters (Ambiente, High Point). Returns (records, error).

    max_pages is forwarded to adapters that build one record per fetched
    page (High Point), so the job's page budget caps their record count."""
    host = (seed_url.split("//", 1)[-1].split("/", 1)[0] or "").lower()
    if "messefrankfurt.com" in host:
        try:
            return _ambiente_records(seed_url)
        except Exception as e:
            return None, f"adapter error: {str(e)[:150]}"
    if "highpointmarket.org" in host:
        try:
            return _highpoint_records(seed_url, max_pages)
        except Exception as e:
            return None, f"adapter error: {str(e)[:150]}"
    return None, None
