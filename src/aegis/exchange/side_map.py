"""Map intent sides onto Toobit Spot / Futures request fields (credential-plane safe)."""

from __future__ import annotations

from typing import Any

from aegis.exchange.errors import ExchangeRejectError
from aegis.schemas.orders import OrderIntent, OrderSide

# Futures v2 ``positionSide`` values verified in docs/API_CONTRACTS.md
# (``POST /api/v2/futures/order``: ``side`` BUY/SELL + ``positionSide`` LONG/SHORT).
VERIFIED_FUTURES_POSITION_SIDES: frozenset[str] = frozenset({"LONG", "SHORT"})


class OrderSideMappingError(ExchangeRejectError):
    """Local pre-submit guard: intent extras conflict with the Risk-approved side, or
    carry an unverified ``positionSide``. Raised before any network call; carries no
    ``http_status`` / ``code`` so the Order Manager treats it as a definitive reject
    (nothing was sent)."""


def spot_side(side: OrderSide | str) -> str:
    """Spot uses BUY / SELL only (verified). Anything else raises ``ValueError``."""
    return OrderSide.parse(side).value


def futures_side_and_position(intent: OrderIntent) -> tuple[str, str]:
    """Return (side, positionSide) for Futures v2.

    The order ``side`` always comes from ``intent.side`` (the Risk-approved
    direction, validated at the schema boundary); ``intent.extras`` can never change
    it. A legacy ``extras["side"]`` equal to ``intent.side`` is ignored; a differing
    one raises :class:`OrderSideMappingError` before any request is built.

    Only the position side may come from extras (``position_side`` or
    ``positionSide``, for closing orders) and it must be a verified value
    (LONG / SHORT). Defaults: LONG for BUY, SHORT for SELL.
    """
    extras: dict[str, Any] = intent.extras or {}
    coid = intent.client_order_id
    side = intent.side.value

    if "side" in extras:
        raw_side = extras["side"]
        normalized = raw_side.strip().upper() if isinstance(raw_side, str) else None
        if normalized != side:
            raise OrderSideMappingError(
                "intent extras 'side' conflicts with the Risk-approved side "
                f"{side!r}; refusing to build futures order client_order_id={coid!r}",
                client_order_id=coid,
            )

    candidates: list[str] = []
    for key in ("position_side", "positionSide"):
        if key in extras:
            raw = extras[key]
            if not isinstance(raw, str):
                raise OrderSideMappingError(
                    f"intent extras {key!r} must be a string "
                    f"client_order_id={coid!r}",
                    client_order_id=coid,
                )
            candidates.append(raw.strip().upper())
    if len(set(candidates)) > 1:
        raise OrderSideMappingError(
            "intent extras 'position_side' and 'positionSide' disagree "
            f"client_order_id={coid!r}",
            client_order_id=coid,
        )
    if candidates:
        position = candidates[0]
    elif intent.side is OrderSide.BUY:
        position = "LONG"
    elif intent.side is OrderSide.SELL:
        position = "SHORT"
    else:  # pragma: no cover — OrderSide has exactly two members
        raise OrderSideMappingError(
            f"unsupported futures side {intent.side!r}", client_order_id=coid
        )
    if position not in VERIFIED_FUTURES_POSITION_SIDES:
        raise OrderSideMappingError(
            f"unverified futures positionSide {position!r} client_order_id={coid!r}",
            client_order_id=coid,
        )
    return side, position
