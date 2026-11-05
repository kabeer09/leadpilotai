"""Google Places API (New) provider — key stays on the server, always.

Provider abstraction (spec section 3): `PlacesProvider` defines the search
contract so additional discovery providers can be added later; tests inject
a mock provider/transport and never consume paid API credits.

Honesty rules:
- Quota/config errors surface as typed exceptions with Google's message —
  never silently swallowed or reported as "0 results".
- Transient errors (429/5xx/network) retry with exponential backoff up to
  GB_SEARCH_MAX_RETRIES; config errors (bad key, denied) never retry.
- Cost tracking is an estimate (GB_COST_PER_CALL_USD) — Google's invoice
  is the source of truth.
"""
import logging
import random
import re
import time
from typing import Callable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Only the fields we store (spec list) — keeps responses small and billing
# on the Text Search (New) basic SKU.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.location",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.primaryType",
    "places.types",
    "places.googleMapsUri",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.editorialSummary",
])
DETAILS_FIELD_MASK = ",".join([
    "id", "displayName", "formattedAddress", "addressComponents", "location",
    "websiteUri", "nationalPhoneNumber", "internationalPhoneNumber",
    "primaryType", "types", "googleMapsUri", "rating", "userRatingCount",
    "businessStatus", "editorialSummary",
])


class PlacesError(Exception):
    """Base for provider failures (message safe to show the user)."""


class PlacesConfigError(PlacesError):
    """Key missing/invalid or API not enabled — retrying won't help."""


class PlacesQuotaError(PlacesError):
    """Google quota/billing exhausted (43 / RESOURCE_EXHAUSTED)."""


class PlacesAPIError(PlacesError):
    """Other API failure after retries."""


# ---------------------------------------------------------------------------
# Query construction (pure — unit tested)
# ---------------------------------------------------------------------------

def _location_suffix(country: str | None, state: str | None,
                     city: str | None) -> str:
    bits = [b for b in (city, state, country) if b and str(b).strip()]
    return ", ".join(str(b).strip() for b in bits)


def build_queries(keywords: list[str], countries: list[str],
                  state: str | None, cities: list[str],
                  business_types: list[str] | None = None,
                  categories: list[str] | None = None) -> list[dict]:
    """One query per (keyword × location) — precise, traceable results.

    Each result can name the keyword+location that found it, which feeds
    the relevance engine's "why this matches" reasons. Returns dicts:
    {"query": str, "keyword": str, "country":..., "state":..., "city":...}
    """
    type_terms = " ".join(
        (list(business_types or [])[:1] + list(categories or [])[:1]))
    locs: list[tuple[str | None, str | None, str | None]] = []
    if cities:
        for c in cities:
            locs.append(((countries[0] if countries else None), state, c))
    elif countries:
        for c in countries:
            locs.append((c, state, None))
    else:
        locs.append((None, state, None))

    queries = []
    seen = set()
    for kw in keywords:
        for country, st, city in locs:
            parts = [kw.strip()]
            if type_terms:
                parts.append(type_terms.strip())
            suffix = _location_suffix(country, st, city)
            if suffix:
                parts.append("in " + suffix)
            q = " ".join(p for p in parts if p)
            if q in seen:
                continue
            seen.add(q)
            queries.append({"query": q, "keyword": kw.strip(),
                            "country": country, "state": st, "city": city})
    return queries


# ---------------------------------------------------------------------------
# Response parsing (pure — unit tested)
# ---------------------------------------------------------------------------

def humanize_type(primary_type: str | None) -> str | None:
    """'chinese_restaurant' -> 'Chinese restaurant'."""
    if not primary_type:
        return None
    words = primary_type.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _address_parts(p: dict) -> dict:
    """Structured city/state/country from addressComponents (never guessed
    by parsing the formatted string)."""
    out = {"city": None, "state": None, "country": None}
    for comp in p.get("addressComponents") or []:
        types = comp.get("types") or []
        long_text = comp.get("longText") or comp.get("long_name")
        if not long_text:
            continue
        if "locality" in types or "postal_town" in types:
            out["city"] = out["city"] or long_text
        elif "administrative_area_level_1" in types:
            out["state"] = out["state"] or long_text
        elif "country" in types:
            out["country"] = out["country"] or long_text
    return out


def parse_place(p: dict) -> dict:
    """Normalize one Google place. Missing fields become None — never faked."""
    display = p.get("displayName") or {}
    loc = p.get("location") or {}
    parts = _address_parts(p)
    return {
        "place_id": p.get("id"),
        "name": display.get("text"),
        "address": p.get("formattedAddress"),
        "city": parts["city"],
        "state": parts["state"],
        "country": parts["country"],
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "website": p.get("websiteUri"),
        "phone": (p.get("internationalPhoneNumber")
                  or p.get("nationalPhoneNumber")),
        "primary_type": p.get("primaryType"),
        "business_category": humanize_type(p.get("primaryType")),
        "types": p.get("types") or [],
        "google_maps_url": p.get("googleMapsUri"),
        "rating": p.get("rating"),
        "review_count": p.get("userRatingCount"),
        "business_status": p.get("businessStatus"),
        "description": (p.get("editorialSummary") or {}).get("text"),
    }


def parse_search_response(payload: dict) -> list[dict]:
    return [parse_place(p) for p in (payload.get("places") or [])]


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

class PlacesProvider:
    """Contract for business-discovery providers (Google, OpenStreetMap)."""
    name = "base"
    # Human phrase used in match reasons, e.g. 'Found via Google search: …'
    search_label = "search"
    # Estimated cost per HTTP request (0.0 = free provider)
    cost_per_call_usd = 0.0

    def search(self, query: str, max_results: int = 20,
               context: dict | None = None) -> list[dict]:
        """context carries the structured query ({keyword, country, state,
        city}) for providers that cannot search free text directly."""
        raise NotImplementedError

    def details(self, place_id: str) -> dict | None:
        raise NotImplementedError


class GooglePlacesProvider(PlacesProvider):
    name = "google_places"
    search_label = "Google search"

    @property
    def cost_per_call_usd(self) -> float:
        return settings.GB_COST_PER_CALL_USD

    def __init__(self, api_key: str | None = None,
                 timeout: float | None = None,
                 max_retries: int | None = None,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key if api_key is not None else settings.GOOGLE_MAPS_API_KEY
        self.timeout = timeout if timeout is not None else settings.GB_SEARCH_TIMEOUT
        self.max_retries = (max_retries if max_retries is not None
                            else settings.GB_SEARCH_MAX_RETRIES)
        self.transport = transport
        self.sleep = sleep
        self.calls = 0  # requests actually issued (for cost accounting)

    # -- HTTP with retry/backoff ------------------------------------------
    def _post(self, url: str, body: dict, field_mask: str) -> dict:
        if not self.api_key:
            raise PlacesConfigError(
                "GOOGLE_MAPS_API_KEY is not configured — add a Places API (New) "
                "key to the server .env (see Settings → Google Business Lead "
                "Finder).")
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": field_mask,
        }
        last_err: PlacesError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                # exponential backoff + jitter; capped at 8s
                self.sleep(min(2 ** attempt, 8) + random.uniform(0, 0.5))
            try:
                if self.transport is not None:
                    # injected transport (tests) — no real network
                    with httpx.Client(transport=self.transport) as client:
                        resp = client.post(url, json=body, headers=headers,
                                           timeout=self.timeout)
                else:
                    resp = httpx.post(url, json=body, headers=headers,
                                      timeout=self.timeout)
                self.calls += 1
            except httpx.HTTPError as e:
                last_err = PlacesAPIError(f"network: {str(e)[:200]}")
                logger.warning("Places network error (attempt %d): %s",
                               attempt + 1, e)
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    last_err = PlacesAPIError("Google returned invalid JSON")
                    break
            text = resp.text[:400]
            low = text.lower()
            # Quota/billing exhaustion FIRST: a 429 or RESOURCE_EXHAUSTED
            # can't succeed on retry — surface it as a typed quota error.
            if ("resource_exhausted" in low or "rate limit" in low
                    or "quota" in low or resp.status_code == 429):
                raise PlacesQuotaError(
                    f"Google quota/billing limit hit (HTTP {resp.status_code}): "
                    f"{text[:200]}")
            if resp.status_code in (500, 502, 503, 504):
                last_err = PlacesAPIError(
                    f"transient HTTP {resp.status_code}: {text[:200]}")
                continue
            if ("api_key_invalid" in low or "api key not valid" in low
                    or "invalid api key" in low
                    or "requests from this ip"
                    in low or "requests_blocked" in low
                    or "service_disabled" in low):
                raise PlacesConfigError(
                    f"Google rejected the API key (HTTP {resp.status_code}): "
                    f"{text[:200]}")
            if resp.status_code == 403 and "denied" in low:
                raise PlacesConfigError(
                    f"Request denied by Google: {text[:200]}")
            if resp.status_code == 403:
                raise PlacesQuotaError(
                    f"Google refused the request (HTTP 403): {text[:200]}")
            last_err = PlacesAPIError(
                f"Google HTTP {resp.status_code}: {text[:200]}")
            break  # non-retryable client error
        raise last_err or PlacesAPIError("unknown Google Places failure")

    # -- contract ----------------------------------------------------------
    def search(self, query: str, max_results: int = 20,
               context: dict | None = None) -> list[dict]:
        """Text Search (New) with pagination (20/page, Google allows 3 pages
        => 60 max). Returns normalized place dicts. `context` is unused —
        Google searches free text directly."""
        cap = max(1, min(max_results, settings.GB_MAX_RESULTS_PER_SEARCH))
        out: list[dict] = []
        page_token: str | None = None
        while len(out) < cap:
            body: dict = {"textQuery": query,
                          "pageSize": min(20, cap - len(out))}
            if page_token:
                body["pageToken"] = page_token
            payload = self._post(SEARCH_URL, body, FIELD_MASK)
            places = parse_search_response(payload)
            if not places:
                break
            out.extend(places[: cap - len(out)])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return out

    def details(self, place_id: str) -> dict | None:
        """Place Details (New) — used to backfill a missing website/phone."""
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", place_id)
        if not safe_id:
            return None
        payload = self._post(DETAILS_URL.format(place_id=safe_id), {},
                             DETAILS_FIELD_MASK)
        if not payload or payload.get("id") is None:
            return None
        return parse_place(payload)


def provider_name() -> str:
    """Which discovery provider is active (never exposes config secrets).

    auto -> Google Places when a key exists, else the free OSM provider.
    """
    mode = (settings.GB_PROVIDER or "auto").strip().lower()
    if mode == "openstreetmap":
        return "openstreetmap"
    if mode == "google":
        return "google_places"
    return "google_places" if settings.GOOGLE_MAPS_API_KEY else "openstreetmap"


def get_places_provider() -> PlacesProvider:
    """Factory: Google Places when configured, else the free OSM provider."""
    if provider_name() == "openstreetmap":
        from app.osm_places import OverpassProvider  # lazy: avoid import cycle
        return OverpassProvider()
    return GooglePlacesProvider()
