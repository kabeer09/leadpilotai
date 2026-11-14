"""Pydantic request/response schemas with validation."""
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator


# ---- Auth ----
class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=255)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    is_admin: bool
    email_verified: bool
    whatsapp_number: str | None = None
    whatsapp_verified: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    whatsapp_number: str | None = Field(default=None, max_length=20)

class OTPRequest(BaseModel):
    email: EmailStr

class OTPVerify(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=4, max_length=8)


# ---- Crawls ----
class CrawlCreate(BaseModel):
    url: str = Field(max_length=2048)
    name: str | None = Field(default=None, max_length=500)
    max_pages: int = Field(default=100, ge=1, le=10000)
    max_depth: int = Field(default=2, ge=0, le=3)
    force_recrawl: bool = False
    force_recrawl: bool = False

    @field_validator("url")
    @classmethod
    def must_be_http(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


# ---- Campaigns ----
class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)
    # Empty = ALL leads with email. Non-empty = only those IDs.
    lead_ids: list[int] = Field(default_factory=list, max_length=5000)
    batch_limit: int | None = Field(default=None, ge=1, le=5000)
    allow_resend: bool = False
    followup_enabled: bool = False
    followup_delay_hours: int = Field(default=24, ge=1, le=168)
    # Audience filters (persisted research only — never guessed):
    # keep only companies with saved research evidence / an evaluation run.
    require_research: bool = False
    # Keep only companies whose latest persisted buyer-fit score >= this
    # (0-100). No evaluation = fit unproven = excluded, counted honestly.
    min_buyer_fit: int | None = Field(default=None, ge=0, le=100)


class CampaignSend(BaseModel):
    confirm: bool = False
    recipient_count_ack: int = 0  # second confirmation: must equal preview count


class TestSend(BaseModel):
    to_email: EmailStr | None = None  # default: account owner's address


# ---- Google Business Lead Finder ----
class GBCampaignCreate(BaseModel):
    campaign_name: str = Field(min_length=1, max_length=255)
    keywords: list[str] = Field(min_length=1, max_length=20)  # product keywords
    categories: list[str] = Field(default_factory=list, max_length=10)
    business_types: list[str] = Field(default_factory=list, max_length=10)
    countries: list[str] = Field(default_factory=list, max_length=10)
    state: str | None = Field(default=None, max_length=100)
    cities: list[str] = Field(default_factory=list, max_length=20)
    max_results: int = Field(default=20, ge=1, le=60)
    # optional single-location quick search (campaigns allow many cities)
    filters: dict = Field(default_factory=dict)
    enrich: bool = False  # enqueue website enrichment after discovery
    start: bool = True    # False = save as PAUSED draft

    @field_validator("keywords")
    @classmethod
    def non_empty_keywords(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip() for k in v if k and k.strip()]
        if not cleaned:
            raise ValueError("At least one non-empty keyword is required")
        return cleaned[:20]


class GBLeadUpdate(BaseModel):
    lead_status: str | None = Field(default=None, max_length=30)
    # NEW|SAVED|CONTACTED|ARCHIVED


class GBEnrichRequest(BaseModel):
    lead_ids: list[int] = Field(default_factory=list, max_length=1000)
    campaign_id: int | None = None


class GBExportQuery(BaseModel):
    campaign_id: int | None = None
    format: str = "csv"  # csv | xlsx
    only_with_email: bool = False
    only_with_phone: bool = False
    only_with_website: bool = False
    min_relevance: int = Field(default=0, ge=0, le=100)
