"""Execution and exchange package safety tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
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
    with pytest.raises(RuntimeError, match="Phase 1"):
        port.submit(intent)


def test_exchange_package_has_no_http_client_modules() -> None:
    assert exchange.LIVE_SUBMIT_SUPPORTED is False
    root = Path(exchange.__file__).resolve().parent
    py_files = list(root.rglob("*.py"))
    forbidden = ("requests", "httpx", "aiohttp", "websocket")
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert f"import {name}" not in text
            assert f"from {name}" not in text
