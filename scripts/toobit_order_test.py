#!/usr/bin/env python3
"""Send ONE signed Spot LIMIT BUY to Toobit `POST /api/v1/spot/orderTest`.

orderTest "creates and validates a new order but does not send it into the
matching engine" (official Spot Account/Trade docs). This script never calls
`/api/v1/spot/order` (every outgoing request path is checked before it is sent),
never calls any withdrawal endpoint, and does not touch the Order Manager.

Credentials come from TOOBIT_API_KEY / TOOBIT_API_SECRET via Settings; they are
never printed. Output: HTTP status, exchange code, exchange msg only.

Symbol / price / quantity are owner-chosen (no defaults). Refuses to run unless
`--i-understand-order-test` is passed.

Usage:
  .venv/bin/python scripts/toobit_order_test.py --symbol ADAUSDT \\
      --price 0.25 --quantity 40 --i-understand-order-test
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import httpx

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aegis.config.settings import Settings, clear_settings_cache  # noqa: E402
from aegis.exchange.errors import ExchangeError  # noqa: E402
from aegis.exchange.paths import REST_BASE_URL, VERIFIED_SUBMIT_PATHS  # noqa: E402
from aegis.exchange.spot_client import (  # noqa: E402
    SPOT_ORDER_TEST_PATH,
    ToobitSpotExecutionClient,
)
from aegis.schemas.common import LedgerKind, MarketType  # noqa: E402
from aegis.schemas.market import InstrumentRef  # noqa: E402
from aegis.schemas.orders import OrderIntent  # noqa: E402


class ForbiddenPathError(RuntimeError):
    pass


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a decimal: {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError(f"must be positive: {value!r}")
    return parsed


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Toobit Spot orderTest (validation only)")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--price", type=_positive_decimal, required=True)
    parser.add_argument("--quantity", type=_positive_decimal, required=True)
    parser.add_argument(
        "--i-understand-order-test",
        dest="confirmed",
        action="store_true",
        help="required: confirms a signed request will be sent to /api/v1/spot/orderTest",
    )
    return parser.parse_args(argv)


def _assert_order_test_only(request: httpx.Request) -> None:
    """httpx request hook: refuse anything except POST /api/v1/spot/orderTest."""
    if request.method != "POST" or request.url.path != SPOT_ORDER_TEST_PATH:
        raise ForbiddenPathError(
            f"refusing request {request.method} {request.url.path!r}: only orderTest allowed"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.confirmed:
        print("refused: pass --i-understand-order-test to send one orderTest request")
        return 2
    if VERIFIED_SUBMIT_PATHS["spot_order_test"] != SPOT_ORDER_TEST_PATH:
        print("refused: orderTest path mismatch")
        return 2

    clear_settings_cache()
    settings = Settings(require_database=False)
    if not settings.toobit_credentials_present:
        print("refused: TOOBIT_API_KEY / TOOBIT_API_SECRET not configured")
        return 2

    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=uuid4(),
        risk_decision_correlation_id="manual-order-test",
        client_order_id=f"aegis-ot-{uuid4().hex[:16]}",
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol=args.symbol),
        side="BUY",
        order_type="LIMIT",
        quantity=args.quantity,
        price=args.price,
        created_at=datetime.now(tz=UTC),
    )

    http = httpx.Client(
        base_url=REST_BASE_URL,
        timeout=settings.exchange_timeout_seconds,
        event_hooks={"request": [_assert_order_test_only]},
    )
    try:
        client = ToobitSpotExecutionClient(
            api_key=settings.toobit_api_key,
            api_secret=settings.toobit_api_secret,
            client=http,
            recv_window_ms=settings.exchange_recv_window_ms,
            use_order_test=True,
        )
        # test_order only: never submit(), never /api/v1/spot/order.
        result = client.test_order(intent)
    except (ExchangeError, ForbiddenPathError, httpx.HTTPError) as exc:
        print(f"error={type(exc).__name__}")
        return 1
    finally:
        http.close()

    print(f"http_status={result.http_status}")
    print(f"code={result.code}")
    print(f"msg={result.msg}")
    print(f"accepted={result.accepted}")
    return 0 if result.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
