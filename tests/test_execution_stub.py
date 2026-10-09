"""Execution port and exchange package safety tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

import aegis.exchange as exchange
from aegis.interfaces.execution import NullExecutionPort
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import OrderIntent


def test_null_execution_refuses_submit() -> None:
    port = NullExecutionPort()
    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=uuid4(),
        risk_decision_correlation_id="corr",
        client_order_id="c1",
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT"),
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("1"),
        created_at=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError, match="null"):
        port.submit(intent)


def test_exchange_live_submit_supported_but_factory_defaults_null() -> None:
    """Phase 7: code path exists; default factory without live gates is Null."""
    assert exchange.LIVE_SUBMIT_SUPPORTED is True
    from aegis.config.settings import Settings, TradingMode, clear_settings_cache
    from aegis.exchange.factory import build_execution_port

    clear_settings_cache()
    settings = Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
    )
    assert isinstance(build_execution_port(settings), NullExecutionPort)
