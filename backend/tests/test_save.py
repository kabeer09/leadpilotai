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
    # single unambiguous candidate is accepted
    assert _pick_official_site(
        [("only.example", "https://only.example", "")],
        "fair.example") == ("only.example", "https://only.example")
    # multiple unsignalled candidates are refused
    assert _pick_official_site(
        [("a.example", "https://a.example", ""),
         ("b.example", "https://b.example", "")],
        "fair.example") is None
    # high point adapter: whole directory from one autocomplete call
    from app.fair_adapters import _highpoint_profiles
    import app.fair_adapters as fa

    class _FakeResp:
        status_code = 200

        def json(self):
            return [{"value": "/exhibitor/16088", "label": "16 West"},
                    {"value": "/about", "label": "About"},
                    {"value": "/exhibitor/16088", "label": "16 West"}]

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            return _FakeResp()

    real_client = fa.httpx.Client
    fa.httpx.Client = _FakeClient
    try:
        urls, err = _highpoint_profiles()
    finally:
        fa.httpx.Client = real_client
    assert err is None
    assert urls == ["https://www.highpointmarket.org/exhibitor/16088"]


def test_owner_emails_stripped_on_profiles():
    from app.crawler import strip_owner_emails
    shared = {"directory_mode": True}
    data = {"emails": ["info@highpointmarket.org", "sales@exhibitor.example"]}
    n = strip_owner_emails(
        data, "https://www.highpointmarket.org/exhibitor/16088", shared)
    assert n == 1 and data["emails"] == ["sales@exhibitor.example"]
    # homepages keep everything
    home = {"emails": ["info@mysite.example"]}
    assert strip_owner_emails(
        home, "https://mysite.example/", shared) == 0
    assert home["emails"] == ["info@mysite.example"]
    # generic crawls unaffected
    assert strip_owner_emails(
        {"emails": ["a@fair.example"]},
        "https://fair.example/exhibitor/1", {}) == 0


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


def test_profile_fields_fill_from_selectors():
    """Old-repo parity: hall/booth/address/products/industry land on the row."""
    from app.crawler import extract_page
    html = (
        "<html><head><title>Fair Exhibitor</title></head><body>"
        "<h1 class='exhibitor-name'>Lampen AG</h1>"
        "<p class='exhibitor-description'>We manufacture designer lamps and "
        "chandeliers for wholesale buyers worldwide.</p>"
        "<div class='location'>Frankfurt, Germany</div>"
        "<span class='hall'>Hall 3</span><span class='booth'>3.0 A51</span>"
        "<div class='product-item'>Pendant lamp Luna</div>"
        "<div class='product-item'>Chandelier Stella</div>"
        "</body></html>"
    )
    d = extract_page("https://fair.example/exhibitor/42", html)
    assert d["profile_name"] == "Lampen AG"
    assert d["industry"] == "Lighting"
    assert d["hall"] == "Hall 3" and d["booth"] == "3.0 A51"
    assert d["country"] == "Germany" and d["city"] == "Frankfurt"
    assert len(d["products"]) == 2
    uid, sid = _owner()
    db = SessionLocal()
    try:
        from app.crawler import _save_company
        c = _save_company(db, uid, sid, "https://fair.example/exhibitor/42",
                          dict(d), 1)
        assert c is not None and c.category == "Lighting"
        assert c.hall == "Hall 3" and c.booth == "3.0 A51"
        assert c.country == "Germany"
        import json as _json
        assert len(_json.loads(c.products_json)) == 2
        cid = c.id
    finally:
        db.close()
        d2 = SessionLocal()
        try:
            for l in d2.query(Lead).filter(Lead.company_id == cid).all():
                d2.delete(l)
            d2.query(Company).filter(Company.id == cid).delete()
            d2.commit()
        finally:
            d2.close()


def test_commit_retry_survives_lock_storm(monkeypatch):
    """Parallel-worker commit helper: BUSY retries without rollback loss."""
    from app.crawler import db_commit_retry
    from app.database import SessionLocal
    from sqlalchemy.orm import Session
    calls = {"n": 0, "rb": 0}
    real_commit = Session.commit
    real_rollback = Session.rollback

    def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("database is locked")
        return real_commit(self)

    def counting_rollback(self):
        calls["rb"] += 1
        return real_rollback(self)

    monkeypatch.setattr(Session, "commit", flaky_commit)
    monkeypatch.setattr(Session, "rollback", counting_rollback)
    monkeypatch.setattr("time.sleep", lambda s: None)
    db = SessionLocal()
    try:
        db_commit_retry(db, tries=5)
    finally:
        db.close()
    assert calls["n"] == 3 and calls["rb"] == 0  # retried, never rolled back


def test_progress_throttled_not_per_take(monkeypatch):
    """Job-row writes at most every 15s (the hottest lock in the system)."""
    import time
    from app.crawler import job_progress
    from app.database import SessionLocal
    from app.models import CrawlJob
    import threading
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    shared = {"lock": threading.Lock(), "pages": 5, "companies": 2,
              "contacts": 9}
    db = SessionLocal()
    try:
        job = CrawlJob(user_id=1, source_id=None, url="https://x.example/",
                       max_pages=10, max_depth=0, status="CRAWLING")
        db.add(job)
        db.commit()
        jid = job.id
        job_progress(db, job, shared)
        assert job.pages_visited == 5
        now[0] += 5.0
        shared.update(pages=9, companies=4, contacts=12)
        job_progress(db, job, shared)
        assert job.pages_visited == 5  # throttled, not written
        now[0] += 11.0
        job_progress(db, job, shared)
        assert job.pages_visited == 9
        db.query(CrawlJob).filter(CrawlJob.id == jid).delete()
        db.commit()
    finally:
        db.close()


def test_process_url_survives_render_without_details(monkeypatch):
    """Shell page + det=None (non-ANDMORE) must render and save, never crash."""
    import threading
    from queue import Queue
    import app.crawler as craw
    from app.models import Source, CrawlJob
    uid, sid = _owner()
    shell_html = ("<html><head><title>Shop Co</title></head><body>"
                  "<div id='root'></div><script src='/static/app.js'></script>"
                  "</body></html>")
    real_html = ("<html><head><title>Shop Co</title></head><body>"
                 "<h1>Shop Co</h1><p>Mail hello@shop.example, call +1-555-0100. "
                 "Quality goods for every home.</p></body></html>")
    monkeypatch.setattr(craw, "_fetch", lambda client, url: (shell_html, None))
    monkeypatch.setattr(craw, "_render_with_browser",
                        lambda url: (real_html, None))
    monkeypatch.setattr(craw, "_robots_allowed", lambda base, url: True)
    monkeypatch.setattr("app.fair_adapters.fetch_exhibitor_details",
                        lambda url: None)
    monkeypatch.setattr(craw.settings, "CRAWL_WORKERS", 1)
    db = SessionLocal()
    try:
        job = CrawlJob(user_id=uid, source_id=sid, url="https://shop.example/",
                       max_pages=5, max_depth=0, status="CRAWLING")
        db.add(job)
        db.commit()
        jid = job.id
    finally:
        db.close()
    shared = {"lock": threading.Lock(), "queue": Queue(), "seen": set(),
              "pages": 0, "companies": 0, "contacts": 0,
              "render_budget": [5], "errors": [],
              "cancel": threading.Event(), "directory_mode": False}
    wdb = SessionLocal()
    wjob = wdb.query(CrawlJob).filter(CrawlJob.id == jid).first()
    import httpx
    client = httpx.Client()
    try:
        craw._process_url(wdb, client, wjob, "https://shop.example/", 0,
                          "https://shop.example", "shop.example", shared)
        assert shared["errors"] == []
        assert shared["companies"] == 1
    finally:
        client.close()
        wdb.close()
    db = SessionLocal()
    try:
        c = db.query(Company).filter(
            Company.user_id == uid, Company.name == "Shop Co").first()
        assert c is not None
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


def test_poisoned_session_recovered_per_take():
    """A flush failure on one take must not poison later takes (lock storm).

    Runs the REAL worker loop: take 1 raises mid-flush, take 2 must still
    save thanks to the worker's per-take rollback.
    """
    import threading
    import time
    from queue import Queue
    import app.crawler as craw
    from app.models import Source, CrawlJob
    uid, sid = _owner()
    db = SessionLocal()
    try:
        job = CrawlJob(user_id=uid, source_id=sid,
                       url="https://poison.example/", max_pages=5,
                       max_depth=0, status="CRAWLING")
        db.add(job)
        db.commit()
        jid = job.id
    finally:
        db.close()
    poisoned = {"done": False}
    real_process = craw._process_url

    def flaky_process(db_, client_, job_, canon, depth, base, host, shared):
        if not poisoned["done"]:
            poisoned["done"] = True
            db_.add(Company(user_id=uid, source_id=sid, name="Poison Co"))
            db_.flush()
            raise Exception("database is locked")
        return real_process(db_, client_, job_, canon, depth, base,
                            host, shared)

    html = ("<html><head><title>Heal Co</title></head><body>"
            "Mail heal@heal.example. Goods for every home, shop with us today "
            "for quality items and friendly service.</body></html>")
    orig_fetch = craw._fetch
    orig_process = craw._process_url
    craw._fetch = lambda client, url: (html, None)
    craw._process_url = flaky_process
    shared = {"lock": threading.Lock(), "queue": Queue(), "seen": set(),
              "pages": 0, "companies": 0, "contacts": 0,
              "render_budget": [5], "errors": [],
              "cancel": threading.Event(), "directory_mode": False}
    shared["queue"].put(("https://poison.example/", 0))
    shared["queue"].put(("https://heal.example/", 0))
    t = threading.Thread(
        target=craw._worker,
        args=(jid, uid, sid, 5, 0, "https://poison.example",
              "poison.example", shared),
        daemon=True)
    try:
        t.start()
        deadline = time.time() + 60
        while time.time() < deadline:
            with shared["lock"]:
                if shared["queue"].unfinished_tasks == 0:
                    break
            time.sleep(1)
        shared["shutdown"] = True
        t.join(timeout=20)
        db = SessionLocal()
        try:
            got = db.query(Company).filter(
                Company.user_id == uid, Company.name == "Heal Co").first()
            assert got is not None
            cid = got.id
        finally:
            db.close()
    finally:
        craw._fetch = orig_fetch
        craw._process_url = orig_process
    d2 = SessionLocal()
    try:
        for l in d2.query(Lead).filter(Lead.company_id == cid).all():
            d2.delete(l)
        # Contacts too: orphan rows keep recycled company_ids alive and
        # poison later saves' contact sets (flipped an EXCLUDED assert).
        d2.query(Contact).filter(Contact.company_id == cid).delete()
        d2.query(Company).filter(Company.id == cid).delete()
        d2.query(Company).filter(Company.name == "Poison Co").delete()
        d2.query(CrawlJob).filter(CrawlJob.id == jid).delete()
        d2.commit()
    finally:
        d2.close()


def test_no_cpp_style_comments_in_python():
    """Tripwire: `//` is not a Python comment (broke the crawler twice)."""
    import re
    import pathlib
    bad = []
    for path in pathlib.Path("app").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]  # ignore real comments
            code = re.sub(r"'[^']*'|\"[^\"]*\"", "", code)  # ignore strings
            if code.strip().startswith("//") or "//" in code:
                bad.append(f"{path}:{i}")
    assert not bad, f"C-style comments found: {bad}"


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
