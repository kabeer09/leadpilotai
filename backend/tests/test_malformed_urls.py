"""Malformed third-party URLs must never kill a crawl job (job 32).

Python's urlparse/urljoin raise ValueError ("Invalid IPv6 URL") on bracket
garbage — raw HTML hrefs, redirect Location headers, adapter JSON fields.
The worker path logged these as warnings; the ingest path re-raises
non-lock errors by design ("real bug — always loud"), so ONE junk anchor
on ONE exhibitor page FAILED THE WHOLE JOB. Every boundary that touches
third-party URL strings now degrades honestly instead of raising.
"""
import httpx

from app.crawler import (
    _enrich_from_official_site,
    _fetch,
    _save_company,
    extract_page,
)
from app.database import SessionLocal
from app.models import User, Source, Company, Contact, Lead
from app.validation import domain_of

BAD_HREF = "http://[bad"          # raises ValueError on urlparse+urljoin
BAD_WEB = "http://foo[bar.com"    # raises when a stored site is re-parsed


# ----------------------------------------------------------------- crawler

def test_extract_page_survives_malformed_href():
    """Job 32's exact death: <a href="http://[bad"> inside an official
    site's HTML. The junk link is dropped, the page's real facts and the
    good links still extract."""
    html = ("<html><head><title>Acme</title></head><body>"
            f'<a href="{BAD_HREF}">junk</a>'
            '<a href="https://good.example/about">About us</a>'
            "Mail sales@acme.example.</body></html>")
    data = extract_page("https://acme.example/", html)
    assert "sales@acme.example" in data["emails"]
    hrefs = [u for u, _ in data["links"]]
    assert BAD_HREF not in hrefs
    assert "https://good.example/about" in hrefs


def test_extract_page_survives_malformed_web_field():
    """The Hometex-style 'Web' text field can carry garbage; a bracketed
    value must not fail the whole page."""
    html = ("<html><head><title>T</title></head><body>"
            "<div>Web</div><div>foo[bar.com</div>"
            "Contact us at info@t.example.</body></html>")
    data = extract_page("https://t.example/", html)
    assert "info@t.example" in data["emails"]


def test_fetch_treats_bad_redirect_location_as_error():
    """A 302 whose Location header is bracket garbage is a fetch error,
    never a ValueError out of _fetch."""
    def handler(request):
        return httpx.Response(302, headers={"location": BAD_HREF},
                              request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    html, err = _fetch(client, "https://example.com/x")
    assert html is None
    assert err == "bad redirect location"


def test_enrich_official_site_malformed_url_skips():
    """Stage-3 entry with a garbage official-site URL (adapter JSON) is an
    honest (0, 0) skip — no fetch, no crash, job keeps going."""
    out = _enrich_from_official_site(None, 1, None, BAD_HREF,
                                     None, render_budget=[0], job_id=None)
    assert out == (0, 0)


def test_save_company_survives_malformed_profile_url():
    db = SessionLocal()
    created = None
    try:
        u = User(email="badurl@test.example",
                 password_hash="x")  # hash never checked here
        db.add(u)
        db.flush()
        src = Source(user_id=u.id, url="https://badurl.example",
                     name="s", status="COMPLETED")
        db.add(src)
        db.flush()
        data = {"site_name": "", "og_title": "", "h1": "",
                "title": "Bracket Co", "profile_name": "Bracket Co",
                "description": "", "profile_description": "Brass items",
                "industry": "", "country": None, "city": None,
                "address": None, "hall": None, "booth": None,
                "products": [], "products_json": None,
                "emails": ["sales@bracket.example"], "phones": [],
                "whatsapp": [], "web_text": "", "social": {},
                "official_links": [], "links": [], "text_len": 5000}
        c = _save_company(db, u.id, src.id, BAD_WEB, data, 1, lock=None)
        assert c is not None
        assert c.name == "Bracket Co"
        created = c.id
        db.commit()
    finally:
        if created is not None and db.in_transaction():
            db.rollback()
        if created is not None:
            d2 = SessionLocal()
            try:
                d2.query(Lead).filter(Lead.company_id == created).delete()
                d2.query(Contact).filter(Contact.company_id == created).delete()
                d2.query(Company).filter(Company.id == created).delete()
                d2.commit()
            finally:
                d2.close()
        db.close()


# ------------------------------------------------------------ validation

def test_domain_of_never_raises_on_bracket_urls():
    """domain_of runs on crawled emails and stored websites deep inside
    ingest — a ValueError here would fail the job after the record was
    already accepted."""
    for u in (BAD_WEB, "https://[bad", "http://[::1"):
        out = domain_of(u)
        assert isinstance(out, str)


def test_directory_host_any_never_raises():
    """A malformed stored website must not 500 the public search."""
    from app.api_directory import _host_any
    for u in (BAD_WEB, "https://[bad"):
        assert isinstance(_host_any(u), str)


# ------------------------------------------------------------ gb enricher

def test_gb_extract_page_survives_malformed_href():
    """Same junk-href guard on the GB-leads extraction path."""
    from app.gb_enricher import extract_page as gb_extract
    html = ('<html><body>'
            f'<a href="{BAD_HREF}">junk</a>'
            '<a href="/contact">Contact us</a>'
            'Reach sales@gb.example</body></html>')
    out = gb_extract(html, "https://gb.example/")
    assert BAD_HREF not in out["links"]
    assert "https://gb.example/contact" in out["links"]
