"""phase3 feature and evidence tables

Revision ID: 20261009_0002
Revises: 20261009_0001
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0002"
down_revision: str | None = "20261009_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False, server_default="toobit"),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("include_intrabar", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("candle_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gap_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_feature_snapshots_symbol_tf_as_of",
        "feature_snapshots",
        ["symbol", "timeframe", "as_of"],
    )

    op.create_table(
        "evidence_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False, server_default="toobit"),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_evidence_packages_correlation_id",
        "evidence_packages",
        ["correlation_id"],
    )

    op.create_table(
        "analyst_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analyst_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_analyst_evidence_package_id", "analyst_evidence", ["package_id"])
    op.create_index("ix_analyst_evidence_analyst_type", "analyst_evidence", ["analyst_type"])

    op.create_table(
        "strategy_experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hypothesis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("leakage_controls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "strategy_id",
            "strategy_version",
            name="uq_strategy_experiment_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("strategy_experiments")
    op.drop_index("ix_analyst_evidence_analyst_type", table_name="analyst_evidence")
    op.drop_index("ix_analyst_evidence_package_id", table_name="analyst_evidence")
    op.drop_table("analyst_evidence")
    op.drop_index("ix_evidence_packages_correlation_id", table_name="evidence_packages")
    op.drop_table("evidence_packages")
    op.drop_index("ix_feature_snapshots_symbol_tf_as_of", table_name="feature_snapshots")
    op.drop_table("feature_snapshots")
