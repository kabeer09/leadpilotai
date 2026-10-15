"""Add Google link columns + oauth_states table (additive only).

Revision ID: 0002_google
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_google"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("gmail_refresh_token", sa.Text(), nullable=True))
    op.create_index("ix_users_google_sub", "users", ["google_sub"])
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("value_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_oauth_states_kind", "oauth_states", ["kind"])
    op.create_index("ix_oauth_states_hash", "oauth_states", ["value_hash"])


def downgrade() -> None:
    op.drop_table("oauth_states")
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "gmail_refresh_token")
    op.drop_column("users", "google_sub")
