# -*- coding: utf-8 -*-
"""High Point Market record adapter: profile parsing, owner-social filtering,
records routing, and the email-driven enrichment trigger.

All offline: canned profile HTML mirrors the live server-rendered page
(including the JSON-LD trailing brace, raw control char, labeled corporate
phone, showroom block and the organizer's footer links)."""
from app.fair_adapters import (
    parse_highpoint_profile, _hp_drop_owner_socials, discover_records)


PROFILE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Acme Seating - High Point Market</title>
<script type="application/ld+json">
{
        "@context": "https://schema.org",
        "@type": "BusinessEvent",
        "name": "High Point Market Oct 17-21, 2026",
        "description": "The market event."
}
</script>
<script type="application/ld+json">
{
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Acme Seating",
        "description": "Hand-built sofas\x0b since 1974.",
        "url": "https://www.highpointmarket.org/exhibitor/999"
    }
}
</script>
</head>
<body>
<h1>Acme Seating</h1>
<p>info@HighPointMarket.org Back to Search IHFC - C412, Commerce, Floor 4
Shuttle Stop: 3 (Red Line) Neighborhood: Commerce
Corporate Phone: (310) 827-2129 ext 148 www.acmeseating.com</p>
<div>High Point Market Authority 164 South Main Street, Suite 700
High Point, NC 27260 336.869.1000</div>
<a href="http://www.acmeseating.com">www.acmeseating.com</a>
<a href="https://www.facebook.com/acmeseating">Facebook</a>
<a href="https://www.instagram.com/acmeseating/">Instagram</a>
<a href="https://www.linkedin.com/company/acme-seating/">LinkedIn</a>
<a href="https://www.facebook.com/pages/High-Point-Market-The-Worlds-Home/10293267477">Market FB</a>
<a href="https://twitter.com/HPMarketNews">Market Twitter</a>
<a href="https://www.youtube.com/channel/UCDq7mJLXssH7NAQyLbd1Cow">Market YouTube</a>
<a href="https://www.linkedin.com/company/high-point-market-authority/">Market LinkedIn</a>
<a href="mailto:info@HighPointMarket.org">Email</a>
</body>
</html>
"""

URL = "https://www.highpointmarket.org/exhibitor/999"


def test_parse_highpoint_profile_exhibitor_fields():
    rec = parse_highpoint_profile(URL, PROFILE_HTML)
    assert rec is not None
    # Organization JSON-LD (trailing brace + control char tolerated)
    assert rec["profile_name"] == "Acme Seating"
    assert rec["description"] == "Hand-built sofas since 1974."
    # Labeled phone only: no footer 336.869.1000, no junk digit runs, no ext
    assert rec["phones"] == ["(310) 827-2129"]
    # Showroom block between "Back to Search" and "Shuttle Stop:"
    assert rec["booth"] == "IHFC - C412, Commerce, Floor 4"
    # Exhibitor's own site picked; the fair's domain and socials never count
    assert rec["official_website"] == "http://www.acmeseating.com"
    # The organizer's email is not captured (profiles publish no exhibitor
    # email at all — Stage-3 enrichment fetches it from the own site)
    assert rec["emails"] == []
    assert rec["profile_url"] == URL
    # Organizer socials dropped by owner tokens; exhibitor's own kept
    assert set(rec["social"]) == {"facebook", "instagram", "linkedin"}
    assert "acmeseating" in rec["social"]["facebook"]
    assert "acmeseating" in rec["social"]["instagram"]
    assert "acme-seating" in rec["social"]["linkedin"]


def test_parse_highpoint_profile_empty_page_returns_none():
    assert parse_highpoint_profile(URL, "<html></html>") is None


def test_hp_drop_owner_socials_by_frequency():
    # An organizer account with no owner token in its URL: only the cross-
    # page frequency rule can identify it as not-the-exhibitor's.
    market = "https://www.youtube.com/channel/UCEXAMPLEMARKETID"
    parsed = [
        {"social": {"youtube": market,
                    "facebook": "https://www.facebook.com/brand-a"}},
        {"social": {"youtube": market,
                    "instagram": "https://www.instagram.com/brandb"}},
        {"social": {"youtube": market,
                    "pinterest": "https://www.pinterest.com/brandc/"}},
    ]
    _hp_drop_owner_socials(parsed)
    assert all("youtube" not in r["social"] for r in parsed)
    assert parsed[0]["social"]["facebook"].endswith("/brand-a")
    assert parsed[1]["social"]["instagram"].endswith("/brandb")
    assert parsed[2]["social"]["pinterest"].endswith("/brandc/")


def test_discover_records_routes_highpoint(monkeypatch):
    import app.fair_adapters as fa
    calls = {}

    def fake(seed, max_pages=None):
        calls["seed"], calls["max_pages"] = seed, max_pages
        return [{"title": "X"}], None

    monkeypatch.setattr(fa, "_highpoint_records", fake)
    recs, err = fa.discover_records("https://www.highpointmarket.org/", 25)
    assert err is None and recs == [{"title": "X"}]
    assert calls == {"seed": "https://www.highpointmarket.org/",
                     "max_pages": 25}
    # Unrelated hosts are untouched by the routing
    assert fa.discover_records("https://example.com/") == (None, None)


def test_ingest_enriches_rows_that_have_a_phone(monkeypatch):
    """A phone must not block Stage 3: rows missing only the email enrich
    from their own site (the High Point profile publishes a phone but no
    email), and the record's own site becomes company.website."""
    import threading
    from queue import Queue

    import app.crawler as craw
    from app.database import SessionLocal
    from app.models import User, Source, Company, Contact, Lead, CrawlJob
    from app.security import hash_password

    db = SessionLocal()
    u = db.query(User).filter(User.email == "hp-ingest@test.example").first()
    if not u:
        u = User(email="hp-ingest@test.example",
                 password_hash=hash_password("TestPass123"))
        db.add(u)
        db.commit()
        db.refresh(u)
    src = db.query(Source).filter(Source.user_id == u.id).first()
    if not src:
        src = Source(user_id=u.id, url="https://hp.example", name="hp",
                     status="COMPLETED")
        db.add(src)
        db.commit()
        db.refresh(src)
    uid, sid = u.id, src.id
    job = CrawlJob(user_id=uid, source_id=sid,
                   url="https://www.highpointmarket.org", max_pages=10,
                   max_depth=1, status="CRAWLING")
    db.add(job)
    db.commit()
    jid = job.id
    db.close()

    recs = [{
        "title": "Phone Only Co", "profile_name": "Phone Only Co",
        "description": "Motion furniture.",
        "emails": [], "phones": ["8286327731"],
        "official_website": "https://phoneonly.example",
        "profile_url": "https://www.highpointmarket.org/exhibitor/424242",
        "official_links": [], "links": [], "text_len": 5000,
    }]

    calls = []

    def fake_enrich(db_, uid_, comp, site, client, render_budget, job_id=None):
        calls.append(site)
        db_.add(Contact(user_id=uid_, company_id=comp.id, kind="EMAIL",
                        value="info@phoneonly.example",
                        normalized="info@phoneonly.example",
                        source_url=site, source_page="contact"))
        return 1, 1

    monkeypatch.setattr(craw, "_enrich_from_official_site", fake_enrich)

    db = SessionLocal()
    cid = None
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        shared = {"lock": threading.Lock(), "queue": Queue(),
                  "seen": set(), "pages": 0, "companies": 0, "contacts": 0,
                  "render_budget": [10], "errors": [],
                  "cancel": threading.Event(), "directory_mode": True}
        n = craw._ingest_adapter_records(db, job, recs, shared)
        assert n == 1
        c = db.query(Company).filter(Company.user_id == uid,
                                     Company.name == "Phone Only Co").first()
        assert c is not None
        assert c.website == "https://phoneonly.example"  # web_text passthrough
        assert calls == ["https://phoneonly.example"]    # phone did NOT block
        vals = {r.value for r in db.query(Contact)
                .filter(Contact.company_id == c.id)}
        assert "info@phoneonly.example" in vals          # email via Stage 3
        assert "8286327731" in vals                      # record phone kept
        assert shared["pages"] >= 2                      # enrichment billed
        cid = c.id
    finally:
        db.close()
        d2 = SessionLocal()
        try:
            for l in d2.query(Lead).filter(Lead.company_id == cid).all():
                d2.delete(l)
            d2.query(Contact).filter(Contact.company_id == cid).delete()
            d2.query(Company).filter(Company.id == cid).delete()
            d2.query(CrawlJob).filter(CrawlJob.id == jid).delete()
            d2.commit()
        finally:
            d2.close()


def _mk_job(email: str):
    """Shared boilerplate: user + source + PENDING-shape crawl job rows."""
    from app.database import SessionLocal
    from app.models import User, Source, CrawlJob
    from app.security import hash_password
    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, password_hash=hash_password("TestPass123"))
        db.add(u)
        db.commit()
        db.refresh(u)
    src = Source(user_id=u.id, url="https://dir.example", name="d",
                 status="COMPLETED")
    db.add(src)
    db.commit()
    db.refresh(src)
    job = CrawlJob(user_id=u.id, source_id=src.id,
                   url="https://dir.example/", max_pages=10, max_depth=1,
                   status="CRAWLING")
    db.add(job)
    db.commit()
    uid, sid, jid = u.id, src.id, job.id
    db.close()
    return uid, sid, jid


def _rm_job(uid, jid):
    from app.database import SessionLocal
    from app.models import User, Source, Company, Contact, Lead, CrawlJob
    db = SessionLocal()
    try:
        for c in db.query(Company).filter(Company.user_id == uid).all():
            for l in db.query(Lead).filter(Lead.company_id == c.id).all():
                db.delete(l)
            db.query(Contact).filter(Contact.company_id == c.id).delete()
            db.delete(c)
        db.query(CrawlJob).filter(CrawlJob.id == jid).delete()
        db.query(Source).filter(Source.user_id == uid).delete()
        db.query(User).filter(User.id == uid).delete()
        db.commit()
    finally:
        db.close()


def test_ingest_clears_directory_host_website(monkeypatch):
    """A directory profile whose exhibitor lists no own site must end with
    website=None — the fair's domain is not the company's website (the save
    path's bare-host fallback must not leak through)."""
    import threading
    from queue import Queue

    import app.crawler as craw
    from app.database import SessionLocal
    from app.models import Company, Contact

    uid, sid, jid = _mk_job("hp-nohost@test.example")
    profile = "https://dir.example/exhibitor/1"
    db = SessionLocal()
    comp = Company(user_id=uid, source_id=sid, name="No Site Co",
                   profile_url=profile, website="dir.example")
    db.add(comp)
    db.commit()
    cid = comp.id
    db.close()

    recs = [{"title": "No Site Co", "profile_name": "No Site Co",
             "description": "Textiles.", "emails": [], "phones": ["1234567890"],
             "official_website": None, "profile_url": profile,
             "links": [], "text_len": 5000}]

    def no_enrich(*a, **k):
        raise AssertionError("site is None — nothing to enrich")

    monkeypatch.setattr(craw, "_enrich_from_official_site", no_enrich)
    db = SessionLocal()
    try:
        from app.models import CrawlJob
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        shared = {"lock": threading.Lock(), "queue": Queue(),
                  "seen": set(), "pages": 0, "companies": 0, "contacts": 0,
                  "render_budget": [10], "errors": [],
                  "cancel": threading.Event(), "directory_mode": True}
        n = craw._ingest_adapter_records(db, job, recs, shared)
        assert n == 1
        c = db.query(Company).filter(Company.id == cid).first()
        assert c.website is None          # directory host cleared
        assert db.query(Contact).filter(Contact.company_id == cid).count() >= 1
    finally:
        db.close()
        _rm_job(uid, jid)


def test_ingest_skips_enrichment_when_row_already_has_email(monkeypatch):
    """Re-crawls must not re-bill enrichment for rows that already carry an
    email — the budget goes only to rows still missing one."""
    import threading
    from queue import Queue

    import app.crawler as craw
    from app.database import SessionLocal
    from app.models import Company, Contact, CrawlJob

    uid, sid, jid = _mk_job("hp-hasmail@test.example")
    profile = "https://dir.example/exhibitor/2"
    db = SessionLocal()
    comp = Company(user_id=uid, source_id=sid, name="Has Mail Co",
                   profile_url=profile)
    db.add(comp)
    db.flush()
    db.add(Contact(user_id=uid, company_id=comp.id, kind="EMAIL",
                   value="a@hasmail.example", normalized="a@hasmail.example",
                   source_url=profile, source_page="contact", is_primary=True))
    db.commit()
    cid = comp.id
    db.close()

    recs = [{"title": "Has Mail Co", "profile_name": "Has Mail Co",
             "description": "Bedding.", "emails": [], "phones": ["1234567890"],
             "official_website": "https://hasmail.example",
             "profile_url": profile, "links": [], "text_len": 5000}]

    calls = []

    def fake_enrich(*a, **k):
        calls.append(1)
        return 0, 0

    monkeypatch.setattr(craw, "_enrich_from_official_site", fake_enrich)
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        shared = {"lock": threading.Lock(), "queue": Queue(),
                  "seen": set(), "pages": 0, "companies": 0, "contacts": 0,
                  "render_budget": [10], "errors": [],
                  "cancel": threading.Event(), "directory_mode": True}
        n = craw._ingest_adapter_records(db, job, recs, shared)
        assert n == 1
        assert calls == []                # email exists — enrichment skipped
        c = db.query(Company).filter(Company.id == cid).first()
        assert c.website == "https://hasmail.example"
    finally:
        db.close()
        _rm_job(uid, jid)
