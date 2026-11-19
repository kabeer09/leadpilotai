"""Lock-recovery regressions (jobs 27/29: 'database is locked' killed jobs).

Proven failure chain: a flush that exceeds busy_timeout — or a stale WAL
read snapshot (busy_timeout does NOT apply to snapshot upgrades) — makes
SQLAlchemy roll the transaction back; every later operation then raises
PendingRollbackError. db_commit_retry can only re-raise it, so the whole
job died. These tests pin the three recovery paths:

1. db_write_retry: rollback + re-stage recovers a REALLY poisoned session.
2. run_job: a lock at the CRAWLING transition must not kill the job.
3. ingest: a lock mid-record rolls back and re-stages THAT record only
   (job 27 died inside _enrich_from_official_site during ingest).
"""
import os
import sqlite3
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import User, Source, Company, Contact, Lead, CrawlJob, CrawlEvent
from app.crawler import db_write_retry


def _owner():
    db = SessionLocal()
    u = db.query(User).filter(User.email == "lockrec@test.example").first()
    if not u:
        from app.security import hash_password
        u = User(email="lockrec@test.example",
                 password_hash=hash_password("TestPass123"))
        db.add(u)
        db.commit()
        db.refresh(u)
    uid = u.id
    src = db.query(Source).filter(Source.user_id == uid).first()
    if not src:
        src = Source(user_id=uid, url="https://lockrec.example", name="s",
                     status="COMPLETED")
        db.add(src)
        db.commit()
        db.refresh(src)
    out = (uid, src.id)
    db.close()
    return out


def _sqlite_path() -> str:
    db_file = engine.url.database
    if not db_file or not str(db_file).endswith(".db"):
        pytest.skip("sqlite file database required")
    if not os.path.isabs(db_file):
        # DATABASE_URL is cwd-relative (./trial.db); resolve against the
        # app package so the test works from any working directory.
        from app import database as dbmod
        db_file = os.path.join(os.path.dirname(dbmod.__file__), "..", db_file)
    return os.path.abspath(db_file)


def test_write_retry_recovers_really_poisoned_session():
    """db_write_retry: rollback + re-stage recovers a session poisoned by
    a genuinely failed flush (write lock held elsewhere, busy_timeout=0)."""
    path = _sqlite_path()
    db = SessionLocal()
    jid = None
    lock_conn = None
    try:
        job = CrawlJob(user_id=1, source_id=None,
                       url="https://poison.example/", max_pages=1,
                       max_depth=0, status="PENDING")
        db.add(job)
        db.commit()
        jid = job.id
        # A second connection holds the write lock (WAL: one writer at a
        # time) — acquired AFTER setup so setup's own commit isn't blocked.
        lock_conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        lock_conn.execute("BEGIN IMMEDIATE")
        # Fail the flush immediately instead of waiting out busy_timeout.
        db.execute(text("PRAGMA busy_timeout=0"))
        db.add(CrawlEvent(user_id=1, job_id=jid, url="https://x.example/",
                          status="ok", detail="will-be-rolled-back"))
        with pytest.raises(Exception) as ei:
            db.flush()
        assert "locked" in str(ei.value).lower()
        # The failed flush poisoned the session: every later op raises.
        with pytest.raises(PendingRollbackError):
            db.query(CrawlEvent).filter(CrawlEvent.job_id == jid).count()

        lock_conn.rollback()
        lock_conn.close()

        # Recovery: rollback (expunges the pending event) + re-stage.
        def stage():
            db.add(CrawlEvent(user_id=1, job_id=jid,
                              url="https://x.example/", status="ok",
                              detail="poison-recovery"))

        db_write_retry(db, stage)
        rows = db.query(CrawlEvent).filter(CrawlEvent.job_id == jid).all()
        assert len(rows) == 1, "exactly one event — no duplicate from retry"
        assert rows[0].detail == "poison-recovery"
    finally:
        try:
            db.rollback()
            db.execute(text("PRAGMA busy_timeout=30000"))
        except Exception:
            pass
        if jid is not None:
            try:
                db.query(CrawlEvent).filter(CrawlEvent.job_id == jid).delete()
                db.query(CrawlJob).filter(CrawlJob.id == jid).delete()
                db.commit()
            except Exception:
                db.rollback()
        db.close()
        try:
            lock_conn.rollback()
        except Exception:
            pass
        try:
            lock_conn.close()
        except Exception:
            pass


def test_run_job_survives_commit_lock_at_transition(monkeypatch):
    """A lock storm at the CRAWLING transition must not kill the job:
    the status write is rolled back and re-staged, not lost (without
    recovery the job ends FAILED with started_at never set)."""
    import app.crawler as craw
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

    # First 5 commits fail with lock contention (unfixed db_commit_retry
    # exhausts its 5 tries on the transition and dies; db_write_retry
    # recovers via rollback + re-stage on the 6th).
    calls = {"n": 0}
    real_commit = Session.commit

    def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] <= 5:
            raise Exception("database is locked")
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", flaky_commit)
    craw.run_job(jid)
    monkeypatch.setattr(Session, "commit", real_commit)

    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        assert job.status == "COMPLETED"
        assert job.started_at is not None, (
            "CRAWLING transition must be re-staged after rollback")
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


def test_ingest_recovers_lock_error_per_record(monkeypatch):
    """Job-27 pattern: a lock error mid-record (inside enrichment) rolls
    back and re-stages THAT record — the job continues, and counters are
    not double-counted for the retried record."""
    import app.crawler as craw
    uid, sid = _owner()
    db = SessionLocal()
    try:
        job = CrawlJob(user_id=uid, source_id=sid,
                       url="https://ingestrec.example/", max_pages=100,
                       max_depth=0, status="CRAWLING")
        db.add(job)
        db.commit()
        jid = job.id
    finally:
        db.close()

    recs = [{
        "title": f"Rec Co {i}", "profile_name": f"Rec Co {i}",
        "description": "Widgets.", "emails": [], "phones": [],
        "official_website": f"https://site{i}.example",
        "profile_url": f"https://ingestrec.example/exhibitor/{i}",
        "official_links": [], "links": [], "text_len": 5000,
    } for i in (1, 2)]

    calls = {"n": 0}

    def flaky_enrich(db_, uid_, comp, site, client, render_budget,
                     job_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("(sqlite3.OperationalError) database is locked")
        return 0, 0

    monkeypatch.setattr(craw, "_enrich_from_official_site", flaky_enrich)
    shared = {"lock": threading.Lock(), "cancel": threading.Event(),
              "pages": 0, "companies": 0, "contacts": 0,
              "render_budget": [10]}
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        n = craw._ingest_adapter_records(db, job, recs, shared)
        assert n == 2, "both records saved exactly once despite the lock"
        # record 1: enrich raised (lock) then retried OK; record 2: once.
        assert calls["n"] == 3, "first record retried, second ran once"
        assert shared["companies"] == 2, "no double-count from the retry"
        rows = db.query(Company).filter(
            Company.profile_url.in_([r["profile_url"] for r in recs])).all()
        assert len(rows) == 2, "one row per profile URL (no duplicates)"
    finally:
        try:
            db.query(Company).filter(
                Company.profile_url.in_(
                    [r["profile_url"] for r in recs])).delete(
                        synchronize_session=False)
            db.query(CrawlEvent).filter(CrawlEvent.job_id == jid).delete()
            db.query(CrawlJob).filter(CrawlJob.id == jid).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()


def test_ingest_skips_record_when_lock_outlasts_retries(monkeypatch):
    """Job 30 pattern: lock contention persisting through ALL retry
    attempts must skip that ONE record with an honest error — never fail
    the whole job (job 30 died at an [S3] event commit inside
    _enrich_from_official_site while a parallel job held the lock)."""
    import app.crawler as craw
    uid, sid = _owner()
    db = SessionLocal()
    try:
        job = CrawlJob(user_id=uid, source_id=sid,
                       url="https://lockskip.example/", max_pages=100,
                       max_depth=0, status="CRAWLING")
        db.add(job)
        db.commit()
        jid = job.id
    finally:
        db.close()

    recs = [{
        "title": f"Skip Co {i}", "profile_name": f"Skip Co {i}",
        "description": "Widgets.", "emails": [], "phones": [],
        "official_website": f"https://site{i}.lockskip.example",
        "profile_url": f"https://lockskip.example/exhibitor/{i}",
        "official_links": [], "links": [], "text_len": 5000,
    } for i in (1, 2)]

    calls = {"n": 0}

    def locked_for_site1(db_, uid_, comp, site, client, render_budget,
                         job_id=None):
        calls["n"] += 1
        if "site1" in site:
            raise Exception("(sqlite3.OperationalError) database is locked")
        return 0, 0

    monkeypatch.setattr(craw, "_enrich_from_official_site",
                        locked_for_site1)
    shared = {"lock": threading.Lock(), "cancel": threading.Event(),
              "pages": 0, "companies": 0, "contacts": 0,
              "render_budget": [10], "errors": []}
    db = SessionLocal()
    try:
        job = db.query(CrawlJob).filter(CrawlJob.id == jid).first()
        # Must NOT raise despite record 1 hitting the lock 3 times.
        n = craw._ingest_adapter_records(db, job, recs, shared)
        assert n == 1, "record 2 saved; record 1 skipped after retries"
        assert calls["n"] == 4, "record 1: 3 attempts, record 2: once"
        assert len(shared["errors"]) == 1
        assert recs[0]["profile_url"] in shared["errors"][0]
        # record 1's row was committed BEFORE enrichment — it is KEPT.
        rows = db.query(Company).filter(
            Company.profile_url.in_([r["profile_url"] for r in recs])).all()
        assert len(rows) == 2, "skipped record's saved row must be kept"
    finally:
        try:
            db.query(Company).filter(
                Company.profile_url.in_(
                    [r["profile_url"] for r in recs])).delete(
                        synchronize_session=False)
            db.query(CrawlEvent).filter(CrawlEvent.job_id == jid).delete()
            db.query(CrawlJob).filter(CrawlJob.id == jid).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()
