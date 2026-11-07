"""Free OpenStreetMap discovery provider (Nominatim + Overpass).

Backs the Business Lead Finder when no GOOGLE_MAPS_API_KEY is set (or when
GB_PROVIDER=openstreetmap): no key, no billing, zero estimated cost.

Honest tradeoffs (surfaced in the UI and README):
- Coverage: OSM holds far fewer businesses than Google, especially retail/
  wholesale — many places have no website, phone, or rating.
- Ratings/reviews do not exist in OSM: they stay None, never fabricated.
- Nominatim resolves each (city, state, country) to a bounding box (1 req,
  cached per run); Overpass then scans places inside that box — by
  name/brand regex AND, for curated keywords (cookware, furniture, ...),
  by their OSM category tags (shop=houseware etc.), which is where OSM's
  coverage actually lives.
  Location resolution failure raises — we never silently return "0 results"
  for a place we could not even find.
- A campaign without any country/city is refused: a global Overpass regex
  scan is not a sane query (Google global search still works when a key
  is configured).
- Fair use: 1s pause between real Overpass requests, exponential backoff
  on 429/5xx, and the per-user daily request budget still applies.

Contract matches `google_places.PlacesProvider`; `search()` receives the
query `context` dict because Overpass searches structured tags, not free
text.
"""
import logging
import random
import re
import time
from typing import Callable

import httpx

from app.config import settings
from app.google_places import (PlacesAPIError, PlacesProvider,
                               humanize_type)

logger = logging.getLogger(__name__)

# Nominatim's usage policy requires a descriptive User-Agent.
USER_AGENT = "LeadPilotGBLeads/1.0 (B2B lead discovery; Overpass fair use)"

# OSM tags that identify a business/place record, most specific first.
CATEGORY_KEYS = ("shop", "office", "craft", "amenity", "tourism",
                 "leisure", "healthcare", "industrial")
# Keys whose value reads better with the key appended ("hardware shop").
_STRUCTURED_KEYS = ("shop", "office")
_CLOSED_TAGS = ("disused", "abandoned", "razed", "demolished")
_STOPWORDS = {"in", "of", "the", "and", "for", "near", "a", "an", "at",
              "to", "on"}
_MISSING = object()      # bbox-cache miss sentinel
_FAILED = object()       # bbox-cache "resolution failed" sentinel

# Trailing generic retail nouns that never change which curated category a
# phrase refers to: "kitchenware stores" is the same retail category as
# "kitchenware". Only these literal words are ever stripped (never a guessed
# synonym) before falling back to the name/brand regex.
_GENERIC_RETAIL_NOUNS = {"store", "stores", "shop", "shops", "centre",
                         "center", "supplier", "suppliers", "seller",
                         "sellers", "retailer", "retailers"}

# Curated keyword -> OSM `shop` values. OSM's real strength is its category
# tags, not names: "Super Housewares" is a perfect cookware lead even though
# its name never says "cookware". The map is deliberately small and curated
# (only clear retail categories) — unmapped keywords keep the name/brand
# regex search, so nothing is ever matched on a guessed equivalence.
KEYWORD_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "cookware": ("houseware", "kitchen"),
    "kitchenware": ("houseware", "kitchen"),
    "kitchen store": ("kitchen", "houseware"),
    "kitchen shop": ("kitchen", "houseware"),
    "homeware": ("houseware", "interior_decoration"),
    "housewares": ("houseware",),
    "home decor": ("interior_decoration",),
    "hardware": ("hardware",),
    "diy": ("hardware",),
    "furniture": ("furniture",),
    "electronics": ("electronics",),
    "supermarket": ("supermarket", "grocery"),
    "grocery": ("grocery", "supermarket"),
    "bakery": ("bakery",),
    "florist": ("florist",),
    "books": ("books",),
    "stationery": ("stationery",),
    "clothing": ("clothes",),
    "shoes": ("shoes",),
    "jewelry": ("jewelry", "jewellery"),
    "pharmacy": ("pharmacy",),
    "garden centre": ("garden_centre",),
    "bathroom": ("bathroom_furnishing",),
}


# ---------------------------------------------------------------------------
# Pure builders (unit tested)
# ---------------------------------------------------------------------------

def _norm_kw(keyword: str | None) -> str:
    return re.sub(r"\s+", " ", (keyword or "")).strip().lower().strip(
        ".,!?:;()[]{}\"'/\\-")


def mapped_categories(keyword: str | None) -> tuple[str, ...]:
    """OSM shop values this keyword maps to via the curated map.

    Empty tuple when unmapped — the query stays a name/brand search.
    The full phrase is tried first, then trailing generic retail nouns are
    dropped one at a time ("kitchenware stores" -> "kitchenware"). Every
    hit is still an exact curated entry — never a guessed equivalence.
    """
    kw = _norm_kw(keyword)
    cats = KEYWORD_CATEGORY_MAP.get(kw, ())
    if cats:
        return cats
    words = kw.split()
    while len(words) > 1 and words[-1] in _GENERIC_RETAIL_NOUNS:
        words.pop()
        cats = KEYWORD_CATEGORY_MAP.get(" ".join(words), ())
        if cats:
            return cats
    return ()


def category_tag_hits(keywords: list[str] | None,
                      primary_type: str | None) -> list[tuple[str, str]]:
    """[(keyword, osm_category)] when the place's OSM category is one the
    keyword maps to. `primary_type` is the stored 'value_key' form
    ('houseware_shop', 'interior_decoration_shop'), so each mapped value is
    matched as the leading segment before '_key'. Used by the scorer to
    give a category-tag match an honest, explainable reason."""
    pt = str(primary_type or "")
    if not pt:
        return []
    hits: list[tuple[str, str]] = []
    for kw in (keywords or []):
        kw = str(kw or "").strip()
        if not kw:
            continue
        for cat in mapped_categories(kw):
            if pt == cat or pt.startswith(cat + "_"):
                hits.append((kw, cat))
                break
    return hits

def keyword_regex(keyword: str | None) -> str | None:
    """POSIX regex matching a free-text keyword inside an OSM name.

    All meaningful tokens must appear in order, unanchored:
    'copper bottles' -> 'copper.*bottle' (plural 's' stripped so both
    'Copper Bottle Co' and 'Copper Bottles' match). None when nothing
    searchable remains (stopwords/punctuation only).
    """
    if not keyword:
        return None
    tokens: list[str] = []
    for piece in str(keyword).lower().split():
        piece = piece.strip(".,!?:;()[]{}\"'/\\-")
        if not piece or piece in _STOPWORDS:
            continue
        t = re.escape(piece)
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        tokens.append(t)
    if not tokens:
        return None
    return ".*".join(tokens)


def build_overpass_query(keyword: str | None, bbox: tuple | None = None,
                         limit: int = 400, timeout: int = 25) -> str:
    """Overpass QL selecting named (or business-tagged) places in a box.

    bbox = (south, west, north, east). Regex is matched case-insensitively
    against `name` and `brand`. When the keyword maps to curated OSM
    categories (e.g. cookware -> shop=houseware|kitchen), those category
    clauses are searched TOO — OSM stores are usually tagged by category
    even when the name never contains the keyword. Without a keyword we
    fall back to any place carrying a business tag (shop/office/...).
    """
    rx = keyword_regex(keyword)
    cats = mapped_categories(keyword)
    area = ""
    if bbox:
        s, w, n, e = (float(v) for v in bbox)
        area = f"({s},{w},{n},{e})"
    lines = [f"[out:json][timeout:{max(5, int(timeout))}];", "("]
    if rx or cats:
        if rx:
            # QL strings use C escaping; re.escape already escaped
            # backslashes, only the quote needs an extra pass.
            q = rx.replace('"', '\\"')
            for typ in ("node", "way", "relation"):
                lines.append(f'  {typ}["name"~"{q}",i]{area};')
                lines.append(f'  {typ}["brand"~"{q}",i]{area};')
        if cats:
            # curated category tags for this keyword (values are plain
            # OSM shop values — no regex metacharacters, escaped anyway)
            qcat = "|".join(re.escape(c) for c in cats)
            for typ in ("node", "way", "relation"):
                lines.append(f'  {typ}["shop"~"{qcat}",i]{area};')
    else:
        for typ in ("node", "way", "relation"):
            for key in CATEGORY_KEYS:
                lines.append(f'  {typ}["{key}"~".+"]{area};')
    lines.append(");")
    lines.append(f"out center tags {max(1, int(limit))};")
    return "\n".join(lines)


def build_details_query(place_id: str) -> str | None:
    """Overpass QL fetching one element by its 'osm_type/osm_id' id."""
    m = re.match(r"^osm_(node|way|relation)/(\d+)$", (place_id or "").strip())
    if not m:
        return None
    typ, eid = m.group(1), m.group(2)
    return (f"[out:json][timeout:25]; {typ}(id:{eid}); "
            f"out center tags 1;")


# ---------------------------------------------------------------------------
# Response parsing (pure — unit tested)
# ---------------------------------------------------------------------------

def parse_osm_element(el: dict) -> dict | None:
    """Normalize one Overpass element. Missing fields become None — never
    faked. Elements without a name are useless as leads -> None."""
    tags = el.get("tags") or {}
    name = str(tags.get("name") or "").strip()
    if not name:
        return None
    typ = str(el.get("type") or "node")
    eid = el.get("id")

    # coordinates: nodes carry lat/lon; ways/relations use `out center`
    if el.get("lat") is not None and el.get("lon") is not None:
        lat, lon = el.get("lat"), el.get("lon")
    elif isinstance(el.get("center"), dict):
        lat, lon = el["center"].get("lat"), el["center"].get("lon")
    else:
        lat, lon = None, None

    # category: first matching business tag wins
    primary = None
    category = None
    for key in CATEGORY_KEYS:
        val = tags.get(key)
        if val:
            primary = f"{val}_{key}"
            category = (humanize_type(primary) if key in _STRUCTURED_KEYS
                        else humanize_type(str(val)))
            break

    # address composed only from real addr:* tags
    bits = [tags.get("addr:housenumber"), tags.get("addr:street")]
    address = " ".join(str(b) for b in bits if b) or None
    if address and tags.get("addr:postcode"):
        address = f"{address} {tags['addr:postcode']}"

    closed = any(k in tags for k in _CLOSED_TAGS)
    return {
        "place_id": f"osm_{typ}/{eid}",
        "name": name,
        "address": address,
        "city": tags.get("addr:city"),
        "state": tags.get("addr:state"),
        "country": tags.get("addr:country"),
        "latitude": lat,
        "longitude": lon,
        "website": (tags.get("website") or tags.get("contact:website")
                    or tags.get("url")),
        "phone": (tags.get("phone") or tags.get("contact:phone")
                  or tags.get("mobile") or tags.get("contact:mobile")),
        "email": tags.get("email") or tags.get("contact:email"),
        "primary_type": primary,
        "business_category": category,
        "types": [primary] if primary else [],
        "google_maps_url": f"https://www.openstreetmap.org/{typ}/{eid}",
        # OSM has no ratings — None stays None (never fabricated)
        "rating": None,
        "review_count": None,
        "business_status": "CLOSED" if closed else None,
        "description": tags.get("description"),
    }


def parse_overpass_response(payload: dict) -> list[dict]:
    out: list[dict] = []
    for el in (payload or {}).get("elements") or []:
        if not isinstance(el, dict):
            continue
        p = parse_osm_element(el)
        if p:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class OverpassProvider(PlacesProvider):
    name = "openstreetmap"
    search_label = "OpenStreetMap search"
    cost_per_call_usd = 0.0

    def __init__(self, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 max_retries: int | None = None,
                 timeout: float | None = None,
                 overpass_url: str | None = None,
                 nominatim_url: str | None = None,
                 overpass_mirror: str | None = None):
        self.transport = transport
        self.sleep = sleep
        self.max_retries = (max_retries if max_retries is not None
                            else settings.GB_SEARCH_MAX_RETRIES)
        self.timeout = timeout if timeout is not None else settings.GB_OVERPASS_TIMEOUT
        self.overpass_url = overpass_url or settings.GB_OVERPASS_URL
        mirror_raw = (overpass_mirror if overpass_mirror is not None
                      else settings.GB_OVERPASS_MIRROR_URL)
        # comma-separated failover list, tried in order after the primary
        self.mirrors: list[str] = [
            u.strip() for u in str(mirror_raw or "").split(",") if u.strip()]
        self.nominatim_url = nominatim_url or settings.GB_NOMINATIM_URL
        self.calls = 0  # requests actually issued (cost stays 0.0)
        self._last_kind: str | None = None  # network|rate|server|client
        # circuit breaker: endpoints that failed a full retry chain are
        # skipped for the rest of this run (fresh run = fresh state)
        self._dead: set[str] = set()
        self._bbox_cache: dict = {}

    # -- HTTP with retry/backoff ------------------------------------------
    def _http(self, method: str, url: str, **kw) -> httpx.Response:
        headers = dict(kw.pop("headers", {}) or {})
        headers.setdefault("User-Agent", USER_AGENT)
        last_err: PlacesAPIError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                # patient exponential backoff + jitter — busy public
                # Overpass instances need longer waits than Google's 2/4/8
                self.sleep(min(4 * (2 ** (attempt - 1)), 16)
                           + random.uniform(0, 0.5))
            try:
                if self.transport is not None:
                    with httpx.Client(transport=self.transport) as client:
                        resp = client.request(method, url, headers=headers,
                                              timeout=self.timeout, **kw)
                else:
                    resp = httpx.request(method, url, headers=headers,
                                         timeout=self.timeout, **kw)
                self.calls += 1
            except httpx.HTTPError as e:
                self._last_kind = "network"
                last_err = PlacesAPIError(f"network: {str(e)[:200]}")
                logger.warning("OSM network error (attempt %d): %s",
                               attempt + 1, e)
                continue
            if resp.status_code == 200:
                return resp
            text = resp.text[:300]
            if resp.status_code in (429, 500, 502, 503, 504):
                # transient (Overpass rate limits clear in seconds/minutes)
                self._last_kind = ("rate" if resp.status_code == 429
                                   else "server")
                last_err = PlacesAPIError(
                    f"OpenStreetMap HTTP {resp.status_code} "
                    f"(rate limit/transient): {text[:200]}")
                continue
            # other 4xx: our query or the endpoint is wrong — do not hammer
            self._last_kind = "client"
            last_err = PlacesAPIError(
                f"OpenStreetMap HTTP {resp.status_code}: {text[:200]}")
            break
        raise last_err or PlacesAPIError("unknown OpenStreetMap failure")

    def _payload(self, resp: httpx.Response) -> dict:
        try:
            payload = resp.json()
        except ValueError:
            raise PlacesAPIError("OpenStreetMap returned invalid JSON")
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            raise PlacesAPIError(
                f"OpenStreetMap: {str(err.get('message') or err)[:300]}")
        return payload if isinstance(payload, dict) else {}

    def _overpass(self, ql: str) -> dict:
        """POST to the primary Overpass endpoint, then walk the mirror
        list in order. An endpoint that failed a full retry chain is
        marked dead for the rest of this run (circuit breaker) so later
        queries fail fast instead of re-running the whole chain.
        Rate limits (429) are per-IP and never trigger the fallback."""
        endpoints = [self.overpass_url] + [
            u for u in self.mirrors if u != self.overpass_url]
        live = [u for u in endpoints if u not in self._dead]
        if not live:
            raise PlacesAPIError(
                "OpenStreetMap Overpass unavailable — every endpoint "
                f"({len(endpoints)}) failed earlier this run; retry later")
        last_err: PlacesAPIError | None = None
        for url in live:
            try:
                resp = self._http("POST", url, data={"data": ql})
            except PlacesAPIError as e:
                if self._last_kind not in ("network", "server"):
                    raise  # 429 (per-IP) or client error: surface as-is
                self._dead.add(url)
                last_err = e
                logger.warning("Overpass endpoint dead (%s) — trying next: %s",
                               url, e)
                continue
            return self._payload(resp)
        raise last_err or PlacesAPIError("unknown OpenStreetMap failure")

    # -- location resolution (Nominatim, cached) ---------------------------
    def _nominatim_bbox(self, city: str, state: str,
                        country: str) -> tuple | None:
        if not (city or state or country):
            return None
        params: dict = {"format": "jsonv2", "limit": "1",
                        "addressdetails": "0"}
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if country:
            params["country"] = country
        try:
            resp = self._http("GET", self.nominatim_url, params=params)
        except PlacesAPIError as e:
            logger.warning("Nominatim lookup failed: %s", e)
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        if not isinstance(data, list) or not data:
            return None
        bb = data[0].get("boundingbox") or []
        if len(bb) != 4:
            return None
        try:
            south, north, west, east = (float(v) for v in bb)
        except (TypeError, ValueError):
            return None
        return (south, west, north, east)

    def _resolve_bbox(self, ctx: dict) -> tuple | None:
        """bbox for the query context; None only when no location exists
        (caller must then refuse the global scan). Raises PlacesAPIError
        when a location was given but could not be resolved — honest
        failure instead of a fabricated empty result."""
        city = str(ctx.get("city") or "").strip()
        state = str(ctx.get("state") or "").strip()
        country = str(ctx.get("country") or "").strip()
        if not city and not country:
            return None
        key = (city, state, country)
        cached = self._bbox_cache.get(key, _MISSING)
        if cached is _MISSING:
            bbox = self._nominatim_bbox(city, state, country)
            if bbox is None and city and country:
                # last resort: whole country (still far better than global)
                bbox = self._nominatim_bbox("", "", country)
            cached = self._bbox_cache[key] = bbox if bbox else _FAILED
        if cached is _FAILED:
            loc = ", ".join(b for b in (city, state, country) if b)
            raise PlacesAPIError(
                f'OpenStreetMap could not resolve location "{loc}" '
                f"(Nominatim lookup failed or not found) — no results "
                f"fabricated; retry later or add GOOGLE_MAPS_API_KEY")
        return cached

    # -- contract ----------------------------------------------------------
    def search(self, query: str, max_results: int = 20,
               context: dict | None = None) -> list[dict]:
        ctx = context or {}
        if ctx.get("keyword"):
            keyword = str(ctx["keyword"]).strip()
        else:
            # no structured context: take the free-text part of
            # "kw ... in City, Country" as the keyword
            keyword = str(query or "").split(" in ")[0].strip()

        bbox = self._resolve_bbox(ctx)
        if bbox is None:
            raise PlacesAPIError(
                "OpenStreetMap search needs a country or city — global "
                "scans are not supported (add a location to the campaign, "
                "or set GOOGLE_MAPS_API_KEY for Google global search)")

        cap = max(1, min(max_results, settings.GB_MAX_RESULTS_PER_SEARCH))
        ql = build_overpass_query(
            keyword, bbox,
            limit=min(max(cap * 2, 50), 1000),
            # server-side execution budget: leave ~25s of the HTTP timeout
            # for queue wait + transfer (busy public instances queue first)
            timeout=max(20, int(self.timeout) - 25))
        payload = self._overpass(ql)
        remark = payload.get("remark")
        if remark:
            # Overpass returns partial results with a remark on timeout
            logger.warning("Overpass remark: %s", remark)
        places = parse_overpass_response(payload)
        if not places and remark:
            raise PlacesAPIError(
                f"OpenStreetMap returned no usable results: "
                f"{str(remark)[:200]}")
        if self.transport is None:
            self.sleep(1.0)  # fair use: pause between Overpass queries
        return places[:cap]

    def details(self, place_id: str) -> dict | None:
        """One-element lookup — used to backfill a missing website/phone."""
        ql = build_details_query(place_id)
        if not ql:
            return None
        payload = self._overpass(ql)
        els = payload.get("elements") or []
        if not els:
            return None
        return parse_osm_element(els[0])
