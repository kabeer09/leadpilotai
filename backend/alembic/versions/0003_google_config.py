"""App-level Google OAuth client pasted at signup (additive only).

Revision ID: 0003_google_config
Revises: 0002_google
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_google_config"
down_revision = "0002_google"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_oauth_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(1024), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("google_oauth_config")
