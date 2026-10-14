"""Pre-submit precision / filter guard for Spot orders (pure; no network).

Filters come from public ``GET /api/v1/exchangeInfo`` symbol ``filters``
(``PRICE_FILTER.tickSize``, ``LOT_SIZE.stepSize`` / ``minQty``,
``MIN_NOTIONAL.minNotional``) as parsed by ``aegis.market_data.metadata``. The
client never fetches metadata itself; callers pass ``SymbolFilters`` in.

Fail closed: any missing filter value, non-positive step/tick, or an order type
whose unit semantics are unverified is refused with ``OrderPrecisionError``
(nothing is sent).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from aegis.exchange.errors import OrderPrecisionError
from aegis.schemas.metadata import SymbolMetadata
from aegis.schemas.orders import OrderIntent, OrderSide


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    tick_size: Decimal | None
    step_size: Decimal | None
    min_qty: Decimal | None
    min_notional: Decimal | None

    @classmethod
    def from_metadata(cls, meta: SymbolMetadata) -> SymbolFilters:
        return cls(
            symbol=meta.symbol,
            tick_size=meta.tick_size,
            step_size=meta.step_size,
            min_qty=meta.min_qty,
            min_notional=meta.min_notional,
        )


def _quantize_to_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    units = (value / step).to_integral_value(rounding=rounding)
    return units * step


def round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Largest multiple of ``step`` that is <= ``value``."""
    return _quantize_to_step(value, step, ROUND_FLOOR)


def round_up_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Smallest multiple of ``step`` that is >= ``value``."""
    return _quantize_to_step(value, step, ROUND_CEILING)


def _reject(intent: OrderIntent, reason: str) -> OrderPrecisionError:
    return OrderPrecisionError(
        f"precision guard refused client_order_id={intent.client_order_id!r}: {reason}",
        client_order_id=intent.client_order_id,
    )


def apply_spot_precision(
    intent: OrderIntent,
    filters: SymbolFilters,
    *,
    reference_price: Decimal | None = None,
) -> OrderIntent:
    """Return a copy of ``intent`` rounded to exchange filters, or raise.

    * quantity is rounded DOWN to ``stepSize`` (never sends more than approved);
    * LIMIT price is rounded to ``tickSize`` conservatively — BUY rounds down,
      SELL rounds up — so the limit is never worse than the approved price;
    * rejects if rounded quantity < ``minQty`` or notional < ``minNotional``.

    MARKET BUY is refused: Toobit Spot treats MARKET BUY ``quantity`` as quote-asset
    amount whose precision rule is not verified. MARKET SELL needs
    ``reference_price`` to evaluate ``minNotional``.
    """
    if intent.instrument.symbol != filters.symbol:
        raise _reject(intent, f"filters are for {filters.symbol!r}")
    tick, step = filters.tick_size, filters.step_size
    min_qty, min_notional = filters.min_qty, filters.min_notional
    if tick is None or step is None or min_qty is None or min_notional is None:
        raise _reject(intent, "missing tickSize/stepSize/minQty/minNotional (fail closed)")
    if tick <= 0 or step <= 0:
        raise _reject(intent, "non-positive tickSize/stepSize")

    order_type = intent.order_type.strip().upper()
    try:
        side = OrderSide.parse(intent.side)
    except ValueError:
        raise _reject(intent, f"unsupported spot side {intent.side!r}") from None
    if order_type == "MARKET" and side is OrderSide.BUY:
        raise _reject(intent, "MARKET BUY quote-amount precision is unverified")
    if order_type not in {"LIMIT", "LIMIT_MAKER", "MARKET"}:
        raise _reject(intent, f"unsupported order type {intent.order_type!r}")

    qty = round_down_to_step(intent.quantity, step)
    if qty <= 0 or qty < min_qty:
        raise _reject(intent, f"quantity {qty} below minQty {min_qty}")

    price: Decimal | None
    if order_type == "MARKET":
        price = None
        if reference_price is None or reference_price <= 0:
            raise _reject(intent, "MARKET order needs reference_price for minNotional")
        notional_price = reference_price
    else:
        if intent.price is None or intent.price <= 0:
            raise _reject(intent, "LIMIT order requires a positive price")
        price = (
            round_down_to_step(intent.price, tick)
            if side is OrderSide.BUY
            else round_up_to_step(intent.price, tick)
        )
        if price <= 0:
            raise _reject(intent, f"price rounds to {price}")
        notional_price = price

    notional = qty * notional_price
    if notional < min_notional:
        raise _reject(intent, f"notional {notional} below minNotional {min_notional}")

    return intent.model_copy(update={"quantity": qty, "price": price})
