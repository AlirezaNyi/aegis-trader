"""phase6 paper orders fills positions balances backtest_runs

Revision ID: 20261009_0005
Revises: 20261009_0004
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0005"
down_revision: str | None = "20261009_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_order_id", sa.String(length=128), nullable=False),
        sa.Column("exchange_order_id", sa.String(length=128), nullable=True),
        sa.Column("ledger_kind", sa.String(length=16), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("order_type", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("filled_quantity", sa.String(length=64), nullable=False),
        sa.Column("price", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "client_order_id",
            "ledger_kind",
            name="uq_orders_client_ledger",
        ),
    )
    op.create_index("ix_orders_ledger_kind", "orders", ["ledger_kind"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])

    op.create_table(
        "fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ledger_kind", sa.String(length=16), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("price", sa.String(length=64), nullable=False),
        sa.Column("fee", sa.String(length=64), nullable=True),
        sa.Column("fee_asset", sa.String(length=32), nullable=True),
        sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])
    op.create_index("ix_fills_ledger_kind", "fills", ["ledger_kind"])

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ledger_kind", sa.String(length=16), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("entry_price", sa.String(length=64), nullable=True),
        sa.Column("unrealized_pnl", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "ledger_kind",
            "exchange",
            "market_type",
            "symbol",
            name="uq_positions_ledger_instrument",
        ),
    )
    op.create_index("ix_positions_ledger_kind", "positions", ["ledger_kind"])

    op.create_table(
        "paper_balances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ledger_kind", sa.String(length=16), nullable=False),
        sa.Column("asset", sa.String(length=32), nullable=False),
        sa.Column("balance", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "ledger_kind",
            "asset",
            name="uq_paper_balances_ledger_asset",
        ),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("software_version", sa.String(length=64), nullable=False),
        sa.Column("splits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("train_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "validation_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("oos_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("uncertainty_notes", sa.Text(), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_backtest_runs_strategy_id", "backtest_runs", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_strategy_id", table_name="backtest_runs")
    op.drop_table("backtest_runs")
    op.drop_table("paper_balances")
    op.drop_index("ix_positions_ledger_kind", table_name="positions")
    op.drop_table("positions")
    op.drop_index("ix_fills_ledger_kind", table_name="fills")
    op.drop_index("ix_fills_order_id", table_name="fills")
    op.drop_table("fills")
    op.drop_index("ix_orders_symbol", table_name="orders")
    op.drop_index("ix_orders_ledger_kind", table_name="orders")
    op.drop_table("orders")
