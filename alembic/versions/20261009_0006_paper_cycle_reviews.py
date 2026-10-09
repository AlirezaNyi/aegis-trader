"""paper cycle reviews journal for operator dashboard

Revision ID: 20261009_0006
Revises: 20261009_0005
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0006"
down_revision: str | None = "20261009_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_cycle_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("final_open_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposal_action", sa.String(length=16), nullable=True),
        sa.Column("proposal_direction", sa.String(length=16), nullable=True),
        sa.Column("risk_decision", sa.String(length=16), nullable=True),
        sa.Column("entry_price", sa.String(length=64), nullable=True),
        sa.Column("stop_loss", sa.String(length=64), nullable=True),
        sa.Column("take_profit", sa.String(length=64), nullable=True),
        sa.Column("mark_status", sa.String(length=16), nullable=True),
        sa.Column("return_pct", sa.String(length=64), nullable=True),
        sa.Column("exit_price", sa.String(length=64), nullable=True),
        sa.Column("paper_order_id", sa.String(length=128), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_paper_cycle_reviews_symbol_created",
        "paper_cycle_reviews",
        ["symbol", "created_at"],
    )
    op.create_index(
        "ix_paper_cycle_reviews_mark_status",
        "paper_cycle_reviews",
        ["mark_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_cycle_reviews_mark_status", table_name="paper_cycle_reviews")
    op.drop_index(
        "ix_paper_cycle_reviews_symbol_created", table_name="paper_cycle_reviews"
    )
    op.drop_table("paper_cycle_reviews")
