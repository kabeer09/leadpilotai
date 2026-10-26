"""Research evidence and job tracking models."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from app.database import Base


class DiscoveryJob(Base):
    __tablename__ = "discovery_jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_id = Column(String(50), nullable=True, index=True)  # fair source id
    status = Column(String(20), default="queued", nullable=False, index=True)
    # queued|running|completed|partial|failed|cancelled
    max_pages = Column(Integer, default=50, nullable=False)
    pages_processed = Column(Integer, default=0, nullable=False)
    companies_found = Column(Integer, default=0, nullable=False)
    contacts_found = Column(Integer, default=0, nullable=False)
    errors = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ContactEvidence(Base):
    __tablename__ = "contact_evidence"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True, index=True)
    contact_type = Column(String(20), nullable=False)  # email|phone|whatsapp
    raw_value = Column(String(500), nullable=False)
    normalized_value = Column(String(500), nullable=True)
    source_url = Column(String(2048), nullable=False)
    source_type = Column(String(50), nullable=True)  # fair_profile|company_website|manual
    page_title = Column(String(500), nullable=True)
    extraction_method = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)
    validation_status = Column(String(30), default="DISCOVERED", nullable=False)
    validation_method = Column(String(50), nullable=True)
    first_observed = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_observed = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text, nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("discovery_jobs.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    agent_name = Column(String(50), nullable=False, index=True)
    status = Column(String(20), default="queued", nullable=False)  # queued|running|completed|failed|cancelled
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    result_summary = Column(Text, nullable=True)  # JSON
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    evaluation_version = Column(String(20), default="1.0", nullable=False)
    contact_completeness_score = Column(Float, nullable=True)  # 0-100
    evidence_quality_score = Column(Float, nullable=True)  # 0-100
    buyer_fit_score = Column(Float, nullable=True)  # 0-100
    validation_coverage = Column(Float, nullable=True)  # 0-100
    overall_score = Column(Float, nullable=True)  # 0-100
    issues = Column(Text, nullable=True)  # JSON list
    warnings = Column(Text, nullable=True)  # JSON list
    evaluated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
