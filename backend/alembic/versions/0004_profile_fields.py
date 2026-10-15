"""Alembic migration 0004_profile_fields."""
from alembic import op
import sqlalchemy as sa

revision = "0004_profile_fields"
down_revision = "0003_google_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("hall", sa.String(100), nullable=True))
    op.add_column("companies", sa.Column("booth", sa.String(100), nullable=True))
    op.add_column("companies", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("products_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "products_json")
    op.drop_column("companies", "address")
    op.drop_column("companies", "booth")
    op.drop_column("companies", "hall")
