"""Alembic migration 0005_trade_tables: saved_searches, trade_statistics,
data_providers (Global Trade Intelligence module)."""
from alembic import op
import sqlalchemy as sa

revision = "0005_trade_tables"
down_revision = "0004_profile_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default="trade"),
        sa.Column("query_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "kind", "name",
                            name="uq_saved_search_name"),
    )
    op.create_index("ix_saved_searches_user_id", "saved_searches", ["user_id"])
    op.create_index("ix_saved_searches_kind", "saved_searches", ["kind"])
    op.create_index("ix_saved_searches_created_at", "saved_searches",
                    ["created_at"])
    op.create_index("ix_saved_search_user_kind", "saved_searches",
                    ["user_id", "kind"])

    op.create_table(
        "trade_statistics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("provider", sa.String(50), nullable=False,
                  server_default="un_comtrade"),
        sa.Column("params_json", sa.Text(), nullable=False,
                  server_default="{}"),
        sa.Column("payload_json", sa.Text(), nullable=False,
                  server_default="{}"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_status", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_trade_statistics_cache_key", "trade_statistics",
                    ["cache_key"], unique=True)
    op.create_index("ix_trade_statistics_fetched_at", "trade_statistics",
                    ["fetched_at"])
    op.create_index("ix_trade_stat_provider_fetched", "trade_statistics",
                    ["provider", "fetched_at"])

    op.create_table(
        "data_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("endpoint", sa.String(2048), nullable=True),
        sa.Column("auth_type", sa.String(50), nullable=False,
                  server_default="none"),
        sa.Column("auth_configured", sa.Boolean(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("data_categories", sa.Text(), nullable=True,
                  server_default="[]"),
        sa.Column("country_coverage", sa.Text(), nullable=True,
                  server_default="[]"),
        sa.Column("available_fields", sa.Text(), nullable=True,
                  server_default="[]"),
        sa.Column("usage_limits", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False,
                  server_default="not_configured"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_data_providers_key", "data_providers", ["key"],
                    unique=True)
    op.create_index("ix_data_providers_status", "data_providers", ["status"])
    op.create_index("ix_data_provider_status", "data_providers",
                    ["status", "is_enabled"])


def downgrade() -> None:
    op.drop_table("data_providers")
    op.drop_table("trade_statistics")
    op.drop_table("saved_searches")
