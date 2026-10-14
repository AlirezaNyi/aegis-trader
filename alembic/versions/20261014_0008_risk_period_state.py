"""paper risk-period state (period equities, loss-limit locks, protective levels)

Revision ID: 20261014_0008
Revises: 20261013_0007
Create Date: 2026-10-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261014_0008"
down_revision: str | None = "20261013_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # One row per ledger_kind. Protective levels live inside ``state`` JSONB (not as
    # columns on ``positions``) so they are written atomically with the tracker.
    op.create_table(
        "risk_period_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ledger_kind", sa.String(length=16), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("ledger_kind", name="uq_risk_period_state_ledger"),
    )


def downgrade() -> None:
    op.drop_table("risk_period_state")
