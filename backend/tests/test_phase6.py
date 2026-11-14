"""Phase 6 tests: CRM pipeline, conversion funnel, campaign guards.

Honesty contracts under test (master prompt):
- funnel stages are strictly nested and rates are null (never a fake 0%)
  when their denominator is zero;
- every figure is tenant-scoped — another tenant's rows never leak;
- lead-score bands reuse the SAME thresholds as POST /ai/lead-score and
  band counts sum exactly to the scored population;
- band filtering is bounded — past the scan cap it refuses with a typed
  422 instead of silently returning a partial page;
- campaigns NEVER re-mail: suppression and prior contact are re-checked
  at SEND TIME (not only at build time), skipped recipients are marked
  with an honest reason, no message row is written for them, and a
  campaign where nothing went out never claims status SENT;
- audience filters (require_research / min_buyer_fit) only ever use
  persisted research rows — no evaluation = fit unproven = excluded;
- confirmation is exact: sending with nothing pending is a 409, not a
  fake success;
- ALL external APIs mocked/absent — zero network, zero credits.
"""
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import (Company, Contact, Lead, EmailMessage, ProviderEvent,
                        Suppression)
from app.models_research import ContactEvidence, EvaluationResult

client = TestClient(app)

_uid = {"n": 0}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _user():
    _uid["n"] += 1
    r = client.post("/api/auth/signup",
                    json={"email": f"p6-{_uid['n']}@example.com",
                          "password": "TestPass123"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _auth(t):
    return {"Authorization": f"Bearer {t['access_token']}"}


def _uid_of(tok):
    from app.security import decode_token
    return decode_token(tok["access_token"])


def _seed_company(db, user_id, name="Mail Co", email="buyer@mailco.example",
                  website="mailco.example", country="United Kingdom"):
    c = Company(user_id=user_id, name=name, website=website, country=country)
    db.add(c)
    db.flush()
    ct = Contact(user_id=user_id, company_id=c.id, kind="EMAIL",
                 value=email, normalized=email.lower(),
                 source_url="https://mailco.example")
    db.add(ct)
    db.flush()
    lead = Lead(user_id=user_id, company_id=c.id, contact_id=ct.id,
                status="NEW", score=50)
    db.add(lead)
    db.commit()
    return c.id, lead.id


def _mk_evidence(db, user_id, company_id, value="ev@mailco.example"):
    db.add(ContactEvidence(
        user_id=user_id, company_id=company_id, contact_type="email",
        raw_value=value, source_url="https://mailco.example/about",
        extraction_method="mailto_link", confidence=0.9,
        validation_status="FORMAT_VALID"))
    db.commit()


def _mk_evaluation(db, user_id, company_id, buyer_fit=80.0,
                   evidence_quality=70.0):
    db.add(EvaluationResult(
        user_id=user_id, company_id=company_id,
        buyer_fit_score=buyer_fit, overall_score=buyer_fit,
        contact_completeness_score=80.0,
        evidence_quality_score=evidence_quality,
        validation_coverage=50.0,
        result_json='{"buyer_type": "Importer"}',
        issues="[]", warnings="[]"))
    db.commit()


def _mk_message(db, user_id, lead_id, to_email, status="accepted"):
    m = EmailMessage(user_id=user_id, lead_id=lead_id, to_email=to_email,
                     subject="Hi", body="Body", provider="dryrun",
                     status=status, is_test=False)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m.id


def _mk_event(db, user_id, message_id, event_type):
    db.add(ProviderEvent(user_id=user_id, message_id=message_id,
                         event_type=event_type, payload="{}"))
    db.commit()


# ---------------------------------------------------------------------------
# conversion funnel
# ---------------------------------------------------------------------------

def test_funnel_requires_auth():
    assert client.get("/api/analytics/funnel").status_code == 401


def test_funnel_empty_rates_are_null_never_fake_zero():
    t = _user()
    body = client.get("/api/analytics/funnel", headers=_auth(t)).json()
    assert body["record_type"] == "conversion_funnel"
    assert [s["key"] for s in body["stages"]] == [
        "discovered", "with_email", "researched", "emailed", "replied"]
    for s in body["stages"]:
        assert s["count"] == 0
        # no denominator anywhere -> every rate must be null, not 0 or 100
        assert s["pct_of_previous"] is None
        assert s["pct_of_first"] is None
    rates = body["rates"]
    assert rates["delivery_rate_pct"] is None
    assert rates["reply_rate_pct"] is None
    assert rates["bounce_rate_pct"] is None
    assert rates["followups_due"] == 0
    assert rates["denominators"] == {"attempted": 0, "delivered": 0,
                                     "bounced": 0, "replies": 0}


def test_funnel_stages_are_strictly_nested():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        # A: directory row only — no email contact (discovered alone)
        db.add(Company(user_id=uid, name="Bare Co"))
        db.flush()
        # B: has an email (discovered -> with_email)
        _seed_company(db, uid, "Mail Co", "b@mail.example")
        # C: email + evidence + emailed + replied
        cid_c, lead_c = _seed_company(db, uid, "Deep Co", "c@deep.example")
        _mk_evidence(db, uid, cid_c)
        msg_id = _mk_message(db, uid, lead_c, "c@deep.example")
        _mk_event(db, uid, msg_id, "reply")
        db.commit()
    finally:
        db.close()

    body = client.get("/api/analytics/funnel", headers=_auth(t)).json()
    counts = {s["key"]: s["count"] for s in body["stages"]}
    assert counts == {"discovered": 3, "with_email": 2, "researched": 1,
                      "emailed": 1, "replied": 1}

    # strictly nested: each stage is a subset of the previous one
    seq = [s["count"] for s in body["stages"]]
    assert all(a >= b for a, b in zip(seq, seq[1:])), seq

    pct = {s["key"]: s["pct_of_previous"] for s in body["stages"]}
    assert pct["discovered"] is None          # first stage has no previous
    assert abs(pct["with_email"] - 66.7) < 0.1   # 2/3
    assert pct["researched"] == 50.0            # 1/2
    assert pct["emailed"] == 100.0              # 1/1
    assert pct["replied"] == 100.0              # 1/1

    # delivery/reply rates now have a real denominator
    assert body["rates"]["denominators"]["attempted"] == 1
    assert body["rates"]["denominators"]["replies"] == 1
    # accepted is NOT delivered (honest states): 0 of 1 delivered -> 0.0
    assert body["rates"]["delivery_rate_pct"] == 0.0
    assert body["rates"]["reply_rate_pct"] == 100.0


def test_funnel_is_tenant_scoped():
    a = _user()
    b = _user()
    uid_a = _uid_of(a)
    db = SessionLocal()
    try:
        cid, lead_id = _seed_company(db, uid_a, "Secret Co",
                                     "s@secret.example")
        _mk_evidence(db, uid_a, cid)
        msg_id = _mk_message(db, uid_a, lead_id, "s@secret.example")
        _mk_event(db, uid_a, msg_id, "reply")
    finally:
        db.close()

    body_b = client.get("/api/analytics/funnel", headers=_auth(b)).json()
    assert all(s["count"] == 0 for s in body_b["stages"])
    assert body_b["rates"]["denominators"]["attempted"] == 0

    body_a = client.get("/api/analytics/funnel", headers=_auth(a)).json()
    assert body_a["stages"][0]["count"] == 1


# ---------------------------------------------------------------------------
# CRM pipeline
# ---------------------------------------------------------------------------

def test_pipeline_empty_is_honest():
    t = _user()
    body = client.get("/api/crm/pipeline", headers=_auth(t)).json()
    assert body["record_type"] == "crm_pipeline"
    assert body["scope"]["scored_companies"] == 0
    assert body["scope"]["sampled"] is False
    for band in body["lead_score_bands"]:
        assert band["count"] == 0
        assert band["pct"] is None       # null, never a fake 0%
    assert body["research_coverage"]["pct_evidence"] is None
    assert body["contact_coverage"]["pct_email"] is None
    assert "null" in body["note"]


def test_pipeline_band_counts_sum_exactly_to_scored():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        # Hot: full evaluation + contact + complete profile
        hot_id, _ = _seed_company(db, uid, "Hot Co", "hot@hot.example")
        _mk_evaluation(db, uid, hot_id, buyer_fit=100.0,
                       evidence_quality=100.0)
        # Low: bare directory row, no research at all
        _seed_company(db, uid, "Cold Co", "cold@cold.example",
                      website=None, country=None)
    finally:
        db.close()

    body = client.get("/api/crm/pipeline", headers=_auth(t)).json()
    bands = body["lead_score_bands"]
    assert [b["band"] for b in bands] == ["Hot", "Warm", "Nurture", "Low"]
    total = sum(b["count"] for b in bands)
    assert total == body["scope"]["scored_companies"] == 2
    counts = {b["band"]: b["count"] for b in bands}
    assert counts["Hot"] == 1 and counts["Low"] == 1
    # percentages are real over a non-zero denominator (0% here is honest)
    pcts = {b["band"]: b["pct"] for b in bands}
    assert pcts["Hot"] == 50.0 and pcts["Low"] == 50.0
    assert pcts["Warm"] == 0.0 and pcts["Nurture"] == 0.0
    assert "same thresholds as POST /ai/lead-score" in body["lead_score_note"]


def test_pipeline_is_tenant_scoped():
    a = _user()
    b = _user()
    uid_a = _uid_of(a)
    db = SessionLocal()
    try:
        _seed_company(db, uid_a, "OnlyA Co", "onlya@a.example")
    finally:
        db.close()
    body_b = client.get("/api/crm/pipeline", headers=_auth(b)).json()
    assert body_b["scope"]["companies_in_scope"] == 0
    body_a = client.get("/api/crm/pipeline", headers=_auth(a)).json()
    assert body_a["scope"]["companies_in_scope"] == 1


# ---------------------------------------------------------------------------
# companies: band / research filters + explainable score on detail
# ---------------------------------------------------------------------------

def test_company_band_filter_rejects_unknown_band():
    t = _user()
    r = client.get("/api/companies?band=Rainbow", headers=_auth(t))
    assert r.status_code == 422
    assert "Hot" in r.json()["detail"]


def test_company_band_filter_returns_only_matching_band():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        hot_id, _ = _seed_company(db, uid, "Hot Co", "hot2@hot.example")
        _mk_evaluation(db, uid, hot_id, buyer_fit=100.0,
                       evidence_quality=100.0)
        _seed_company(db, uid, "Cold Co", "cold2@cold.example",
                      website=None, country=None)
    finally:
        db.close()

    hot = client.get("/api/companies?band=Hot", headers=_auth(t)).json()
    assert hot["total"] == 1
    assert hot["items"][0]["name"] == "Hot Co"
    assert hot["items"][0]["lead_score"]["band"] == "Hot"
    assert hot["items"][0]["has_research"] is True

    low = client.get("/api/companies?band=Low", headers=_auth(t)).json()
    assert low["total"] == 1
    assert low["items"][0]["name"] == "Cold Co"
    assert low["items"][0]["has_research"] is False

    # no filter -> both, each with a score attached
    allr = client.get("/api/companies", headers=_auth(t)).json()
    assert allr["total"] == 2
    assert all(i["lead_score"] is not None for i in allr["items"])


def test_company_band_filter_scan_is_bounded():
    from app import api_crm
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        _seed_company(db, uid, "Any Co", "any@any.example")
    finally:
        db.close()
    original = api_crm.SCORE_SCAN_LIMIT
    api_crm.SCORE_SCAN_LIMIT = 0
    try:
        r = client.get("/api/companies?band=Hot", headers=_auth(t))
        assert r.status_code == 422
        assert "narrow" in r.json()["detail"]
    finally:
        api_crm.SCORE_SCAN_LIMIT = original


def test_company_has_research_filter():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        res_id, _ = _seed_company(db, uid, "Researched Co",
                                  "res@res.example")
        _mk_evidence(db, uid, res_id)
        _seed_company(db, uid, "Plain Co", "plain@plain.example")
    finally:
        db.close()

    yes = client.get("/api/companies?has_research=true",
                     headers=_auth(t)).json()
    assert yes["total"] == 1
    assert yes["items"][0]["name"] == "Researched Co"

    no = client.get("/api/companies?has_research=false",
                    headers=_auth(t)).json()
    assert no["total"] == 1
    assert no["items"][0]["name"] == "Plain Co"


def test_company_detail_lead_score_factors_sum_exactly():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        cid, _ = _seed_company(db, uid, "Scored Co", "sc@sc.example")
        _mk_evidence(db, uid, cid)
        _mk_evaluation(db, uid, cid, buyer_fit=80.0, evidence_quality=70.0)
        bare_id, _ = _seed_company(db, uid, "Bare Co", "ba@ba.example",
                                   website=None, country=None)
    finally:
        db.close()

    body = client.get(f"/api/companies/{cid}", headers=_auth(t)).json()
    score = body["lead_score"]
    assert score is not None
    assert score["engine"] == "deterministic_v1"
    assert score["ai_generated"] is False
    assert abs(sum(f["points"] for f in score["factors"]) - score["score"]) < 1e-9
    for f in score["factors"]:
        assert {"points", "max", "reason"} <= set(f)
        assert 0 <= f["points"] <= f["max"]
    assert body["has_research"] is True
    assert f"sum to exactly {score['score']}" in body["lead_score_note"]

    bare = client.get(f"/api/companies/{bare_id}",
                      headers=_auth(t)).json()
    assert bare["has_research"] is False
    assert bare["lead_score"]["band"] == "Low"
    # honest reason for the zero, not a silent 0
    assert "No persisted research evaluation" in \
        bare["lead_score"]["factors"][0]["reason"]


# ---------------------------------------------------------------------------
# leads: suppression filter runs BEFORE pagination
# ---------------------------------------------------------------------------

def test_leads_suppressed_filter_total_is_correct():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        _, lead_ok = _seed_company(db, uid, "Ok Co", "ok@ok.example")
        _, lead_ok2 = _seed_company(db, uid, "Ok2 Co", "ok2@ok.example")
        _, lead_sup = _seed_company(db, uid, "Sup Co", "sup@sup.example")
        db.add(Suppression(user_id=uid, value="sup@sup.example",
                           reason="unsubscribe", source="test"))
        db.commit()
    finally:
        db.close()

    # total must reflect the FILTER, not the raw page contents
    sup = client.get("/api/leads?suppressed=true&page_size=2",
                     headers=_auth(t)).json()
    assert sup["total"] == 1
    assert len(sup["items"]) == 1
    assert sup["items"][0]["suppressed"] is True
    assert sup["items"][0]["id"] == lead_sup

    # page 2 must still be consistent with total (no phantom rows)
    sup_p2 = client.get("/api/leads?suppressed=true&page=2&page_size=2",
                        headers=_auth(t)).json()
    assert sup_p2["total"] == 1
    assert sup_p2["items"] == []

    # default view lists everything, flagging suppression per row
    allrows = client.get("/api/leads?page_size=100",
                         headers=_auth(t)).json()
    assert allrows["total"] == 3
    flags = {i["id"]: i["suppressed"] for i in allrows["items"]}
    assert flags[lead_sup] is True
    assert flags[lead_ok] is False and flags[lead_ok2] is False

    # empty suppression list -> filter yields empty, total 0 (not unfiltered)
    t2 = _user()
    empty = client.get("/api/leads?suppressed=true", headers=_auth(t2)).json()
    assert empty["total"] == 0 and empty["items"] == []


# ---------------------------------------------------------------------------
# campaign send-time guards (never re-mail)
# ---------------------------------------------------------------------------

def test_send_time_suppression_guard_stops_mail():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid, "Guard Co",
                                   "guard@guard.example")
    finally:
        db.close()

    r = client.post("/api/campaigns",
                    json={"name": "G1", "subject": "Hi", "body": "B",
                          "lead_ids": [lead_id]},
                    headers=_auth(t))
    assert r.status_code == 201
    assert r.json()["added"] == 1
    cid = r.json()["id"]

    # unsubscribe lands AFTER the recipient list was built
    db = SessionLocal()
    try:
        db.add(Suppression(user_id=uid, value="guard@guard.example",
                           reason="unsubscribe", source="test"))
        db.commit()
    finally:
        db.close()

    s = client.post(f"/api/campaigns/{cid}/send",
                    json={"confirm": True, "recipient_count_ack": 1},
                    headers=_auth(t)).json()
    assert s["sent"] == 0
    assert s["skipped"] == 1
    assert s["skip_reasons"] == {"suppressed": 1}

    # no message row was written for the skipped recipient
    evts = client.get("/api/email-events", headers=_auth(t)).json()
    assert [e for e in evts if not e["is_test"]] == []

    # recipient is marked with an honest reason
    pv = client.get(f"/api/campaigns/{cid}/preview", headers=_auth(t)).json()
    assert pv["recipients"][0]["status"] == "SUPPRESSED"
    assert "suppressed at send time" in pv["recipients"][0]["error"]

    # campaign never claims SENT when nothing went out
    camps = client.get("/api/campaigns", headers=_auth(t)).json()
    assert next(c for c in camps if c["id"] == cid)["status"] == "DRAFT"

    # nothing pending -> 409, never a fake success
    again = client.post(f"/api/campaigns/{cid}/send",
                        json={"confirm": True, "recipient_count_ack": 0},
                        headers=_auth(t))
    assert again.status_code == 409
    assert "No pending recipients" in again.json()["detail"]


def test_send_time_prior_contact_guard_stops_remail():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid, "Once Co",
                                   "once@once.example")
    finally:
        db.close()

    r = client.post("/api/campaigns",
                    json={"name": "P1", "subject": "Hi", "body": "B",
                          "lead_ids": [lead_id]},
                    headers=_auth(t))
    cid = r.json()["id"]
    assert r.json()["added"] == 1   # not contacted yet at build time

    # another campaign mails this address in between build and send
    db = SessionLocal()
    try:
        _mk_message(db, uid, lead_id, "once@once.example",
                    status="accepted")
    finally:
        db.close()

    s = client.post(f"/api/campaigns/{cid}/send",
                    json={"confirm": True, "recipient_count_ack": 1},
                    headers=_auth(t)).json()
    assert s["sent"] == 0
    assert s["skipped"] == 1
    assert s["skip_reasons"] == {"already_contacted": 1}
    # only the pre-existing message exists — nothing new was written
    evts = client.get("/api/email-events", headers=_auth(t)).json()
    assert len([e for e in evts if not e["is_test"]]) == 1


def test_send_without_pending_recipients_is_409():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid, "Done Co",
                                   "done@done.example")
    finally:
        db.close()
    r = client.post("/api/campaigns",
                    json={"name": "D1", "subject": "Hi", "body": "B",
                          "lead_ids": [lead_id]},
                    headers=_auth(t))
    cid = r.json()["id"]
    assert r.json()["added"] == 1

    # first campaign sends normally
    s = client.post(f"/api/campaigns/{cid}/send",
                    json={"confirm": True, "recipient_count_ack": 1},
                    headers=_auth(t)).json()
    assert s["sent"] == 1

    # sending it again: status guard (already SENT) — still a 4xx, no resend
    again = client.post(f"/api/campaigns/{cid}/send",
                        json={"confirm": True, "recipient_count_ack": 1},
                        headers=_auth(t))
    assert again.status_code == 409

    # second campaign for the now-contacted lead: build adds nobody
    r2 = client.post("/api/campaigns",
                     json={"name": "D2", "subject": "Hi", "body": "B",
                           "lead_ids": [lead_id]},
                     headers=_auth(t))
    assert r2.json()["added"] == 0
    assert r2.json()["skipped_contacted"] == 1

    # empty campaign: pending == 0 -> honest 409, never a fake success
    empty_send = client.post(f"/api/campaigns/{r2.json()['id']}/send",
                             json={"confirm": True, "recipient_count_ack": 0},
                             headers=_auth(t))
    assert empty_send.status_code == 409
    assert "No pending recipients" in empty_send.json()["detail"]


def test_preview_reports_exact_sendable_counts():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        _, lead_id = _seed_company(db, uid, "Prev Co",
                                   "prev@prev.example")
    finally:
        db.close()
    r = client.post("/api/campaigns",
                    json={"name": "PV1", "subject": "Hi", "body": "B",
                          "lead_ids": [lead_id]},
                    headers=_auth(t))
    cid = r.json()["id"]

    pv = client.get(f"/api/campaigns/{cid}/preview", headers=_auth(t)).json()
    assert pv["pending_count"] == 1
    assert pv["sendable_count"] == 1
    assert pv["blocked_count"] == 0
    row = pv["recipients"][0]
    assert row["previously_contacted"] is False
    assert row["suppressed"] is False
    assert row["lead_score"] is not None and row["band"] is not None
    assert "re-mailed" in pv["send_note"]

    # suppression arrives -> preview shows it before anything irreversible
    db = SessionLocal()
    try:
        db.add(Suppression(user_id=uid, value="prev@prev.example",
                           reason="unsubscribe", source="test"))
        db.commit()
    finally:
        db.close()
    pv2 = client.get(f"/api/campaigns/{cid}/preview",
                     headers=_auth(t)).json()
    assert pv2["recipients"][0]["suppressed"] is True
    assert pv2["sendable_count"] == 0
    assert pv2["blocked_count"] == 1


# ---------------------------------------------------------------------------
# audience filters (persisted research only)
# ---------------------------------------------------------------------------

def test_audience_filter_require_research():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        res_id, res_lead = _seed_company(db, uid, "Res Co",
                                         "r@r.example")
        _mk_evidence(db, uid, res_id)
        _, plain_lead = _seed_company(db, uid, "NoRes Co",
                                      "nr@nr.example")
    finally:
        db.close()

    r = client.post("/api/campaigns",
                    json={"name": "AR1", "subject": "Hi", "body": "B",
                          "lead_ids": [res_lead, plain_lead],
                          "require_research": True},
                    headers=_auth(t))
    assert r.status_code == 201
    info = r.json()
    assert info["added"] == 1
    assert info["skipped_unresearched"] == 1
    assert info["skipped_noemail"] == 0

    pv = client.get(f"/api/campaigns/{info['id']}/preview",
                    headers=_auth(t)).json()
    assert pv["recipient_count"] == 1
    assert pv["recipients"][0]["company"] == "Res Co"


def test_audience_filter_min_buyer_fit():
    t = _user()
    uid = _uid_of(t)
    db = SessionLocal()
    try:
        good_id, good_lead = _seed_company(db, uid, "Good Fit",
                                           "gf@gf.example")
        _mk_evaluation(db, uid, good_id, buyer_fit=80.0)
        weak_id, weak_lead = _seed_company(db, uid, "Weak Fit",
                                           "wf@wf.example")
        _mk_evaluation(db, uid, weak_id, buyer_fit=30.0)
        _, noeval_lead = _seed_company(db, uid, "No Eval",
                                       "ne@ne.example")
    finally:
        db.close()

    r = client.post("/api/campaigns",
                    json={"name": "AF1", "subject": "Hi", "body": "B",
                          "lead_ids": [good_lead, weak_lead, noeval_lead],
                          "min_buyer_fit": 50},
                    headers=_auth(t))
    assert r.status_code == 201
    info = r.json()
    assert info["added"] == 1
    assert info["skipped_low_fit"] == 1          # 30 < 50
    assert info["skipped_unresearched"] == 1     # no evaluation = unproven

    pv = client.get(f"/api/campaigns/{info['id']}/preview",
                    headers=_auth(t)).json()
    assert [row["company"] for row in pv["recipients"]] == ["Good Fit"]


def test_audience_filter_validation_is_typed():
    t = _user()
    r = client.post("/api/campaigns",
                    json={"name": "V1", "subject": "Hi", "body": "B",
                          "min_buyer_fit": 101},
                    headers=_auth(t))
    assert r.status_code == 422


def test_audience_filters_are_tenant_scoped():
    a = _user()
    b = _user()
    uid_a = _uid_of(a)
    uid_b = _uid_of(b)
    db = SessionLocal()
    try:
        # tenant A's company HAS research
        cid_a, _ = _seed_company(db, uid_a, "Foreign Co", "f@f.example")
        _mk_evidence(db, uid_a, cid_a)
        # tenant B's company does NOT — and B must not inherit A's rows
        _, lead_b = _seed_company(db, uid_b, "Domestic Co",
                                  "d@d.example")
    finally:
        db.close()

    r = client.post("/api/campaigns",
                    json={"name": "T1", "subject": "Hi", "body": "B",
                          "lead_ids": [lead_b], "require_research": True},
                    headers=_auth(b))
    assert r.status_code == 201
    info = r.json()
    # B's own lead has no research -> excluded; A's research never counted
    assert info["added"] == 0
    assert info["skipped_unresearched"] == 1

    # A's identical campaign works off A's research
    r_a = client.post("/api/campaigns",
                      json={"name": "T2", "subject": "Hi", "body": "B",
                            "lead_ids": [], "require_research": True},
                      headers=_auth(a))
    assert r_a.status_code == 201
    assert r_a.json()["added"] == 1
    assert r_a.json()["skipped_unresearched"] == 0
