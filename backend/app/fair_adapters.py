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


def _lasvegas_profiles() -> tuple[list[str], str | None]:
    """Las Vegas Market: A-Z exhibitor buckets via the site's public API."""
    key = "391D75C6-01EE-463C-8B51-47B2748F8ACD"  # public key embedded in their own page JS
    base = "https://www.lasvegasmarket.com"
    headers = {"User-Agent": UA, "Accept": "application/json",
               "Referer": f"{base}/exhibitor/Directory",
               "Channel": "las-vegas-market"}
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


ADAPTERS: list[tuple[str, callable]] = [
    ("lasvegasmarket.com", _lasvegas_profiles),
]


def discover_profiles(seed_url: str) -> tuple[list[str] | None, str | None]:
    """Returns (profile_urls, error). None urls = no adapter, crawl generically."""
    host = (seed_url.split("//", 1)[-1].split("/", 1)[0] or "").lower()
    for domain, fn in ADAPTERS:
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
