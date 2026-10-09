"""SQLAlchemy models (Phase 1–6: audit through paper/backtest ledger rows)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeatureSnapshotRow(Base):
    __tablename__ = "feature_snapshots"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="features-v1")
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="toobit")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    include_intrabar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gap_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AnalystEvidenceRow(Base):
    __tablename__ = "analyst_evidence"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    package_id: Mapped[Any | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    analyst_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class EvidencePackageRow(Base):
    __tablename__ = "evidence_packages"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="toobit")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class StrategyExperimentRow(Base):
    __tablename__ = "strategy_experiments"
    __table_args__ = (
        UniqueConstraint("strategy_id", "strategy_version", name="uq_strategy_experiment_version"),
    )

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    hypothesis_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid4)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    leakage_controls: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class JevResultRow(Base):
    __tablename__ = "jev_results"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    evidence_package_id: Mapped[Any | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TradeProposalRow(Base):
    __tablename__ = "trade_proposals"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="toobit")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    entry_conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stop_loss: Mapped[str | None] = mapped_column(String(64), nullable=True)
    take_profit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sizing: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    leverage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    analyst_results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    jev_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    uncertainty: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    invalidation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    supervisor_model_meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RiskDecisionRow(Base):
    __tablename__ = "risk_decisions"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_evaluated: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rejection_reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    validated_order_params: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    account_state_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    market_state_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class OrderRow(Base):
    """Order state — paper and live distinguished by ledger_kind (Phase 6 writes paper only)."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", "ledger_kind", name="uq_orders_client_ledger"),
    )

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    intent_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ledger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="toobit")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    filled_quantity: Mapped[str] = mapped_column(String(64), nullable=False, default="0")
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FillRow(Base):
    __tablename__ = "fills"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[Any] = mapped_column(UUID(as_uuid=True), nullable=False)
    ledger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="toobit")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[str] = mapped_column(String(64), nullable=False)
    fee: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fee_asset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchanged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionRow(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint(
            "ledger_kind",
            "exchange",
            "market_type",
            "symbol",
            name="uq_positions_ledger_instrument",
        ),
    )

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ledger_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="toobit")
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unrealized_pnl: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperBalanceRow(Base):
    """Paper cash balances only — ledger_kind must remain 'paper'."""

    __tablename__ = "paper_balances"
    __table_args__ = (
        UniqueConstraint("ledger_kind", "asset", name="uq_paper_balances_ledger_asset"),
    )

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ledger_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    balance: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BacktestRunRow(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[Any] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    software_version: Mapped[str] = mapped_column(String(64), nullable=False)
    splits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    train_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    validation_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    oos_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    uncertainty_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
