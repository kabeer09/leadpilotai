"""Global Trade Intelligence models (additive tables — spec section 17).

Design notes (mirrors models_gbleads.py):
- saved_searches is tenant-scoped (user_id on every row + indexes).
- trade_statistics is a GLOBAL cache keyed by a hash of the query (no
  user_id): the same UN Comtrade query from any user is the same public
  statistic, and the cache lets us serve a stale-but-honest result when
  the provider rate-limits us (quota errors are never "0 results").
- data_providers is a registry row per external source: endpoint,
  categories, coverage, limits, last success/error. API keys NEVER live
  here — config only, server-side.
- No table stores shipment-level records or buyer identities: UN
  Comtrade preview returns aggregated annual statistics only, and every
  API response carries record_type + source attribution.
"""
from datetime import datetime

from sqlalchemy import (Column, Integer, String, Text, Boolean, DateTime,
                        Float, ForeignKey, UniqueConstraint, Index)

from app.database import Base


class SavedSearch(Base):
    """A user's saved trade query (spec: save search configurations)."""
    __tablename__ = "saved_searches"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    # trade | statistics
    kind = Column(String(30), default="trade", nullable=False, index=True)
    # JSON of the query params: {reporter_code, period_from, period_to,
    # flow, cmd_code, partner_code, max_records}
    query_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False,
                        index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "kind", "name",
                         name="uq_saved_search_name"),
        Index("ix_saved_search_user_kind", "user_id", "kind"),
    )


class TradeStatistic(Base):
    """Cached aggregated trade-statistics payload for one canonical query.

    Global (no user_id): keys are a sha1 of the normalized provider
    params, so every tenant shares the same public UN Comtrade data.
    `fetched_at` drives the TTL; expired rows stay put so a provider
    quota error can still serve them flagged as stale.
    """
    __tablename__ = "trade_statistics"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(64), unique=True, nullable=False, index=True)
    provider = Column(String(50), default="un_comtrade", nullable=False)
    # JSON of normalized params the payload was fetched with
    params_json = Column(Text, nullable=False, default="{}")
    # JSON envelope: {record_type, source, rows, count, ...}
    payload_json = Column(Text, nullable=False, default="{}")
    row_count = Column(Integer, default=0, nullable=False)
    # upstream HTTP status of the last fetch attempt (429 = quota)
    last_status = Column(Integer, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False,
                        index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_trade_stat_provider_fetched", "provider", "fetched_at"),
    )


class DataProvider(Base):
    """Data Source Management registry row (spec section 16).

    Status values: active | configured | not_configured | error.
    `auth_configured` is a boolean only — the credential itself stays in
    environment config and is never loaded into this table.
    """
    __tablename__ = "data_providers"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    endpoint = Column(String(2048), nullable=True)
    auth_type = Column(String(50), default="none", nullable=False)  # none | api_key
    auth_configured = Column(Boolean, default=False, nullable=False)
    # JSON lists: categories, country coverage, available fields
    data_categories = Column(Text, nullable=True, default="[]")
    country_coverage = Column(Text, nullable=True, default="[]")
    available_fields = Column(Text, nullable=True, default="[]")
    usage_limits = Column(Text, nullable=True)
    # active | configured | not_configured | error
    status = Column(String(30), default="not_configured", nullable=False,
                    index=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_data_provider_status", "status", "is_enabled"),
    )
