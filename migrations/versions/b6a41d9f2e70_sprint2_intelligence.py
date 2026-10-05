"""sprint2_intelligence

Revision ID: b6a41d9f2e70
Revises: 86796390003b
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6a41d9f2e70"
down_revision: Union[str, None] = "86796390003b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_sync_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("observations_written", sa.Integer(), nullable=False),
        sa.Column("markets_requested", sa.JSON(), nullable=False),
        sa.Column("metrics_available", sa.JSON(), nullable=False),
        sa.Column("rate_limit_status", sa.String(80), nullable=False),
        sa.Column("data_confidence", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.CheckConstraint("data_confidence >= 0 AND data_confidence <= 1", name="ck_provider_run_confidence"),
    )
    op.create_index("ix_provider_runs_name_started", "provider_sync_runs", ["provider_name", "started_at"])

    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_name", sa.String(120), nullable=False),
        sa.Column("provider_entity_id", sa.String(160), nullable=False),
        sa.Column("canonical_entity_id", sa.String(36)),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_label", sa.String(240)),
        sa.Column("artist_label", sa.String(240)),
        sa.Column("metric_code", sa.String(80), nullable=False),
        sa.Column("platform", sa.String(80), nullable=False),
        sa.Column("market_code", sa.String(80), nullable=False),
        sa.Column("genre_slug", sa.String(100)),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("source_url", sa.String(500)),
        sa.Column("rights_basis", sa.String(20), nullable=False),
        sa.Column("sync_run_id", sa.String(36)),
        sa.CheckConstraint("entity_type IN ('track', 'artist', 'playlist', 'genre', 'market')", name="ck_metric_entity_type"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_metric_confidence"),
        sa.CheckConstraint("coverage IS NULL OR (coverage >= 0 AND coverage <= 1)", name="ck_metric_coverage"),
        sa.ForeignKeyConstraint(["sync_run_id"], ["provider_sync_runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "provider_name", "entity_type", "provider_entity_id", "metric_code", "platform", "market_code", "observed_at",
            name="uq_metric_snapshot_observation",
        ),
    )
    op.create_index("ix_metric_entity_time", "metric_snapshots", ["entity_type", "provider_entity_id", "metric_code", "platform", "market_code", "observed_at"])
    op.create_index("ix_metric_market_time", "metric_snapshots", ["market_code", "metric_code", "observed_at"])

    op.create_table(
        "intelligence_scores",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("score_type", sa.String(40), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("provider_entity_id", sa.String(160), nullable=False),
        sa.Column("canonical_entity_id", sa.String(36)),
        sa.Column("entity_label", sa.String(240)),
        sa.Column("artist_label", sa.String(240)),
        sa.Column("market_code", sa.String(80), nullable=False),
        sa.Column("genre_slug", sa.String(100)),
        sa.Column("score_value", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_coverage", sa.Float(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("feature_version", sa.String(40), nullable=False),
        sa.Column("weights_version", sa.String(40), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("score_value >= 0 AND score_value <= 100", name="ck_score_value"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_score_confidence"),
    )
    op.create_index("ix_score_type_market_value", "intelligence_scores", ["score_type", "market_code", "score_value"])
    op.create_index("ix_score_entity_time", "intelligence_scores", ["entity_type", "provider_entity_id", "calculated_at"])

    op.create_table(
        "cross_border_propagation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_entity_id", sa.String(160), nullable=False),
        sa.Column("canonical_entity_id", sa.String(36)),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("genre_slug", sa.String(100)),
        sa.Column("origin_market", sa.String(80), nullable=False),
        sa.Column("destination_market", sa.String(80), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("days_to_propagate", sa.Integer()),
        sa.Column("growth_velocity", sa.Float()),
        sa.Column("acceleration", sa.Float()),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_propagation_confidence"),
        sa.UniqueConstraint("provider_entity_id", "origin_market", "destination_market", name="uq_cross_border_route"),
    )
    op.create_index("ix_propagation_destination", "cross_border_propagation", ["destination_market", "first_detected_at"])


def downgrade() -> None:
    op.drop_index("ix_propagation_destination", table_name="cross_border_propagation")
    op.drop_table("cross_border_propagation")
    op.drop_index("ix_score_entity_time", table_name="intelligence_scores")
    op.drop_index("ix_score_type_market_value", table_name="intelligence_scores")
    op.drop_table("intelligence_scores")
    op.drop_index("ix_metric_market_time", table_name="metric_snapshots")
    op.drop_index("ix_metric_entity_time", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
    op.drop_index("ix_provider_runs_name_started", table_name="provider_sync_runs")
    op.drop_table("provider_sync_runs")
