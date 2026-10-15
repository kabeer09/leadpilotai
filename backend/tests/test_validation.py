"""Old-pipeline rules ported: validation, primary picking, multi-page enrich."""
from app.validation import (validate_contact_email, filter_valid_emails,
                            pick_best_email, normalize_email, domain_matches)
from app.database import SessionLocal
from app.models import Company, Contact, Lead, User, Source


def test_role_emails_valid_noreply_rejected():
    assert validate_contact_email("info@shop.example")["valid"] is True
    assert validate_contact_email("sales-a@shop.example")["valid"] is True
    assert validate_contact_email("noreply@shop.example")["valid"] is False
    assert validate_contact_email("bounces@shop.example")["valid"] is False
    assert validate_contact_email("logo.png@shop.example")["valid"] is False
    assert validate_contact_email("not-an-email")["valid"] is False


def test_filter_dedupes_and_cleans():
    out = filter_valid_emails(["A@Shop.example ", "a@shop.example",
                               "noreply@shop.example", "bad"])
    assert out == ["a@shop.example"]


def test_pick_best_prefers_own_domain():
    emails = ["boss@gmail.com", "info@shop.example", "sales@shop.example"]
    assert pick_best_email(emails, "https://www.shop.example") in (
        "info@shop.example", "sales@shop.example")
    assert pick_best_email(["boss@gmail.com"], "https://shop.example") == "boss@gmail.com"
    assert pick_best_email([], "https://shop.example") is None
    assert domain_matches("info@shop.example", "https://shop.example") is True
    assert domain_matches("info@other.example", "https://shop.example") is False


def _owner_ids():
    db = SessionLocal()
    u = db.query(User).filter(User.email == "val@test.example").first()
    if not u:
        from app.security import hash_password
        u = User(email="val@test.example", password_hash=hash_password("TestPass123"))
        db.add(u)
        db.commit()
        db.refresh(u)
    src = db.query(Source).filter(Source.user_id == u.id).first()
    if not src:
        src = Source(user_id=u.id, url="https://val.example", name="v",
                     status="COMPLETED")
        db.add(src)
        db.commit()
        db.refresh(src)
    out = (u.id, src.id)
    db.close()
    return out


def test_validation_pass_picks_primary_and_flags_invalid():
    from app.crawler import apply_contact_validation
    uid, sid = _owner_ids()
    db = SessionLocal()
    try:
        c = Company(user_id=uid, source_id=sid, name="Val Co",
                    website="valco.example")
        db.add(c)
        db.flush()
        for v in ["boss@gmail.com", "info@valco.example", "noreply@valco.example"]:
            db.add(Contact(user_id=uid, company_id=c.id, kind="EMAIL",
                           value=v, normalized=normalize_email(v),
                           source_url="https://valco.example"))
        db.commit()
        best = apply_contact_validation(db, c)
        assert best == "info@valco.example"
        rows = {r.value: r for r in db.query(Contact).filter(
            Contact.company_id == c.id).all()}
        assert rows["info@valco.example"].is_primary is True
        assert rows["boss@gmail.com"].is_primary is False
        assert rows["noreply@valco.example"].verification == "invalid"
        # nothing deleted
        assert len(rows) == 3
    finally:
        cid = c.id
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


def test_fetch_retries_transport_errors():
    import httpx
    from app import crawler as craw
    calls = []

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        content = b"<html><head><title>T</title></head><body>hi</body></html>"
        text = content.decode()

    def fake_get(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("slow server")
        return FakeResp()

    client = httpx.Client()
    orig = client.get
    client.get = fake_get
    try:
        # example.com resolves for real (SSRF check passes); transport mocked
        html, err = craw._fetch(client, "https://example.com/")
    finally:
        client.get = orig
        client.close()
    assert err is None and html is not None and len(calls) == 2


def test_ambiente_parse_record_maps_all_fields():
    from app.fair_adapters import parse_ambiente_record
    seed = ("https://ambiente.messefrankfurt.com/frankfurt/en/"
            "exhibitor-search.html?page=1&pagesize=30")
    rec = parse_ambiente_record({
        "name": "Lampen <b>AG</b>", "rewriteId": "lampen-ag-1",
        "homepage": "lampen.example",
        "address": {"country": {"label": "Germany"}, "city": "Frankfurt",
                    "street": "Main 1", "zip": "60311",
                    "email": "INFO@Lampen.Example", "tel": "+49 69 123"},
        "exhibition": {"name": "Ambiente",
                       "exhibitionHall": [{"name": "Hall 3",
                                           "stand": [{"name": "3.0 A51"}]}]},
        "description": {"text": "Designer lamps."},
        "products": {"products": [{"name": "Pendant Luna"}]},
        "categories": [{"name": "Lighting"}],
    }, "http://x", seed)
    assert rec is not None
    assert rec["profile_name"] == "Lampen AG"  # HTML stripped
    assert rec["official_website"] == "https://lampen.example"
    assert rec["country"] == "Germany" and rec["city"] == "Frankfurt"
    assert rec["hall"] == "Hall 3" and rec["booth"] == "3.0 A51"
    assert rec["emails"] == ["info@lampen.example"]
    assert rec["phones"] == ["+49 69 123"]
    assert rec["products"] == [{"name": "Pendant Luna", "category": ""}]
    assert rec["industry"] == "Lighting"
    assert rec["profile_url"].startswith(
        "https://ambiente.messefrankfurt.com/frankfurt/en/exhibitor-search.html#")
    assert parse_ambiente_record({"name": "   "}, "http://x", seed) is None
    assert parse_ambiente_record({"name": "<b></b>"}, "http://x", seed) is None


def test_ingest_adapter_records_saves_and_enriches(monkeypatch):
    """Ingest path: records become companies; contact-less rows with a site enrich."""
    import app.crawler as craw
    from app.database import SessionLocal
    from app.models import User, Source, Company, Contact, Lead
    db = SessionLocal()
    u = db.query(User).filter(User.email == "val@test.example").first()
    if not u:
        from app.security import hash_password
        u = User(email="val@test.example", password_hash=hash_password("TestPass123"))
        db.add(u)
        db.commit()
        db.refresh(u)
    src = db.query(Source).filter(Source.user_id == u.id).first()
    if not src:
        src = Source(user_id=u.id, url="https://val.example", name="v",
                     status="COMPLETED")
        db.add(src)
        db.commit()
        db.refresh(src)
    uid, sid, job_url = u.id, src.id, "https://fake-fair.example/search"
    from app.models import CrawlJob
    job = CrawlJob(user_id=uid, source_id=sid, url=job_url,
                   max_pages=10, max_depth=1, status="CRAWLING")
    db.add(job)
    db.commit()
    jid = job.id
    db.close()

    recs = [{
        "title": "Ingest Co", "h1": "Ingest Co", "profile_name": "Ingest Co",
        "description": "Metal decor wholesale.", "industry": "Furniture",
        "country": "India", "city": "Moradabad", "address": "A 1, Moradabad",
        "hall": "H1", "booth": "B2", "products": [{"name": "Urli", "category": ""}],
        "official_website": "https://ingest.example",
        "profile_url": job_url + "#exhibitor=ingest-co",
        "emails": [], "phones": [], "whatsapp": [], "official_links": [],
        "links": [], "text_len": 5000,
    }]

    def fake_enrich(db_, uid_, comp, site, client, render_budget, job_id=None):
        assert site == "https://ingest.example"
        db_.add(Contact(user_id=uid_, company_id=comp.id, kind="EMAIL",
                        value="hello@ingest.example",
                        normalized="hello@ingest.example",
                        source_url=site, source_page="contact"))
        return 1, 2

    monkeypatch.setattr(craw, "_enrich_from_official_site", fake_enrich)
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        shared = {"lock": __import__("threading").Lock(), "queue": None,
                  "seen": set(), "pages": 0, "companies": 0, "contacts": 0,
                  "render_budget": [10], "errors": [], "cancel": None,
                  "directory_mode": True}
        import threading
        shared["cancel"] = threading.Event()
        from queue import Queue
        shared["queue"] = Queue()
        n = craw._ingest_adapter_records(db, job, recs, shared)
        assert n == 1
        c = db.query(Company).filter(Company.user_id == uid,
                                    Company.name == "Ingest Co").first()
        assert c is not None and c.hall == "H1" and c.booth == "B2"
        assert c.country == "India" and c.category == "Furniture"
        rows = db.query(Contact).filter(Contact.company_id == c.id).all()
        assert {r.value for r in rows} == {"hello@ingest.example"}
        assert shared["pages"] >= 2  # enrichment visits counted
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


def test_andmore_details_parses_contact_card(monkeypatch):
    import app.fair_adapters as fa
    import httpx

    payload = {"count": 1, "data": [{
        "companyDetails": {
            "companyName": "A & B Home Group",
            "activeLeases": [{"showrooms": [
                {"showroomBuildingName": "Building 1",
                 "showroomFloor": "11", "showroom": "B2"}]}]},
        "directoryContactInfo": {
            "address1": "800 N. Haven Avenue", "city": "Ontario",
            "zip": "91764", "companyEmail1": "customerservice@abhomeinc.com",
            "directoryContactEmail": "lauram@abhomeinc.com",
            "directoryContactFirstName": "Laura",
            "directoryContactLastName": "Maguire",
            "primaryPhoneNo": "9097585366",
            "tollFreePhoneNo": "888-868-1808",
            "faxNumber": "9097585377"},
    }]}

    class FakeResp:
        status_code = 200

        def json(self):
            return payload

    monkeypatch.setattr(httpx.Client, "get",
                        lambda self, url, **k: FakeResp())
    d = fa.fetch_exhibitor_details(
        "https://www.atlantamarket.com/exhibitor/118533")
    assert d is not None
    assert "customerservice@abhomeinc.com" in d["emails"]
    assert "lauram@abhomeinc.com" in d["emails"]
    assert "9097585366" in d["phones"] and "8888681808" in d["phones"]
    assert "Laura Maguire" in d["contact_names"]
    assert "Ontario" in (d["address"] or "")
    assert "B2" in (d["booth"] or "")
    # non-ANDMORE URLs untouched
    assert fa.fetch_exhibitor_details("https://example.com/x") is None


def test_details_survive_render_replacement(monkeypatch):
    """Ordering: shell-check on RAW data; details re-merged after render."""
    import app.crawler as craw
    shell_html = ("<html><head><title>{exhibitorName} at Fair</title></head>"
                  "<body><div id='root'></div>"
                  "<script src='/static/app.js'></script></body></html>")
    real_html = ("<html><head><title>{exhibitorName} at Fair</title></head>"
                 "<body><p>Giftware wholesale.</p></body></html>")
    d0 = craw.extract_page("https://fair.example/exhibitor/1", shell_html)
    assert craw._looks_like_js_shell(shell_html, d0) is True  # raw: shell
    det = {"emails": ["a@mint.example"], "phones": ["123456789"],
           "contact_names": [], "address": None, "booth": None,
           "company_name": "Mint", "website": None}
    # merge (as _process_url does)
    for e in det["emails"]:
        d0["emails"].append(e)
    # shell check must have run BEFORE merge in real flow; raw check is True
    # re-extract wipes, then re-merge restores:
    d1 = craw.extract_page("https://fair.example/exhibitor/1", real_html)
    assert d1["emails"] == []  # wipe confirmed (the old bug surface)
    for e in det["emails"]:
        if e not in d1["emails"]:
            d1["emails"].append(e)
    d1["details_name"] = det["company_name"]
    assert d1["emails"] == ["a@mint.example"]
    # name falls back to details_name when page title is a template
    assert "{" in (d1["title"] or "")
    from app.crawler import _save_company
    from app.database import SessionLocal
    from app.models import User, Source, Company, Contact, Lead
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "val@test.example").first()
        s = db.query(Source).filter(Source.user_id == u.id).first()
        comp = _save_company(db, u.id, s.id,
                             "https://fair.example/exhibitor/1", d1, 1)
        assert comp is not None and comp.name == "Mint"
        cid = comp.id
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


def test_lvm_adapter_parses_buckets(monkeypatch):
    import app.fair_adapters as fa
    import httpx

    class FakeResp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    def fake_get(self, url, **k):
        if "invalidaz" in url:
            return FakeResp(["X"])
        if "az=A" in url:
            return FakeResp({"items": [
                {"exhibitorId": "65615", "name": "aspenhome", "type": "exhibitor"},
                {"exhibitorId": "65615", "name": "aspenhome", "type": "exhibitor"},
                {"exhibitorId": "", "name": "junk"}]})
        return FakeResp({"items": []})

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **k):
            return fake_get(self, url, **k)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    urls, err = fa.discover_profiles(
        "https://www.lasvegasmarket.com/exhibitor/Directory")
    assert err is None
    assert urls == ["https://www.lasvegasmarket.com/exhibitor/65615"]  # deduped


def test_adapter_falls_back_gracefully(monkeypatch):
    import app.fair_adapters as fa
    import httpx

    def boom(self, *a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.Client, "get", boom)
    urls, err = fa.discover_profiles(
        "https://www.lasvegasmarket.com/exhibitor/Directory")
    assert urls is None and err is not None
    # unknown domains: no adapter, generic crawl
    urls, err = fa.discover_profiles("https://example.com/")
    assert urls is None and err is None


def test_enrich_visits_contact_pages(monkeypatch):
    """Enrichment must fetch contact/about subpages, not just home."""
    from app.crawler import _enrich_from_official_site
    import app.crawler as craw
    uid, sid = _owner_ids()
    home_html = ("<html><head><title>Valco</title></head><body>Welcome. "
                 '<a href="/contact-us">Contact</a> '
                 '<a href="/about">About</a></body></html>')
    contact_html = ("<html><head><title>Contact</title></head><body>"
                    "Email hello@valco.example, phone +1-555-0123.</body></html>")
    fetched = []

    def fake_fetch(client, url):
        fetched.append(url)
        if "contact" in url or "about" in url:
            return contact_html, None
        return home_html, None

    monkeypatch.setattr(craw, "_fetch", fake_fetch)
    monkeypatch.setattr(craw, "_robots_allowed", lambda base, url: True)
    db = SessionLocal()
    try:
        c = Company(user_id=uid, source_id=sid, name="Valco",
                    website="valco.example")
        db.add(c)
        db.flush()
        import httpx
        with httpx.Client() as client:
            added, pages = _enrich_from_official_site(
                db, uid, c, "https://valco.example", client, [10],
                job_id=None)
        assert added >= 2  # email + phone from the contact page
        assert pages >= 2  # home + at least one subpage
        assert any("contact" in u or "about" in u for u in fetched[1:])
        rows = db.query(Contact).filter(Contact.company_id == c.id).all()
        assert "contact" in {r.source_page for r in rows}  # from subpage, not home
        primaries = [r for r in rows if r.is_primary]
        assert len(primaries) == 1 and primaries[0].value == "hello@valco.example"
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
