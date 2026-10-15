"""Crawler save path: company+contacts+lead creation, exclusion, idempotency."""
from app.database import SessionLocal
from app.models import User, Source, Company, Contact, Lead, CrawlEvent
from app.crawler import _save_company, extract_page


def _owner():
    db = SessionLocal()
    u = db.query(User).filter(User.email == "save@test.example").first()
    if not u:
        from app.security import hash_password
        u = User(email="save@test.example", password_hash=hash_password("TestPass123"))
        db.add(u)
        db.commit()
        db.refresh(u)
    uid = u.id
    src = db.query(Source).filter(Source.user_id == uid).first()
    if not src:
        src = Source(user_id=uid, url="https://save.example", name="s", status="COMPLETED")
        db.add(src)
        db.commit()
        db.refresh(src)
    out = (uid, src.id)
    db.close()
    return out


def test_save_company_creates_contacts_and_lead():
    uid, sid = _owner()
    html = ("<html><head><title>Save Corp | Gifts</title>"
            "<meta name='description' content='Brass decor wholesale'></head>"
            "<body>Contact sales@savecorp.example or info@savecorp.example. "
            "Call +91-98765-43210. Our factory makes brass urlis.</body></html>")
    data = extract_page("https://savecorp.example/contact", html)
    db = SessionLocal()
    try:
        c = _save_company(db, uid, sid, "https://savecorp.example/contact", data, 0)
        assert c is not None and c.lifecycle == "active"
        contacts = db.query(Contact).filter(Contact.company_id == c.id).all()
        assert {x.value for x in contacts if x.kind == "EMAIL"} == \
            {"sales@savecorp.example", "info@savecorp.example"}
        assert all(x.source_url == "https://savecorp.example/contact" for x in contacts)
        lead = db.query(Lead).filter(Lead.company_id == c.id).first()
        assert lead is not None and lead.score > 0
        # Re-save is idempotent (no duplicate contacts/companies)
        _save_company(db, uid, sid, "https://savecorp.example/contact", data, 0)
        assert db.query(Company).filter(Company.user_id == uid,
                                        Company.name == c.name).count() == 1
        assert db.query(Contact).filter(Contact.company_id == c.id).count() == len(contacts)
    finally:
        db.close()
        d2 = SessionLocal()
        try:
            for l in d2.query(Lead).filter(Lead.company_id == c.id).all():
                d2.delete(l)
            d2.query(Contact).filter(Contact.company_id == c.id).delete()
            d2.query(Company).filter(Company.id == c.id).delete()
            d2.commit()
        finally:
            d2.close()


def test_js_shell_and_listing_detection():
    from app.crawler import _looks_like_js_shell, _is_listing_page
    shell_html = ('<html><head></head><body><div id="root"></div>'
                  '<script src="/static/js/main.js"></script></body></html>')
    d = {"emails": [], "phones": [], "text_len": 20}
    assert _looks_like_js_shell(shell_html, d) is True
    assert _looks_like_js_shell(shell_html, {"emails": ["a@b.com"],
                                             "phones": [], "text_len": 20}) is False
    assert _looks_like_js_shell("<html><body>" + ("word " * 2000) + "</body></html>",
                               {"emails": [], "phones": [], "text_len": 10000}) is False
    links = [(f"https://dir.example/exhibitor/{i}", f"Exhibitor {i}") for i in range(12)]
    assert _is_listing_page({"links": links}, "dir.example") is True
    assert _is_listing_page({"links": links[:3]}, "dir.example") is False


def test_profile_identity_keeps_directory_companies_separate():
    from app.crawler import _save_company
    uid, sid = _owner()
    db = SessionLocal()
    try:
        base = {"site_name": "", "og_title": "", "h1": "", "title": "Exhibitor",
                "description": "d", "emails": [], "phones": [], "whatsapp": [],
                "links": [], "official_links": [], "text_len": 600}
        a = _save_company(db, uid, sid, "https://dir.example/exhibitor/111", dict(base), 1)
        b = _save_company(db, uid, sid, "https://dir.example/exhibitor/222", dict(base), 1)
        assert a is not None and b is not None and a.id != b.id
        # same profile URL re-saved -> same row
        a2 = _save_company(db, uid, sid, "https://dir.example/exhibitor/111", dict(base), 1)
        assert a2.id == a.id
        aid, bid = a.id, b.id
    finally:
        db.close()
        d2 = SessionLocal()
        try:
            for l in d2.query(Lead).filter(
                    Lead.company_id.in_([aid, bid])).all():
                d2.delete(l)
            d2.query(Company).filter(Company.id.in_([aid, bid])).delete()
            d2.commit()
        finally:
            d2.close()


def test_template_names_and_short_digit_runs_rejected():
    from app.crawler import _save_company, _norm_phone
    assert _norm_phone("20260916") is None  # date/booth artifact
    assert _norm_phone("+1-555-0100") == "+15550100"
    uid, sid = _owner()
    db = SessionLocal()
    try:
        base = {"site_name": "", "og_title": "", "h1": "",
                "title": "{lineName} shown by {exhibitorName}",
                "description": "d", "emails": [], "phones": [], "whatsapp": [],
                "links": [], "official_links": [], "text_len": 600}
        assert _save_company(db, uid, sid,
                             "https://dir.example/exhibitor/999", dict(base), 1) is None
    finally:
        db.close()


def test_complete_check_and_drop_rule():
    from app.crawler import company_is_complete, _should_drop, _find_company
    uid, sid = _owner()
    db = SessionLocal()
    try:
        c = Company(user_id=uid, source_id=sid, name="Complete Co",
                    website="complete.example",
                    product_summary="Brass decor wholesale")
        db.add(c)
        db.flush()
        assert company_is_complete(db, c) is False  # no email yet
        db.add(Contact(user_id=uid, company_id=c.id, kind="EMAIL",
                       value="info@complete.example",
                       normalized="info@complete.example",
                       source_url="https://complete.example"))
        db.commit()
        assert company_is_complete(db, c) is True
        assert _find_company(db, uid, "https://dir.example/exhibitor/1",
                             "dir.example") is None
        # new profile, no contacts, no site -> drop
        assert _should_drop(db, uid, "https://dir.example/exhibitor/1",
                             "dir.example",
                             {"emails": [], "phones": [],
                              "official_links": []}) is True
        # same but with an official site -> keep (enrichable)
        assert _should_drop(db, uid, "https://dir.example/exhibitor/1",
                             "dir.example",
                             {"emails": [], "phones": [],
                              "official_links": [("x.example", "https://x.example", "Visit")]}) is False
        # with contacts -> keep
        assert _should_drop(db, uid, "https://dir.example/exhibitor/1",
                             "dir.example",
                             {"emails": ["a@b.example"], "phones": [],
                              "official_links": []}) is False
        # homepage crawl without contacts -> keep as EXCLUDED (has its site)
        assert _should_drop(db, uid, "https://newsite.example/",
                             "newsite.example",
                             {"emails": [], "phones": [],
                              "official_links": []}) is False
    finally:
        cid = c.id
        db.close()
        d2 = SessionLocal()
        try:
            d2.query(Contact).filter(Contact.company_id == cid).delete()
            d2.query(Company).filter(Company.id == cid).delete()
            d2.commit()
        finally:
            d2.close()


def test_queued_urls_are_processed_not_skipped(monkeypatch):
    """Regression: worker must process every queue take (enqueue dedupes)."""
    import app.crawler as craw
    from app.models import Source, CrawlJob
    uid, sid = _owner()
    html = ("<html><head><title>Pool Co</title></head><body>"
            "Mail hello@pool.example. "
            '<a href="/about">About</a> '
            '<a href="/contact">Contact</a></body></html>')

    def fake_fetch(client, url):
        return html, None

    monkeypatch.setattr(craw, "_fetch", fake_fetch)
    monkeypatch.setattr(craw, "_robots_allowed", lambda base, url: True)
    monkeypatch.setattr(craw, "_render_with_browser", lambda url: (None, "off"))
    monkeypatch.setattr(craw.settings, "CRAWL_WORKERS", 2)
    db = SessionLocal()
    try:
        job = CrawlJob(user_id=uid, source_id=sid, url="https://pool.example/",
                       max_pages=3, max_depth=1, status="PENDING")
        db.add(job)
        db.commit()
        jid = job.id
    finally:
        db.close()
    craw.run_job(jid)
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        assert job.status == "COMPLETED"
        assert job.pages_visited >= 2, "queued links must be fetched, not skipped"
        assert job.companies_found >= 1
        for l in db.query(Lead).filter(
                Lead.company_id.in_(
                    db.query(Company.id).filter(Company.user_id == uid,
                                               Company.name == "Pool Co"))).all():
            db.delete(l)
        for c in db.query(Company).filter(
                Company.user_id == uid, Company.name == "Pool Co").all():
            db.query(Contact).filter(Contact.company_id == c.id).delete()
            db.delete(c)
        db.query(CrawlEvent).filter(CrawlEvent.job_id == jid).delete()
        db.query(CrawlJob).filter(CrawlJob.id == jid).delete()
        db.commit()
    finally:
        db.close()


def test_directory_mode_saves_only_viable_profiles():
    from app.crawler import _save_allowed, _pick_official_site
    shared = {"directory_mode": True}
    prof = "https://fair.example/exhibitor/12345"
    section = "https://fair.example/en/sustainability-info"
    contactless = {"emails": [], "phones": [], "official_links": []}
    with_site = {"emails": [], "phones": [],
                 "official_links": [("x.example", "https://x.example", "Visit")]}
    with_mail = {"emails": ["a@x.example"], "phones": [], "official_links": []}
    sister = {"emails": [], "phones": [],
              "official_links": [("www.fair.example", "https://www.fair.example",
                                  "more information")]}
    ok, _ = _save_allowed(contactless, prof, False, shared)
    assert ok is False  # dead profile refused
    ok, _ = _save_allowed(with_site, prof, False, shared)
    assert ok is True  # enrichable
    ok, _ = _save_allowed(with_mail, prof, False, shared)
    assert ok is True  # contacts on profile
    ok, _ = _save_allowed(contactless, section, False, shared)
    assert ok is False  # organizer section page
    ok, _ = _save_allowed(sister, prof, False, shared)
    assert ok is False  # sister-domain links are not an official site
    ok, _ = _save_allowed(contactless, prof, False, {})
    assert ok is True  # generic (non-directory) crawls unaffected
    # anchor-named independent domain wins over first-link junk
    picked = _pick_official_site(
        [("reg.example", "https://reg.example", "Register"),
         ("aspen.example", "https://www.aspen.example", "Visit aspen.example")],
        "fair.example")
    assert picked == ("aspen.example", "https://www.aspen.example")


def test_concurrent_same_url_saves_once():
    """Two workers, same profile URL, same instant -> exactly one row."""
    import threading
    from app.crawler import _save_company
    uid, sid = _owner()
    base = {"site_name": "", "og_title": "", "h1": "Race Co",
            "title": "Race Co", "description": "d", "emails": ["r@race.example"],
            "phones": [], "whatsapp": [], "links": [], "official_links": [],
            "text_len": 600}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def work():
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            _save_company(db, uid, sid, "https://dir.example/exhibitor/777",
                          dict(base), 1, lock=lock)
        finally:
            db.close()

    ts = [threading.Thread(target=work) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    db = SessionLocal()
    try:
        rows = db.query(Company).filter(
            Company.user_id == uid,
            Company.profile_url == "https://dir.example/exhibitor/777").all()
        assert len(rows) == 1
        cid = rows[0].id
    finally:
        db.close()
    d2 = SessionLocal()
    try:
        for l in d2.query(Lead).filter(Lead.company_id == cid).all():
            d2.delete(l)
        d2.query(Contact).filter(Contact.company_id == cid).delete()
        d2.query(Company).filter(Company.id == cid).delete()
        d2.commit()
    finally:
        d2.close()


def test_save_company_without_contacts_excluded():
    uid, sid = _owner()
    html = "<html><head><title>Ghost Ltd</title></head><body>" + ("word " * 200) + "</body></html>"
    data = extract_page("https://ghost.example/", html)
    assert data["emails"] == [] and data["phones"] == []
    db = SessionLocal()
    try:
        c = _save_company(db, uid, sid, "https://ghost.example/", data, 0)
        assert c.lifecycle == "EXCLUDED_NO_CONTACT"
    finally:
        db.close()
        d2 = SessionLocal()
        try:
            for l in d2.query(Lead).filter(Lead.company_id == c.id).all():
                d2.delete(l)
            d2.query(Company).filter(Company.id == c.id).delete()
            d2.commit()
        finally:
            d2.close()
