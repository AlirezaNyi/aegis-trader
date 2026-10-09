"""Deterministic paper fill pricing and fee model (simulation assumptions only)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from aegis.paper.exceptions import PaperFillError
from aegis.schemas.orders import OrderIntent

_BPS = Decimal("10000")
_FEE_QUANT = Decimal("0.00000001")
_PRICE_QUANT = Decimal("0.00000001")


def compute_fill_price(
    intent: OrderIntent,
    *,
    mid_price: Decimal | None,
    slippage_bps: Decimal,
) -> Decimal:
    """Fill at limit price when provided; otherwise mid ± slippage_bps.

    BUY pays mid + slip; SELL receives mid - slip. Engineering simulation only.
    """
    order_type = intent.order_type.strip().upper()
    if intent.price is not None and order_type in {"LIMIT", "LIMIT_MAKER"}:
        return Decimal(intent.price)

    if mid_price is None:
        raise PaperFillError(
            "mid_price required for market / non-limit paper fills "
            f"(client_order_id={intent.client_order_id!r})"
        )

    mid = Decimal(mid_price)
    slip = (mid * Decimal(slippage_bps) / _BPS).quantize(_PRICE_QUANT, rounding=ROUND_HALF_UP)
    side = intent.side.strip().upper()
    if side == "BUY":
        return (mid + slip).quantize(_PRICE_QUANT, rounding=ROUND_HALF_UP)
    if side == "SELL":
        return (mid - slip).quantize(_PRICE_QUANT, rounding=ROUND_HALF_UP)
    raise PaperFillError(f"unsupported paper side={intent.side!r}")


def compute_fee(
    quantity: Decimal,
    price: Decimal,
    fee_bps: Decimal,
) -> Decimal:
    """Fee = notional * fee_bps / 10000 (simulation assumption, not risk policy)."""
    notional = Decimal(quantity) * Decimal(price)
    return (notional * Decimal(fee_bps) / _BPS).quantize(_FEE_QUANT, rounding=ROUND_HALF_UP)
