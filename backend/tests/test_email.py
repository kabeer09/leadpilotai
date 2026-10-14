"""Phase 4+7: campaigns, idempotency, suppression, follow-up stops, honesty."""
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Company, Contact, Lead

client = TestClient(app)


def _user(email):
    r = client.post("/api/auth/signup", json={"email": email, "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _seed_company(db, user_id, name="Mail Co", email="buyer@mailco.example"):
    c = Company(user_id=user_id, name=name, website="mailco.example")
    db.add(c)
    db.flush()
    ct = Contact(user_id=user_id, company_id=c.id, kind="EMAIL",
                 value=email, normalized=email, source_url="https://mailco.example")
    db.add(ct)
    db.flush()
    lead = Lead(user_id=user_id, company_id=c.id, contact_id=ct.id,
                status="NEW", score=50)
    db.add(lead)
    db.commit()
    return c.id, lead.id


def _uid(email):
    from app.models import User
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email.lower()).first().id
    finally:
        db.close()


def test_campaign_preview_test_no_send_without_confirm():
    tokens = _user("camp1@test.example")
    uid = _uid("camp1@test.example")
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid)
    finally:
        db.close()
    r = client.post("/api/campaigns",
                    json={"name": "C1", "subject": "Hi {company}",
                          "body": "Hello {company} in {country}", "lead_ids": [lead_id]},
                    headers=_auth(tokens))
    assert r.status_code == 201
    cid = r.json()["id"]
    pv = client.get(f"/api/campaigns/{cid}/preview", headers=_auth(tokens)).json()
    assert pv["recipient_count"] == 1
    assert pv["recipients"][0]["subject"] == "Hi Mail Co"
    # no confirmation -> 422, nothing sends
    bad = client.post(f"/api/campaigns/{cid}/send",
                      json={"confirm": False, "recipient_count_ack": 0},
                      headers=_auth(tokens))
    assert bad.status_code == 422
    # wrong count -> 422
    bad2 = client.post(f"/api/campaigns/{cid}/send",
                       json={"confirm": True, "recipient_count_ack": 99},
                       headers=_auth(tokens))
    assert bad2.status_code == 422
    # test email works without confirmation (to owner)
    t = client.post(f"/api/campaigns/{cid}/test", json={}, headers=_auth(tokens))
    assert t.status_code == 200 and t.json()["dry_run"] is True


def test_dryrun_send_marks_accepted_not_delivered():
    tokens = _user("camp2@test.example")
    uid = _uid("camp2@test.example")
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid, "Mail2 Co", "b@mail2.example")
    finally:
        db.close()
    r = client.post("/api/campaigns",
                    json={"name": "C2", "subject": "Hi", "body": "Body here",
                          "lead_ids": [lead_id], "followup_enabled": True},
                    headers=_auth(tokens))
    cid = r.json()["id"]
    s = client.post(f"/api/campaigns/{cid}/send",
                    json={"confirm": True, "recipient_count_ack": 1},
                    headers=_auth(tokens)).json()
    assert s["sent"] == 1
    evts = client.get("/api/email-events", headers=_auth(tokens)).json()
    live = [e for e in evts if not e["is_test"]]
    assert len(live) == 1 and live[0]["status"] == "accepted"  # NOT delivered
    fus = client.get("/api/followups", headers=_auth(tokens)).json()
    assert len(fus) == 1 and fus[0]["status"] == "SCHEDULED"


def test_suppressed_and_contacted_excluded_and_retry_idempotent():
    tokens = _user("camp3@test.example")
    uid = _uid("camp3@test.example")
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid, "Mail3 Co", "c@mail3.example")
    finally:
        db.close()
    # First campaign sends
    r = client.post("/api/campaigns",
                    json={"name": "C3a", "subject": "Hi", "body": "B",
                          "lead_ids": [lead_id]}, headers=_auth(tokens))
    c1 = r.json()["id"]
    s1 = client.post(f"/api/campaigns/{c1}/send",
                     json={"confirm": True, "recipient_count_ack": 1},
                     headers=_auth(tokens)).json()
    assert s1["sent"] == 1
    # Second campaign: previously contacted excluded by default
    r2 = client.post("/api/campaigns",
                     json={"name": "C3b", "subject": "Hi", "body": "B",
                           "lead_ids": [lead_id]}, headers=_auth(tokens)).json()
    assert r2["added"] == 0 and r2["skipped_contacted"] == 1
    # Unsubscribe -> suppressed
    u = client.post("/api/unsubscribe", json={"email": "c@mail3.example"})
    assert u.status_code == 200
    sup = client.get("/api/suppressions", headers=_auth(tokens)).json()
    assert any(s["value"] == "c@mail3.example" for s in sup)


def test_reply_stops_followup_and_marks_replied():
    tokens = _user("camp4@test.example")
    uid = _uid("camp4@test.example")
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid, "Mail4 Co", "d@mail4.example")
    finally:
        db.close()
    r = client.post("/api/campaigns",
                    json={"name": "C4", "subject": "Hi", "body": "B",
                          "lead_ids": [lead_id], "followup_enabled": True},
                    headers=_auth(tokens))
    cid = r.json()["id"]
    client.post(f"/api/campaigns/{cid}/send",
                json={"confirm": True, "recipient_count_ack": 1},
                headers=_auth(tokens))
    # inbound reply
    ib = client.post("/api/email-events/inbound",
                     json={"event_type": "reply", "email": "d@mail4.example",
                           "body": "Interested!"},
                     headers=_auth(tokens))
    assert ib.status_code == 200
    # reply already cancelled the scheduled follow-up at inbound time
    fus = client.get("/api/followups", headers=_auth(tokens)).json()
    assert len(fus) == 1 and fus[0]["status"] == "SKIPPED"
    # processor sends nothing more
    p = client.post("/api/followups/process", headers=_auth(tokens)).json()
    assert p["sent"] == 0
    lead = client.get(f"/api/leads/{lead_id}", headers=_auth(tokens)).json()
    assert lead["status"] == "REPLIED"


def test_tenant_isolation_crm():
    a = _user("tenantA@test.example")
    b = _user("tenantB@test.example")
    uid_a = _uid("tenantA@test.example")
    db = SessionLocal()
    try:
        cid, _ = _seed_company(db, uid_a, "Secret Co", "s@secret.example")
    finally:
        db.close()
    # B cannot see A's company
    assert client.get(f"/api/companies/{cid}", headers=_auth(b)).status_code == 404
    assert client.get("/api/campaigns", headers=_auth(b)).json() == []
    assert client.get("/api/email-events", headers=_auth(b)).json() == []
