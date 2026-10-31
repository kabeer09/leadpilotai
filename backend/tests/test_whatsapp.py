"""WhatsApp module: country-code fixing, availability check, provider sends.

Honesty contracts under test:
- offline checks never claim 'on_whatsapp' (format-only = 'identified');
- dryrun provider never marks registration;
- provider-mode sends require double confirmation (confirm + exact count);
- live probes require WHATSAPP_PROVIDER=meta and confirm=true.
"""
import re

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Company, Contact, Lead, Source, User, WhatsAppMessage
from app.whatsapp import phone_e164, inspect_phone, region_for, wa_link

client = TestClient(app)


def _user(email):
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _uid(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email.lower()).first().id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# phone_e164 — the country-code engine
# ---------------------------------------------------------------------------

def test_phone_e164_fixes_local_formats():
    # German local with trunk prefix
    assert phone_e164("030 12345678", "Germany") == ("+493012345678", "from_country")
    # US local, country known
    assert phone_e164("415-555-1234", "United States") == ("+14155551234", "from_country")
    # India local mobile (no trunk prefix for mobiles)
    assert phone_e164("9876543210", "India") == ("+919876543210", "from_country")


def test_phone_e164_recovers_lost_plus():
    # country known: the embedded country code is recognized directly
    assert phone_e164("393884890731", "Italy") == ("+393884890731", "from_country")
    assert phone_e164("9779802343024", "Nepal") == ("+9779802343024", "from_country")
    assert phone_e164("905070754171", "Turkey") == ("+905070754171", "from_country")
    # no country at all — still recoverable because the CC is inside
    assert phone_e164("393783072885", None) == ("+393783072885", "cc_missing_plus")
    assert phone_e164("905556816406", None) == ("+905556816406", "cc_missing_plus")


def test_phone_e164_zero_zero_and_plus_forms():
    assert phone_e164("0049 30 12345678", None) == ("+493012345678", "already_intl")
    assert phone_e164("+49 30 12345678", None) == ("+493012345678", "already_intl")


def test_phone_e164_default_region_beats_cc_guess():
    # Bare 10-digit US number, no country: fair default (High Point = US)
    # must win — the '+'-guess would make it Swiss +41...
    assert phone_e164("4045551234", None, "US") == ("+14045551234", "default_region")
    # But an actual foreign number with lost '+' still resolves correctly
    assert phone_e164("393783072885", None, "US") == ("+393783072885", "cc_missing_plus")


def test_phone_e164_honest_failures():
    # too short to be a real number anywhere
    assert phone_e164("12345", "Germany") == ("", "invalid")
    # 9 digits, no country, no valid CC prefix: cannot invent digits
    num, how = phone_e164("984431602", None)
    assert num == "" and how == "no_country"
    # garbage with explicit + is invalid, not 'no country'
    assert phone_e164("+99999", None) == ("", "invalid")
    assert phone_e164("", "Germany") == ("", "empty")


def test_region_for_country_names():
    assert region_for("United States") == "US"
    assert region_for("usa") == "US"
    assert region_for("Turkey") == "TR"
    assert region_for("turkiye") == "TR"
    assert region_for("United Kingdom") == "GB"
    assert region_for("Atlantis") is None
    assert region_for(None) is None


def test_inspect_phone_line_types():
    landline = inspect_phone("+493012345678")
    assert landline["valid"] and landline["region"] == "DE"
    assert landline["line_type"] == "landline"
    assert landline["wa_likely"] is False
    mobile = inspect_phone("+919876543210")
    assert mobile["valid"] and mobile["region"] == "IN"
    assert mobile["wa_likely"] is True
    # not a claim about WhatsApp registration, only line type
    assert inspect_phone("+9999")["valid"] is False


def test_wa_link_builds_me():
    assert wa_link("+39 388 489 0731", "hi there") == \
        "https://wa.me/393884890731?text=hi%20there"


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

def _seed(uid, name, country, wa_values, phone_value=None,
          wa_status="identified", source_url="https://www.highpointmarket.org/"):
    """One company with WHATSAPP/PHONE rows + a lead per company."""
    db = SessionLocal()
    try:
        src = Source(user_id=uid, url=source_url, name=name[:40])
        db.add(src)
        db.flush()
        co = Company(user_id=uid, source_id=src.id, name=name, country=country,
                     lifecycle="active")
        db.add(co)
        db.flush()
        lead_contact = None
        for v in wa_values:
            c = Contact(user_id=uid, company_id=co.id, kind="WHATSAPP",
                        value=v, normalized=v, whatsapp_status=wa_status)
            db.add(c)
            db.flush()
            lead_contact = lead_contact or c
        if phone_value:
            c = Contact(user_id=uid, company_id=co.id, kind="PHONE",
                        value=phone_value, normalized=phone_value)
            db.add(c)
            db.flush()
            lead_contact = lead_contact or c
        lead = Lead(user_id=uid, company_id=co.id,
                    contact_id=lead_contact.id if lead_contact else None,
                    status="NEW", score=50)
        db.add(lead)
        db.commit()
        return lead.id, [c.id for c in db.query(Contact).filter(
            Contact.company_id == co.id).all()]
    finally:
        db.close()


def test_normalize_fixes_country_codes_and_merges_duplicates():
    t = _user("wa_norm@test.example")
    uid = _uid("wa_norm@test.example")
    # Italy: same number twice — one with '+', one without (real HP/Ambiente case)
    lead_it, cids = _seed(uid, "Borgo IT", "Italy",
                          ["+393884890731", "393884890731"])
    # Nepal: CC without '+'
    _seed(uid, "Aja NP", "Nepal", ["9779802343024"])
    # US local phone under a High Point source (no country on company)
    _seed(uid, "Emerald US", None, [], phone_value="4045551234")

    r = client.post("/api/whatsapp/normalize",
                    json={"apply": True}, headers=_auth(t))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["applied"] is True
    assert data["merged"] >= 1          # the duplicate Italian row
    assert data["fixed"] >= 3           # 3938..., 9779..., 4045...
    assert data["invalid"] == 0

    db = SessionLocal()
    try:
        it_rows = db.query(Contact).filter(
            Contact.company_id.in_(
                db.query(Company.id).filter(Company.name == "Borgo IT"))).all()
        # duplicate merged away, survivor in strict E.164
        assert len(it_rows) == 1
        assert it_rows[0].normalized == "+393884890731"
        np_row = db.query(Contact).join(
            Company, Contact.company_id == Company.id).filter(
            Company.name == "Aja NP").first()
        assert np_row.normalized == "+9779802343024"
        us_row = db.query(Contact).join(
            Company, Contact.company_id == Company.id).filter(
            Company.name == "Emerald US").first()
        assert us_row.normalized == "+14045551234"
    finally:
        db.close()


def test_normalize_dry_run_applies_nothing():
    t = _user("wa_norm2@test.example")
    uid = _uid("wa_norm2@test.example")
    _seed(uid, "Dryrun Co", "Turkey", ["905070754171"])
    r = client.post("/api/whatsapp/normalize",
                    json={"apply": False}, headers=_auth(t))
    assert r.status_code == 200
    assert r.json()["fixed"] == 1 and r.json()["applied"] is False
    db = SessionLocal()
    try:
        row = db.query(Contact).join(
            Company, Contact.company_id == Company.id).filter(
            Company.name == "Dryrun Co").first()
        assert row.normalized == "905070754171"  # untouched
    finally:
        db.close()


def test_check_offline_updates_status_honestly():
    t = _user("wa_check@test.example")
    uid = _uid("wa_check@test.example")
    # valid mobile (status stays/becomes identified — NOT on_whatsapp)
    _seed(uid, "Good Co", "India", ["9876543210"])
    # broken number -> invalid
    _seed(uid, "Bad Co", "United States", ["984431602"])
    r = client.post("/api/whatsapp/check",
                    json={"lead_ids": []}, headers=_auth(t))
    assert r.status_code == 200, r.text
    fmt = r.json()["format"]
    assert fmt["mobile"] == 1 and fmt["fixed"] == 1
    assert fmt["invalid"] == 1
    assert r.json()["live"] is None
    db = SessionLocal()
    try:
        good = db.query(Contact).join(
            Company, Contact.company_id == Company.id).filter(
            Company.name == "Good Co").first()
        assert good.normalized == "+919876543210"
        assert good.whatsapp_status == "identified"   # format only!
        bad = db.query(Contact).join(
            Company, Contact.company_id == Company.id).filter(
            Company.name == "Bad Co").first()
        assert bad.whatsapp_status == "invalid"
    finally:
        db.close()


def test_live_check_requires_meta_and_confirm(monkeypatch):
    from app import config as cfg
    t = _user("wa_live@test.example")
    uid = _uid("wa_live@test.example")
    _seed(uid, "Live Co", "India", ["9876543210"])
    # dryrun provider: live not possible
    r = client.post("/api/whatsapp/check",
                    json={"live": True}, headers=_auth(t))
    assert r.status_code == 400 and "WHATSAPP_PROVIDER=meta" in r.json()["detail"]
    # meta provider but no confirm: must refuse (probes cost money)
    monkeypatch.setattr(cfg.settings, "WHATSAPP_PROVIDER", "meta")
    r = client.post("/api/whatsapp/check",
                    json={"live": True, "confirm": False}, headers=_auth(t))
    assert r.status_code == 422
    assert "confirm" in r.json()["detail"]


def test_provider_send_double_confirm_and_dryrun_honesty(monkeypatch):
    from app import config as cfg
    t = _user("wa_send@test.example")
    uid = _uid("wa_send@test.example")
    lead_ok, _ = _seed(uid, "Send Co", "India", [], phone_value="9876543210")
    # a lead Meta already told us is unreachable must be skipped
    # (the number itself must be valid E.164 — else it'd be 'skipped_invalid')
    lead_off, _ = _seed(uid, "Off Co", "India", ["+919812345678"],
                        wa_status="not_on_whatsapp")
    ids = [lead_ok, lead_off]

    # no confirmation -> 422 with the exact counts in the detail
    r = client.post("/api/whatsapp/send",
                    json={"message": "Hello {company}", "lead_ids": ids,
                          "mode": "provider"}, headers=_auth(t))
    assert r.status_code == 422, r.text
    assert "recipient_count_ack" in r.json()["detail"]

    # meta selected but unconfigured -> clear 400 before any loop
    monkeypatch.setattr(cfg.settings, "WHATSAPP_PROVIDER", "meta")
    r = client.post("/api/whatsapp/send",
                    json={"message": "Hello", "lead_ids": ids,
                          "mode": "provider", "confirm": True,
                          "recipient_count_ack": 1}, headers=_auth(t))
    assert r.status_code == 400
    monkeypatch.setattr(cfg.settings, "WHATSAPP_PROVIDER", "dryrun")

    # proper confirmation -> dryrun records the send
    r = client.post("/api/whatsapp/send",
                    json={"message": "Hello {company}", "lead_ids": ids,
                          "mode": "provider", "confirm": True,
                          "recipient_count_ack": 1}, headers=_auth(t))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["provider"] == "whatsapp-dryrun"
    assert data["sent"] == 1 and data["failed"] == 0
    assert data["skipped_not_on_whatsapp"] == 1
    db = SessionLocal()
    try:
        msg = db.query(WhatsAppMessage).filter(
            WhatsAppMessage.user_id == uid).first()
        assert msg is not None
        assert msg.status == "accepted"           # accepted, never 'delivered'
        assert re.fullmatch(r"\+919876543210", msg.to_phone)
        # dryrun proved NOTHING about registration:
        ok_co = db.query(Contact).join(
            Company, Contact.company_id == Company.id).filter(
            Company.name == "Send Co").first()
        assert ok_co.whatsapp_status != "on_whatsapp"
    finally:
        db.close()


def test_links_mode_stays_back_compatible():
    t = _user("wa_links@test.example")
    uid = _uid("wa_links@test.example")
    lead_id, _ = _seed(uid, "Links Co", "Italy", ["393884890731"])
    r = client.post("/api/whatsapp/send",
                    json={"message": "Ciao {company}", "lead_ids": [lead_id]},
                    headers=_auth(t))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 1
    link = data["links"][0]["link"]
    assert link.startswith("https://wa.me/393884890731")
    assert "Links%20Co" in link


def test_config_endpoint_reports_provider():
    t = _user("wa_cfg@test.example")
    r = client.get("/api/whatsapp/config", headers=_auth(t))
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "whatsapp-dryrun"
    assert data["configured"] is True      # dryrun is always usable
    assert data["sent_today"] == 0
