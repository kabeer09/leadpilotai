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
