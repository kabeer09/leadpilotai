"""Alembic migration 0006_evaluation_result_json: durable research payload
on evaluation_results (Phase 3 — company research module).

SQLite dev picks the column up via the startup ad-hoc ALTER in app/main.py;
this migration covers the alembic/PostgreSQL path.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_evaluation_result_json"
down_revision = "0005_trade_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluation_results",
                  sa.Column("result_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("evaluation_results", "result_json")
