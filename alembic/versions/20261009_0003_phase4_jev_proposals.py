"""phase4 jev_results and trade_proposals

Revision ID: 20261009_0003
Revises: 20261009_0002
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0003"
down_revision: str | None = "20261009_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jev_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("evidence_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("confidence_notes", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_jev_results_evidence_package_id",
        "jev_results",
        ["evidence_package_id"],
    )

    op.create_table(
        "trade_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False, server_default="toobit"),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=True),
        sa.Column("entry_conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stop_loss", sa.String(length=64), nullable=True),
        sa.Column("take_profit", sa.String(length=64), nullable=True),
        sa.Column("sizing", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("leverage", sa.String(length=64), nullable=True),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("analyst_results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("jev_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("uncertainty", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("invalidation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "supervisor_model_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_trade_proposals_correlation_id",
        "trade_proposals",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_trade_proposals_correlation_id", table_name="trade_proposals")
    op.drop_table("trade_proposals")
    op.drop_index("ix_jev_results_evidence_package_id", table_name="jev_results")
    op.drop_table("jev_results")
