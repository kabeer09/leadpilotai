"""Crawler save path: company+contacts+lead creation, exclusion, idempotency."""
from app.database import SessionLocal
from app.models import User, Source, Company, Contact, Lead
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
