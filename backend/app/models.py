"""All tables. Every tenant row carries user_id; indexes on (user_id, ...) lookups."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    # Google account link (Sign in with Google). Tokens stay server-side.
    google_sub = Column(String(255), nullable=True, index=True)
    gmail_refresh_token = Column(Text, nullable=True)
    name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    email_verified = Column(Boolean, default=False, nullable=False)
    whatsapp_number = Column(String(20), nullable=True)
    whatsapp_verified = Column(Boolean, default=False, nullable=False)
    otp_code = Column(String(10), nullable=True)
    otp_expires = Column(DateTime, nullable=True)
    verification_token = Column(String(255), nullable=True, index=True)
    reset_token = Column(String(255), nullable=True, index=True)
    reset_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GoogleOAuthConfig(Base):
    """App-level Google OAuth client, pasted by the owner at signup time.

    Single row (id=1). DB values override env vars so no server restart or
    .env editing is ever needed. Secrets never leave the server.
    """
    __tablename__ = "google_oauth_config"
    id = Column(Integer, primary_key=True)
    client_id = Column(Text, nullable=False)
    client_secret = Column(Text, nullable=False)
    redirect_uri = Column(String(1024), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)


class OAuthState(Base):
    """CSRF states + one-time login codes for the Google flow (short-lived)."""
    __tablename__ = "oauth_states"
    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False, index=True)  # state | login_code
    value_hash = Column(String(255), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Source(Base):
    """A crawled website/directory (per user)."""
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    url = Column(String(2048), nullable=False)
    name = Column(String(500), nullable=True)
    status = Column(String(30), default="PENDING", nullable=False)  # PENDING|CRAWLING|COMPLETED|FAILED|CANCELLED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True, index=True)
    url = Column(String(2048), nullable=False)
    max_pages = Column(Integer, default=30, nullable=False)
    max_depth = Column(Integer, default=2, nullable=False)
    status = Column(String(30), default="PENDING", nullable=False)
    pages_visited = Column(Integer, default=0, nullable=False)
    companies_found = Column(Integer, default=0, nullable=False)
    contacts_found = Column(Integer, default=0, nullable=False)
    errors = Column(Text, nullable=True)  # JSON list of last errors
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CrawlEvent(Base):
    __tablename__ = "crawl_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("crawl_jobs.id"), nullable=False, index=True)
    url = Column(String(2048), nullable=False)
    status = Column(String(30), nullable=False)  # ok|skipped|error|blocked
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True, index=True)
    name = Column(String(500), nullable=False, index=True)
    product_summary = Column(Text, nullable=True)
    country = Column(String(100), nullable=True, index=True)
    city = Column(String(100), nullable=True)
    website = Column(String(2048), nullable=True)
    category = Column(String(255), nullable=True, index=True)
    profile_url = Column(String(2048), nullable=True)
    # Exhibitor-profile fields (old-pipeline parity, all nullable/additive)
    hall = Column(String(100), nullable=True)
    booth = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    products_json = Column(Text, nullable=True)  # JSON list [{name, category}]
    # lifecycle: active | EXCLUDED_NO_CONTACT | ARCHIVED | MERGED
    lifecycle = Column(String(30), default="active", nullable=False, index=True)
    merged_into_id = Column(Integer, ForeignKey("companies.id"), nullable=True)
    lead_status = Column(String(30), default="NEW", nullable=False)  # NEW|CONTACTED|REPLIED|QUALIFIED
    confidence = Column(Float, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_verified = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    contacts = relationship("Contact", back_populates="company", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="company", cascade="all, delete-orphan")


class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False, index=True)  # EMAIL|PHONE|WHATSAPP
    value = Column(String(500), nullable=False)
    normalized = Column(String(500), nullable=False, index=True)
    source_url = Column(String(2048), nullable=True)
    source_page = Column(String(50), nullable=True)  # home|contact|about|products|directory|other
    is_primary = Column(Boolean, default=False, nullable=False)
    # verification: scraped | verified | invalid
    verification = Column(String(20), default="scraped", nullable=False)
    # whatsapp_status: unknown | identified (explicitly marked by source)
    whatsapp_status = Column(String(20), default="unknown", nullable=False)
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_verified = Column(DateTime, nullable=True)

    company = relationship("Company", back_populates="contacts")


class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=True, index=True)
    status = Column(String(30), default="NEW", nullable=False, index=True)
    score = Column(Integer, default=0, nullable=False)
    notes = Column(Text, nullable=True)
    last_contacted_at = Column(DateTime, nullable=True)
    next_followup_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    company = relationship("Company", back_populates="leads")


class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String(30), default="DRAFT", nullable=False)  # DRAFT|SCHEDULED|SENDING|SENT|CANCELLED
    followup_enabled = Column(Boolean, default=False, nullable=False)
    followup_delay_hours = Column(Integer, default=24, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    recipients = relationship("CampaignRecipient", back_populates="campaign", cascade="all, delete-orphan")


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    email = Column(String(500), nullable=False)
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True)
    status = Column(String(30), default="PENDING", nullable=False)  # PENDING|SENT|FAILED|SUPPRESSED
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)

    campaign = relationship("Campaign", back_populates="recipients")


class EmailMessage(Base):
    __tablename__ = "email_messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    recipient_id = Column(Integer, ForeignKey("campaign_recipients.id"), nullable=True)
    to_email = Column(String(500), nullable=False)
    subject = Column(String(500), nullable=True)
    body = Column(Text, nullable=True)
    # queued|accepted|delivered|bounced|failed|replied|unknown (+test)
    status = Column(String(30), default="queued", nullable=False, index=True)
    provider = Column(String(30), nullable=False)
    provider_message_id = Column(String(255), nullable=True, index=True)
    thread_id = Column(String(255), nullable=True, index=True)
    is_test = Column(Boolean, default=False, nullable=False)
    is_followup = Column(Boolean, default=False, nullable=False)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProviderEvent(Base):
    __tablename__ = "provider_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("email_messages.id"), nullable=False, index=True)
    event_type = Column(String(30), nullable=False)  # sent|delivered|bounce|reply|unsubscribe|failed
    payload = Column(Text, nullable=True)  # JSON excerpt (no secrets)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Followup(Base):
    __tablename__ = "followups"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("email_messages.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    status = Column(String(30), default="SCHEDULED", nullable=False)  # SCHEDULED|SENT|SKIPPED|CANCELLED
    skip_reason = Column(String(255), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Suppression(Base):
    __tablename__ = "suppressions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    value = Column(String(500), nullable=False, index=True)  # normalized email
    reason = Column(String(50), nullable=False)  # unsubscribe|bounce|manual|abuse
    source = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)
    entity = Column(String(50), nullable=True)
    entity_id = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
