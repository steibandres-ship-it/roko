"""score_readiness_and_release_date

Revision ID: c843b1239ae1
Revises: b6a41d9f2e70
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c843b1239ae1"
down_revision: Union[str, None] = "b6a41d9f2e70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("metric_snapshots", sa.Column("release_date", sa.Date(), nullable=True))
    with op.batch_alter_table("intelligence_scores") as batch:
        batch.alter_column("score_value", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("intelligence_scores") as batch:
        batch.alter_column("score_value", existing_type=sa.Float(), nullable=False)
    op.drop_column("metric_snapshots", "release_date")
