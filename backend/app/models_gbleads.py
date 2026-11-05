"""Google Business Lead Finder models (separate module, additive tables).

Design notes:
- Every tenant row carries user_id; (user_id, ...) indexes match the rest
  of the app for tenant-scoped lookups.
- SearchCampaign IS the search job: status PENDING|RUNNING|PAUSED|
  COMPLETED|FAILED|CANCELLED with progress counters (spec section 10).
  Enrichment batches run as their own GBJob rows (retryable).
- LeadEvidence stores the exact source URL + method for every extracted
  field — nothing is claimed without a source.
- Dedup keys: place_id (unique per user), website_domain (normalized),
  plus a company_name+city+country fallback computed at write time.
"""
from datetime import datetime
from sqlalchemy import (Column, Integer, String, Text, Boolean, DateTime,
                        ForeignKey, Float, UniqueConstraint, Index)
from app.database import Base


class SearchCampaign(Base):
    __tablename__ = "gb_search_campaigns"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_name = Column(String(255), nullable=False)
    # JSON lists (stored as Text; parsed at read time like products_json)
    keywords = Column(Text, nullable=False, default="[]")        # ["copper bottles"]
    categories = Column(Text, nullable=True, default="[]")       # product categories
    business_types = Column(Text, nullable=True, default="[]")   # manufacturer|wholesaler...
    countries = Column(Text, nullable=True, default="[]")        # ["Australia"]
    state = Column(String(100), nullable=True)
    cities = Column(Text, nullable=True, default="[]")           # ["Sydney","Melbourne"]
    max_results = Column(Integer, default=20, nullable=False)
    filters_json = Column(Text, nullable=True)  # {has_website, has_phone, min_rating, min_reviews,...}
    # PENDING|RUNNING|PAUSED|COMPLETED|FAILED|CANCELLED
    status = Column(String(20), default="PENDING", nullable=False, index=True)
    # progress counters
    total_discovered = Column(Integer, default=0, nullable=False)
    total_enriched = Column(Integer, default=0, nullable=False)
    total_skipped = Column(Integer, default=0, nullable=False)      # filtered out
    duplicate_count = Column(Integer, default=0, nullable=False)
    api_calls = Column(Integer, default=0, nullable=False)
    api_errors = Column(Integer, default=0, nullable=False)
    website_errors = Column(Integer, default=0, nullable=False)
    estimated_cost_usd = Column(Float, default=0.0, nullable=False)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_gb_campaign_user_status", "user_id", "status"),
    )


class BusinessLead(Base):
    __tablename__ = "gb_leads"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("gb_search_campaigns.id"),
                         nullable=True, index=True)
    # --- Google Places fields ---
    place_id = Column(String(128), nullable=True)      # Google place id (dedup key 1)
    source_provider = Column(String(50), default="google_places", nullable=False)
    company_name = Column(String(500), nullable=False, index=True)
    business_category = Column(String(255), nullable=True, index=True)  # primary type, humanized
    primary_type = Column(String(100), nullable=True)
    types_json = Column(Text, nullable=True)           # JSON list of Google types
    business_description = Column(Text, nullable=True) # Google editorial/website meta
    website = Column(String(2048), nullable=True)
    website_domain = Column(String(255), nullable=True, index=True)  # dedup key 2
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)         # published on the official site or OSM record (evidence rows document the source)
    country = Column(String(100), nullable=True, index=True)
    state = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True, index=True)
    address = Column(Text, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    google_maps_url = Column(String(2048), nullable=True)
    rating = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=True)
    # --- scoring / classification ---
    relevance_score = Column(Integer, default=0, nullable=False, index=True)
    relevance_label = Column(String(30), nullable=True)  # Highly Relevant|Relevant|Potential Match|Low Relevance
    match_reasons = Column(Text, nullable=True)          # JSON list of evidence strings
    # Manufacturer|Importer|Wholesaler|Retailer|Distributor|Exporter|Service Provider|Unknown
    business_type = Column(String(30), default="Unknown", nullable=False, index=True)
    business_type_evidence = Column(Text, nullable=True)  # JSON list of evidence strings
    # --- lifecycle ---
    enrichment_status = Column(String(20), default="NOT_ENRICHED", nullable=False, index=True)
    # NOT_ENRICHED|ENRICHED|NO_WEBSITE|FAILED
    lead_status = Column(String(30), default="NEW", nullable=False, index=True)
    # NEW|SAVED|CONTACTED|ARCHIVED
    enriched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        # dedup key 1: same place per tenant
        UniqueConstraint("user_id", "place_id", name="uq_gb_lead_place"),
        Index("ix_gb_lead_user_score", "user_id", "relevance_score"),
        Index("ix_gb_lead_user_campaign", "user_id", "campaign_id"),
        Index("ix_gb_lead_user_domain", "user_id", "website_domain"),
    )


class LeadEvidence(Base):
    """One extracted fact with its exact source (spec: every field sourced)."""
    __tablename__ = "gb_lead_evidence"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("gb_leads.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    field_name = Column(String(50), nullable=False)    # email|phone|whatsapp|description|products|social_link|business_type...
    field_value = Column(String(1000), nullable=False)
    source_url = Column(String(2048), nullable=False)  # exact page the value came from
    extraction_method = Column(String(50), nullable=True)  # mailto_link|text_regex|meta_tag|anchor_text...
    confidence = Column(Float, nullable=True)          # 0.0-1.0 (method-based, not a claim of verification)
    extracted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_gb_evidence_lead_field", "lead_id", "field_name"),
    )


class GBJob(Base):
    """Background job service row (currently: website enrichment batches).

    States: PENDING|RUNNING|PAUSED|COMPLETED|FAILED|CANCELLED.
    Search runs live on SearchCampaign (1:1 job); enrichment batches are
    separate retryable rows here.
    """
    __tablename__ = "gb_jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(20), default="enrich", nullable=False)  # enrich
    campaign_id = Column(Integer, ForeignKey("gb_search_campaigns.id"),
                         nullable=True, index=True)
    lead_ids_json = Column(Text, nullable=True)  # explicit lead set (else campaign leads)
    status = Column(String(20), default="PENDING", nullable=False, index=True)
    # PENDING|RUNNING|PAUSED|COMPLETED|FAILED|CANCELLED
    total = Column(Integer, default=0, nullable=False)
    processed = Column(Integer, default=0, nullable=False)
    enriched = Column(Integer, default=0, nullable=False)
    skipped = Column(Integer, default=0, nullable=False)
    errors = Column(Integer, default=0, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_gb_job_user_status", "user_id", "status"),
    )
