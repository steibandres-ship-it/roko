"""organic_campaign_reports

Revision ID: d74e19ac6f21
Revises: c843b1239ae1
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d74e19ac6f21"
down_revision: Union[str, None] = "c843b1239ae1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organic_campaign_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("playlist_slug", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=60), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("followers_source", sa.String(length=120), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("reach", sa.Integer(), nullable=True),
        sa.Column("link_clicks", sa.Integer(), nullable=True),
        sa.Column("followers_start", sa.Integer(), nullable=True),
        sa.Column("followers_end", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("period_end >= period_start", name="ck_organic_campaign_period_order"),
        sa.CheckConstraint("views IS NULL OR views >= 0", name="ck_organic_campaign_views_nonnegative"),
        sa.CheckConstraint("reach IS NULL OR reach >= 0", name="ck_organic_campaign_reach_nonnegative"),
        sa.CheckConstraint("link_clicks IS NULL OR link_clicks >= 0", name="ck_organic_campaign_clicks_nonnegative"),
        sa.CheckConstraint("followers_start IS NULL OR followers_start >= 0", name="ck_organic_campaign_followers_start_nonnegative"),
        sa.CheckConstraint("followers_end IS NULL OR followers_end >= 0", name="ck_organic_campaign_followers_end_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("playlist_slug", "period_start", "period_end", name="uq_organic_campaign_period"),
    )
    op.create_index(
        "ix_organic_campaign_reports_playlist_period",
        "organic_campaign_reports",
        ["playlist_slug", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_organic_campaign_reports_playlist_period", table_name="organic_campaign_reports")
    op.drop_table("organic_campaign_reports")
