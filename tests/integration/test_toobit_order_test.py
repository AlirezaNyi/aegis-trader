"""Signed Toobit spot orderTest (validation only; no order, no funds).

Opt-in: AEGIS_TEST_TOOBIT_ORDER_TEST=1 plus AEGIS_TEST_TOOBIT_SYMBOL, _PRICE, _QTY and
TOOBIT_API_KEY/SECRET (Trade-only key, no Withdraw). Toobit has no verified testnet;
this reuses scripts/toobit_order_test.py, whose request hook refuses any path other
than POST /api/v1/spot/orderTest.
"""

from __future__ import annotations

import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from aegis.config.settings import Settings, clear_settings_cache
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import OrderIntent, OrderSide

pytestmark = pytest.mark.integration_order_test

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "toobit_order_test.py"


def _load_script() -> object:
    spec = importlib.util.spec_from_file_location("toobit_order_test_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_signed_order_test_round_trip() -> None:
    symbol = os.environ.get("AEGIS_TEST_TOOBIT_SYMBOL", "")
    price = os.environ.get("AEGIS_TEST_TOOBIT_PRICE", "")
    qty = os.environ.get("AEGIS_TEST_TOOBIT_QTY", "")
    if not (symbol and price and qty):
        pytest.skip("set AEGIS_TEST_TOOBIT_SYMBOL, _PRICE and _QTY (owner-chosen)")
    clear_settings_cache()
    settings = Settings(require_database=False)
    if not settings.toobit_credentials_present:
        pytest.skip("TOOBIT_API_KEY / TOOBIT_API_SECRET not configured")
    from datetime import UTC, datetime

    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=uuid4(),
        risk_decision_correlation_id="integration-order-test",
        client_order_id=f"aegis-it-{uuid4().hex[:16]}",
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol=symbol),
        side=OrderSide.BUY,
        order_type="LIMIT",
        quantity=Decimal(qty),
        price=Decimal(price),
        time_in_force="GTC",
        created_at=datetime.now(tz=UTC),
    )
    script = _load_script()
    result = await script._send_order_test(settings, intent)  # type: ignore[attr-defined]
    # Signing/auth must be accepted; an exchange-side validation code is reported, not hidden.
    assert result.http_status != 401, "signature or API key rejected"
    assert result.accepted or result.code is not None
