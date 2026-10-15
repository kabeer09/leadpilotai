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


URL_ADAPTERS: list[tuple[str, callable]] = [
    ("lasvegasmarket.com", _lasvegas_profiles),
    ("atlantamarket.com", _atlanta_profiles),
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


def discover_records(seed_url: str) -> tuple[list[dict] | None, str | None]:
    """Full-record adapters (Ambiente). Returns (records, error)."""
    host = (seed_url.split("//", 1)[-1].split("/", 1)[0] or "").lower()
    if "messefrankfurt.com" in host:
        try:
            return _ambiente_records(seed_url)
        except Exception as e:
            return None, f"adapter error: {str(e)[:150]}"
    return None, None
