"""Backtest cost model (fees + optional spread/slippage). Simulation only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_BPS = Decimal("10000")
_QUANT = Decimal("0.00000001")


@dataclass(frozen=True)
class CostModel:
    """Per-fill cost assumptions for a run (not risk policy)."""

    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("0")

    def fill_price(self, mid: Decimal, *, side: str) -> Decimal:
        slip = (mid * self.slippage_bps / _BPS).quantize(_QUANT, rounding=ROUND_HALF_UP)
        half_spread = (mid * self.spread_bps / _BPS / Decimal("2")).quantize(
            _QUANT, rounding=ROUND_HALF_UP
        )
        side_u = side.strip().upper()
        if side_u == "BUY":
            return (mid + slip + half_spread).quantize(_QUANT, rounding=ROUND_HALF_UP)
        if side_u == "SELL":
            return (mid - slip - half_spread).quantize(_QUANT, rounding=ROUND_HALF_UP)
        raise ValueError(f"unsupported side={side!r}")

    def fee(self, quantity: Decimal, price: Decimal) -> Decimal:
        notional = quantity * price
        return (notional * self.fee_bps / _BPS).quantize(_QUANT, rounding=ROUND_HALF_UP)

    def cost_on_fill(self, quantity: Decimal, price: Decimal) -> Decimal:
        """Fee portion attributed as cost contribution (spread/slip embedded in price)."""
        return self.fee(quantity, price)

    def to_assumptions(self) -> dict[str, str]:
        return {
            "fee_bps": str(self.fee_bps),
            "slippage_bps": str(self.slippage_bps),
            "spread_bps": str(self.spread_bps),
            "label": "engineering simulation assumptions — not risk policy",
        }
