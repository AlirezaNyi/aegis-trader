"""phase5 risk_decisions

Revision ID: 20261009_0004
Revises: 20261009_0003
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0004"
down_revision: str | None = "20261009_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("rules_evaluated", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rejection_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "validated_order_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "account_state_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "market_state_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_risk_decisions_proposal_id",
        "risk_decisions",
        ["proposal_id"],
    )
    op.create_index(
        "ix_risk_decisions_correlation_id",
        "risk_decisions",
        ["correlation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_decisions_correlation_id", table_name="risk_decisions")
    op.drop_index("ix_risk_decisions_proposal_id", table_name="risk_decisions")
    op.drop_table("risk_decisions")
