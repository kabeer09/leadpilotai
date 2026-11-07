"""Free OpenStreetMap provider (Nominatim + Overpass) for Business Lead
Finder.

Honesty contracts under test:
- every Nominatim/Overpass response is mocked — zero network, zero cost;
- keyword -> POSIX regex building (plural/stem/stopword/escaping rules);
- a location that cannot be resolved raises — never a fabricated "0 results";
- a campaign with no location refuses the global scan with a clear error;
- rate limits retry with backoff then surface as typed PlacesAPIError;
- OSM has no ratings: rating/review_count stay None, never invented;
- emails stored only when literally published (OSM tag) + evidence row;
- closed/disused places are skipped; factory picks Google iff a key exists.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app import gbleads_jobs
from app import config as cfg
from app.google_places import (GooglePlacesProvider, PlacesAPIError,
                               PlacesQuotaError, get_places_provider,
                               provider_name)
from app.models_gbleads import BusinessLead, LeadEvidence, SearchCampaign
from app.osm_places import (OverpassProvider, build_details_query,
                            build_overpass_query, category_tag_hits,
                            keyword_regex, mapped_categories,
                            parse_osm_element, parse_overpass_response)
from app.gb_scoring import score_relevance

client = TestClient(app)

BBOX = (-34.0, 150.8, -33.5, 151.8)          # (S, W, N, E)
NOMINATIM_HIT = [{"boundingbox": ["-34.0", "-33.5", "150.8", "151.8"]}]


@pytest.fixture(autouse=True)
def osm_env(monkeypatch):
    """No Google key, offline, deterministic budgets."""
    monkeypatch.setattr(cfg.settings, "GOOGLE_MAPS_API_KEY", "")
    monkeypatch.setattr(cfg.settings, "GB_PROVIDER", "auto")
    monkeypatch.setattr(cfg.settings, "GB_REQUEST_DELAY", 0.0)
    monkeypatch.setattr(cfg.settings, "GB_DAILY_API_CALLS", 500)
    yield


def _user(email):
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _wait_search(campaign_id, timeout=15.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = SessionLocal()
        try:
            status = db.query(SearchCampaign.status).filter(
                SearchCampaign.id == campaign_id).scalar()
        finally:
            db.close()
        if status not in ("PENDING", "RUNNING"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"search {campaign_id} did not finish in {timeout}s")


def _node(**over):
    el = {"type": "node", "id": 101, "lat": -33.87, "lon": 151.21,
          "tags": {"name": "Sydney Copper Bottle Traders",
                   "shop": "hardware",
                   "phone": "+61 2 9876 5432",
                   "website": "https://coppertraders.example",
                   "email": "sales@coppertraders.example",
                   "addr:housenumber": "12",
                   "addr:street": "George Street",
                   "addr:postcode": "2000",
                   "addr:city": "Sydney"}}
    el.update(over)
    return el


def _route(nominatim=None, elements=None, overpass_statuses=None,
           mirror_status=None):
    """MockTransport router for Nominatim + Overpass. Returns (state,
    transport). overpass_statuses: list of HTTP codes the PRIMARY Overpass
    endpoint serves in order (last one repeats). mirror_status: None =
    every mirror answers 200; an int = every mirror answers with that
    code; a dict = per-mirror codes keyed by URL substring
    (e.g. {"mail.ru": 503})."""
    state = {"nominatim": 0, "overpass": 0, "mirror": 0}
    nom = NOMINATIM_HIT if nominatim is None else nominatim
    statuses = list(overpass_statuses or [])

    def mirror_code(url):
        if isinstance(mirror_status, dict):
            return next((c for key, c in mirror_status.items()
                         if key in url), None)
        return mirror_status

    def handler(request):
        url = str(request.url)
        if "nominatim" in url:
            state["nominatim"] += 1
            return httpx.Response(200, json=nom)
        if "overpass-api.de" in url:            # primary endpoint
            state["overpass"] += 1
            code = statuses.pop(0) if statuses else 200
            if code != 200:
                return httpx.Response(code, text="server busy")
            return httpx.Response(
                200, json={"elements": list(elements or [])})
        state["mirror"] += 1                    # any mirror endpoint
        code = mirror_code(url)
        if code:
            return httpx.Response(code, text="mirror down")
        return httpx.Response(
            200, json={"elements": list(elements or [])})

    return state, httpx.MockTransport(handler)


def _provider(transport, recorder=None):
    return OverpassProvider(transport=transport,
                            sleep=(recorder.append if recorder is not None
                                   else (lambda s: None)))


# ---------------------------------------------------------------------------
# pure builders
# ---------------------------------------------------------------------------

def test_keyword_regex_stemming_and_stopwords():
    assert keyword_regex("copper bottles") == "copper.*bottle"
    assert keyword_regex("The Copper Bottle") == "copper.*bottle"
    assert keyword_regex("glass") == "glass"       # 'ss' never stripped
    assert keyword_regex("shoes") == "shoe"
    assert keyword_regex("  ") is None
    assert keyword_regex(None) is None


def test_keyword_regex_escapes_posix_specials():
    rx = keyword_regex("c++ pipes")
    assert rx is not None and "\\+" in rx and ".*" in rx


def test_build_overpass_query_bbox_and_limit():
    ql = build_overpass_query("copper bottles", BBOX, limit=100)
    assert ql.startswith("[out:json][timeout:25];")
    assert ('node["name"~"copper.*bottle",i]'
            "(-34.0,150.8,-33.5,151.8);") in ql
    assert 'way["brand"~"copper.*bottle",i]' in ql
    assert 'relation["name"~' in ql
    assert ql.rstrip().endswith("out center tags 100;")


def test_build_overpass_query_business_tags_without_keyword():
    ql = build_overpass_query("", BBOX, limit=10)
    assert 'node["shop"~".+"]' in ql and 'relation["amenity"~".+"]' in ql
    assert '"name"~' not in ql


def test_build_overpass_query_mapped_keyword_adds_category_clauses():
    """A curated keyword searches name/brand AND its OSM categories —
    OSM stores are usually tagged by category, not by name."""
    ql = build_overpass_query("cookware", BBOX, limit=50)
    # name/brand regex still searched
    assert 'node["name"~"cookware",i]' in ql
    assert 'relation["brand"~"cookware",i]' in ql
    # curated category clauses added alongside
    assert 'node["shop"~"houseware|kitchen",i]' in ql
    assert 'way["shop"~"houseware|kitchen",i]' in ql
    assert ql.rstrip().endswith("out center tags 50;")


def test_build_overpass_query_unmapped_keyword_stays_name_only():
    """No guessed equivalences: an unmapped keyword is name/brand only."""
    ql = build_overpass_query("copper bottles", BBOX, limit=50)
    assert 'node["name"~"copper.*bottle",i]' in ql
    assert '["shop"~"' not in ql


def test_mapped_categories_is_curated_and_normalized():
    assert mapped_categories("Kitchenware") == ("houseware", "kitchen")
    assert mapped_categories("  Cookware. ") == ("houseware", "kitchen")
    assert mapped_categories("copper bottles") == ()
    assert mapped_categories(None) == ()
    # trailing generic retail nouns are stripped before the lookup, but the
    # hit is still an exact curated entry ("kitchenware stores" = the
    # "kitchenware" category, not a guessed equivalence)
    assert mapped_categories("kitchenware stores") == ("houseware", "kitchen")
    assert mapped_categories("Kitchenware Stores") == ("houseware", "kitchen")
    assert mapped_categories("furniture shops") == ("furniture",)
    # full-key entries win before any stripping ("garden centre" is itself
    # a curated key)
    assert mapped_categories("garden centre") == ("garden_centre",)
    # stripping never invents a mapping: no curated entry remains -> ()
    assert mapped_categories("copper bottles store") == ()
    assert mapped_categories("kitchen utensils") == ()


def test_phrase_with_generic_noun_builds_category_query():
    # "kitchenware stores" now gets the shop-category clauses, not just
    # the name regex (Romford had no place *named* "kitchenware store")
    ql = build_overpass_query("kitchenware stores", BBOX, limit=50)
    assert '["shop"~"' in ql
    assert "houseware" in ql and "kitchen" in ql
    # unmapped phrases keep the honest name-only fallback
    ql2 = build_overpass_query("kitchen utensils", BBOX, limit=50)
    assert '["shop"~"' not in ql2


def test_category_tag_hits_matches_primary_type_segment():
    assert category_tag_hits(["cookware"], "houseware_shop") == [
        ("cookware", "houseware")]
    # multi-word keyword + underscored OSM value
    assert category_tag_hits(["home decor"],
                             "interior_decoration_shop") == [
        ("home decor", "interior_decoration")]
    # unrelated category -> no hit (never force a match)
    assert category_tag_hits(["cookware"], "furniture_shop") == []
    assert category_tag_hits(["cookware"], None) == []
    assert category_tag_hits([], "houseware_shop") == []


def test_score_relevance_tag_hits_add_reason_and_weight():
    # without the tag hit: no evidence at all
    s0, _l0, r0 = score_relevance(["cookware"], "Super Housewares",
                                  "Houseware shop", [])
    assert s0 == 0
    assert all("OpenStreetMap" not in r for r in r0)
    # with the curated tag hit: +20 and a reason naming the exact tag
    s1, _l1, r1 = score_relevance(["cookware"], "Super Housewares",
                                  "Houseware shop", [],
                                  tag_hits=[("cookware", "houseware")])
    assert s1 == 20
    assert ("Keyword 'cookware' maps to the OpenStreetMap category "
            "'houseware'") in r1
    # when the category text already matched literally, the tag hit must
    # not double-count the same fact
    s2, _l2, _r2 = score_relevance(["houseware"], "X", "Houseware shop",
                                   [], tag_hits=[("houseware", "houseware")])
    assert s2 == 20


def test_build_details_query():
    assert build_details_query("osm_way/55") == (
        "[out:json][timeout:25]; way(id:55); out center tags 1;")
    assert build_details_query("bogus-place-id") is None
    assert build_details_query("") is None


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def test_parse_osm_element_full_record():
    p = parse_osm_element(_node())
    assert p["place_id"] == "osm_node/101"
    assert p["name"] == "Sydney Copper Bottle Traders"
    assert p["address"] == "12 George Street 2000"
    assert p["city"] == "Sydney"
    assert p["website"] == "https://coppertraders.example"
    assert p["phone"] == "+61 2 9876 5432"
    assert p["email"] == "sales@coppertraders.example"
    assert p["primary_type"] == "hardware_shop"
    assert p["business_category"] == "Hardware shop"
    # OSM has no ratings — must stay None, never invented
    assert p["rating"] is None and p["review_count"] is None
    assert p["business_status"] is None
    assert p["google_maps_url"] == "https://www.openstreetmap.org/node/101"


def test_parse_osm_element_missing_fields_and_closed():
    # nameless elements are useless as leads
    assert parse_osm_element({"type": "node", "id": 1,
                              "tags": {"shop": "hardware"}}) is None
    # ways report coordinates via `center`
    way = parse_osm_element({
        "type": "way", "id": 9,
        "center": {"lat": 52.5, "lon": 13.4},
        "tags": {"name": "Old Copper Co", "disused": "yes",
                 "amenity": "restaurant"}})
    assert way["latitude"] == 52.5 and way["longitude"] == 13.4
    assert way["business_status"] == "CLOSED"
    assert way["primary_type"] == "restaurant_amenity"
    assert way["business_category"] == "Restaurant"
    # contact:* fallbacks
    p = parse_osm_element({"type": "node", "id": 2, "lat": 1, "lon": 2,
                           "tags": {"name": "X",
                                    "contact:website": "https://x.example",
                                    "contact:email": "hi@x.example"}})
    assert p["website"] == "https://x.example"
    assert p["email"] == "hi@x.example"


def test_parse_overpass_response_filters_junk():
    out = parse_overpass_response({"elements": [
        _node(),
        {"type": "node", "id": 5, "tags": {"shop": "hardware"}},
        "not-a-dict",
    ]})
    assert len(out) == 1 and out[0]["name"].startswith("Sydney")


# ---------------------------------------------------------------------------
# provider HTTP (mock transport — zero network, zero cost)
# ---------------------------------------------------------------------------

def test_provider_search_resolves_location_and_counts_free():
    state, transport = _route(elements=[_node()])
    p = _provider(transport)
    places = p.search("copper bottles Wholesaler in Sydney, Australia",
                      max_results=10,
                      context={"keyword": "copper bottles",
                               "city": "Sydney", "country": "Australia"})
    assert len(places) == 1
    assert places[0]["place_id"] == "osm_node/101"
    assert p.calls == 2                      # 1 Nominatim + 1 Overpass
    assert p.cost_per_call_usd == 0.0        # free provider
    assert state["nominatim"] == 1 and state["overpass"] == 1


def test_provider_resolves_location_once_per_run():
    state, transport = _route(elements=[])
    p = _provider(transport)
    ctx = {"keyword": "copper", "city": "Sydney", "country": "Australia"}
    p.search("copper in Sydney, Australia", context=ctx)
    p.search("bottles in Sydney, Australia",
             context=dict(ctx, keyword="bottles"))
    assert state["nominatim"] == 1           # cached per (city, state, country)
    assert state["overpass"] == 2


def test_provider_unresolvable_location_raises_honestly():
    state, transport = _route(nominatim=[])   # Nominatim finds nothing
    p = _provider(transport)
    ctx = {"keyword": "copper", "city": "Nowhereville",
           "country": "Australia"}
    with pytest.raises(PlacesAPIError, match="could not resolve"):
        p.search("copper in Nowhereville, Australia", context=ctx)
    # failure is cached: a second query must not hammer Nominatim again
    with pytest.raises(PlacesAPIError, match="could not resolve"):
        p.search("bottles in Nowhereville, Australia", context=ctx)
    assert state["nominatim"] == 2            # city attempt + country fallback


def test_provider_refuses_global_scan_without_location():
    state, transport = _route(elements=[_node()])
    p = _provider(transport)
    with pytest.raises(PlacesAPIError, match="needs a country or city"):
        p.search("copper bottles", context={"keyword": "copper bottles"})
    assert state["nominatim"] == 0 and state["overpass"] == 0


def test_provider_retries_rate_limit_then_succeeds():
    state, transport = _route(elements=[_node()],
                              overpass_statuses=[429, 429])
    sleeps: list = []
    p = OverpassProvider(transport=transport, sleep=sleeps.append)
    places = p.search("copper in Sydney, Australia",
                      context={"keyword": "copper", "city": "Sydney",
                               "country": "Australia"})
    assert len(places) == 1
    assert state["overpass"] == 3            # 2 rate-limited + 1 ok
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)  # backoff ran


def test_provider_persistent_rate_limit_is_typed_api_error():
    state, transport = _route(elements=[],
                              overpass_statuses=[429, 429, 429, 429])
    p = _provider(transport)
    with pytest.raises(PlacesAPIError) as excinfo:
        p.search("copper in Sydney, Australia",
                 context={"keyword": "copper", "city": "Sydney",
                          "country": "Australia"})
    assert "429" in str(excinfo.value)
    # Overpass rate limits are transient — typed as API error (the campaign
    # continues to the next query), NOT as a fatal quota/config error.
    assert not isinstance(excinfo.value, PlacesQuotaError)
    assert state["overpass"] == 4             # 1 + max_retries(3)
    # 429 is per-IP: the mirror would be rate-limited too — no fallback
    assert state["mirror"] == 0


def test_provider_falls_back_to_mirror_when_primary_busy():
    # live observed: the public instance answers 502 "at capacity"
    state, transport = _route(elements=[_node()],
                              overpass_statuses=[502, 502, 502, 502])
    sleeps: list = []
    p = OverpassProvider(transport=transport, sleep=sleeps.append)
    places = p.search("copper in Sydney, Australia",
                      context={"keyword": "copper", "city": "Sydney",
                               "country": "Australia"})
    assert len(places) == 1
    assert state["nominatim"] == 1
    assert state["overpass"] == 4             # primary exhausted retries
    assert state["mirror"] == 1               # free mirror served the query
    assert len(sleeps) == 3 and all(s > 0 for s in sleeps)


def test_provider_circuit_breaker_fails_fast_when_all_die():
    # primary 502 chain -> every mirror 503 chain: all marked dead; the
    # next query must fail instantly instead of re-running ~12 HTTP attempts
    state, transport = _route(elements=[],
                              overpass_statuses=[502, 502, 502, 502],
                              mirror_status=503)
    p = _provider(transport)
    ctx = {"keyword": "copper", "city": "Sydney", "country": "Australia"}
    with pytest.raises(PlacesAPIError, match="503"):
        p.search("copper in Sydney, Australia", context=ctx)
    assert state["overpass"] == 4             # primary: 1 + retries(3)
    assert state["mirror"] == 8               # both mirrors: 4 + 4
    # second query: bbox cached, all endpoints dead -> immediate error
    with pytest.raises(PlacesAPIError, match="every endpoint"):
        p.search("bottles in Sydney, Australia", context=ctx)
    assert state["overpass"] == 4 and state["mirror"] == 8  # no new HTTP


def test_provider_walks_mirror_list_until_one_answers():
    # primary at capacity, first mirror (mail.ru) down, second (kumi) up
    state, transport = _route(elements=[_node()],
                              overpass_statuses=[502, 502, 502, 502],
                              mirror_status={"mail.ru": 503})
    p = _provider(transport)
    places = p.search("copper in Sydney, Australia",
                      context={"keyword": "copper", "city": "Sydney",
                               "country": "Australia"})
    assert len(places) == 1
    assert state["overpass"] == 4             # primary exhausted retries
    assert state["mirror"] == 5               # mail.ru 4 (dead) + kumi 1


def test_provider_details_backfill():
    state, transport = _route(elements=[_node()])
    p = _provider(transport)
    det = p.details("osm_node/101")
    assert det and det["website"] == "https://coppertraders.example"
    before = p.calls
    assert p.details("not-an-osm-id") is None
    assert p.calls == before                  # invalid id: no HTTP at all


# ---------------------------------------------------------------------------
# factory + API surface
# ---------------------------------------------------------------------------

def test_factory_picks_provider_by_key_and_setting(monkeypatch):
    # auto + no key -> free OSM
    assert provider_name() == "openstreetmap"
    assert isinstance(get_places_provider(), OverpassProvider)
    # auto + key -> Google
    monkeypatch.setattr(cfg.settings, "GOOGLE_MAPS_API_KEY", "k")
    assert provider_name() == "google_places"
    assert isinstance(get_places_provider(), GooglePlacesProvider)
    # forced OSM even with a key (free mode)
    monkeypatch.setattr(cfg.settings, "GB_PROVIDER", "openstreetmap")
    assert provider_name() == "openstreetmap"
    assert isinstance(get_places_provider(), OverpassProvider)
    # forced Google without a key: provider exists but campaigns 400
    monkeypatch.setattr(cfg.settings, "GB_PROVIDER", "google")
    monkeypatch.setattr(cfg.settings, "GOOGLE_MAPS_API_KEY", "")
    assert provider_name() == "google_places"
    assert isinstance(get_places_provider(), GooglePlacesProvider)


def test_create_campaign_works_without_google_key():
    t = _user("osm-nokey@test.example")
    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "OSM draft", "keywords": ["copper bottles"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 10, "start": False,
    }, headers=_auth(t))
    assert r.status_code == 201, r.text


def test_settings_reports_provider_without_leaking_anything():
    t = _user("osm-settings@test.example")
    r = client.get("/api/gbleads/settings", headers=_auth(t))
    assert r.status_code == 200
    data = r.json()
    assert data["provider"]["name"] == "openstreetmap"
    assert data["provider"]["free"] is True
    assert data["provider"]["google_configured"] is False
    assert data["google_places"]["configured"] is False
    assert data["google_places"]["cost_per_call_usd"] == 0.0
    assert "OpenStreetMap" in data["note"]
    assert "key" in r.text  # wording only — no secret value exists to leak

    # with a key the settings flip to Google
    cfg.settings.GOOGLE_MAPS_API_KEY = "SUPER-SECRET-OSM-TEST"
    try:
        r = client.get("/api/gbleads/settings", headers=_auth(t))
        data = r.json()
        assert data["provider"]["name"] == "google_places"
        assert data["provider"]["free"] is False
        assert data["google_places"]["configured"] is True
        assert "SUPER-SECRET" not in r.text
    finally:
        cfg.settings.GOOGLE_MAPS_API_KEY = ""


# ---------------------------------------------------------------------------
# end-to-end campaign on the OSM provider (fully mocked HTTP)
# ---------------------------------------------------------------------------

def test_search_flow_osm_end_to_end(monkeypatch):
    t = _user("osm-flow@test.example")
    elements = [
        _node(),                                                    # ok
        {"type": "way", "id": 202,                                  # ok
         "center": {"lat": -33.88, "lon": 151.2},
         "tags": {"name": "Copper Bottle Warehouse",
                  "shop": "wholesale",
                  "website": "https://warehouse.example"}},
        {"type": "node", "id": 203,                                 # no name
         "lat": -33.9, "lon": 151.3, "tags": {"shop": "hardware"}},
        {"type": "way", "id": 204,                                  # closed
         "center": {"lat": -33.91, "lon": 151.31},
         "tags": {"name": "Old Copper Depot", "disused": "yes"}},
    ]
    state, transport = _route(elements=elements)
    sleeps: list = []
    monkeypatch.setattr(
        gbleads_jobs, "get_places_provider",
        lambda: OverpassProvider(transport=transport,
                                 sleep=sleeps.append))

    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Sydney OSM Copper",
        "keywords": ["copper bottles"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 20, "enrich": False, "start": True,
    }, headers=_auth(t))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert _wait_search(cid) == "COMPLETED"

    c = client.get(f"/api/gbleads/campaigns/{cid}", headers=_auth(t)).json()
    assert c["total_discovered"] == 2          # 2 real businesses
    assert c["total_skipped"] == 1             # closed one; nameless elements
    #                                         are dropped by the provider parser
    assert c["duplicate_count"] == 0
    assert c["api_calls"] == 2                 # 1 Nominatim + 1 Overpass
    assert c["estimated_cost_usd"] == 0.0      # free provider
    assert c["error"] is None

    db = SessionLocal()
    try:
        leads = (db.query(BusinessLead)
                 .filter(BusinessLead.campaign_id == cid)
                 .order_by(BusinessLead.id).all())
        assert len(leads) == 2
        assert all(l.source_provider == "openstreetmap" for l in leads)
        assert all(l.place_id.startswith("osm_") for l in leads)
        # no ratings in OSM — None, not 0
        assert all(l.rating is None and l.review_count is None for l in leads)
        with_email = next(l for l in leads if l.email)
        assert with_email.email == "sales@coppertraders.example"
        assert with_email.website == "https://coppertraders.example"
        # published OSM email gets an evidence row with its exact source
        ev = (db.query(LeadEvidence)
              .filter(LeadEvidence.lead_id == with_email.id,
                      LeadEvidence.field_name == "email").first())
        assert ev is not None
        assert ev.source_url == ("https://www.openstreetmap.org/"
                                 "node/101")
        assert ev.extraction_method == "osm_tag"
        assert ev.confidence and ev.confidence < 1.0
        # match reasons name the actual provider
        reasons = with_email.match_reasons
        assert "OpenStreetMap search" in reasons
        assert "copper bottles in Sydney, Australia" in reasons
    finally:
        db.close()


def test_search_flow_finds_category_tagged_stores_not_in_name(monkeypatch):
    """The Ilford gap: stores whose NAME never contains the keyword but
    whose OSM category tag does (shop=houseware for 'kitchenware') must be
    discovered, scored, and carry the exact-tag reason."""
    t = _user("osm-category@test.example")
    elements = [
        {"type": "node", "id": 301, "lat": -33.9, "lon": 151.2,
         "tags": {"name": "Super Housewares", "shop": "houseware",
                  "website": "https://superhousewares.example"}},
        {"type": "node", "id": 302, "lat": -33.91, "lon": 151.3,
         "tags": {"name": "DelightKB", "shop": "kitchen",
                  "email": "info@delightkb.com", "phone": "+44 20 8988 9916"}},
    ]
    state, transport = _route(elements=elements)
    monkeypatch.setattr(
        gbleads_jobs, "get_places_provider",
        lambda: OverpassProvider(transport=transport,
                                 sleep=lambda s: None))

    r = client.post("/api/gbleads/campaigns", json={
        "campaign_name": "Ilford kitchenware categories",
        "keywords": ["kitchenware"],
        "countries": ["Australia"], "cities": ["Sydney"],
        "max_results": 10, "enrich": False, "start": True,
    }, headers=_auth(t))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert _wait_search(cid) == "COMPLETED"

    c = client.get(f"/api/gbleads/campaigns/{cid}", headers=_auth(t)).json()
    assert c["total_discovered"] == 2        # neither name says 'kitchenware'
    assert c["error"] is None

    db = SessionLocal()
    try:
        leads = {l.company_name: l for l in db.query(BusinessLead).filter(
            BusinessLead.campaign_id == cid).all()}
        assert set(leads) == {"Super Housewares", "DelightKB"}
        sh = leads["Super Housewares"]
        # category-tag match: +20 with the exact OSM tag in the reason
        assert sh.relevance_score == 20
        assert ("Keyword 'kitchenware' maps to the OpenStreetMap "
                "category 'houseware'") in sh.match_reasons
        # honest fields: OSM has no ratings; email only when published
        assert sh.rating is None and sh.review_count is None
        assert sh.email is None
        dk = leads["DelightKB"]
        assert dk.email == "info@delightkb.com"
        assert ("category 'kitchen'") in dk.match_reasons
        ev = (db.query(LeadEvidence)
              .filter(LeadEvidence.lead_id == dk.id,
                      LeadEvidence.field_name == "email").first())
        assert ev is not None and ev.extraction_method == "osm_tag"
    finally:
        db.close()
