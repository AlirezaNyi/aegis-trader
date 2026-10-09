"""Map intent sides onto Toobit Spot / Futures request fields (credential-plane safe)."""

from __future__ import annotations

from typing import Any

from aegis.schemas.orders import OrderIntent


def spot_side(side: str) -> str:
    """Spot uses BUY / SELL only (verified)."""
    normalized = side.strip().upper()
    if normalized in {"BUY", "SELL"}:
        return normalized
    if normalized in {"LONG", "BUY_OPEN", "BUY_CLOSE"}:
        return "BUY"
    if normalized in {"SHORT", "SELL_OPEN", "SELL_CLOSE"}:
        return "SELL"
    raise ValueError(f"unsupported spot side: {side!r}")


def futures_side_and_position(intent: OrderIntent) -> tuple[str, str]:
    """Return (side, positionSide) for Futures v2."""
    extras: dict[str, Any] = intent.extras or {}
    if "side" in extras and "position_side" in extras:
        return str(extras["side"]).upper(), str(extras["position_side"]).upper()
    if "positionSide" in extras:
        side = str(extras.get("side", intent.side)).upper()
        return side, str(extras["positionSide"]).upper()

    normalized = intent.side.strip().upper()
    if normalized == "BUY":
        position = str(extras.get("position_side", extras.get("positionSide", "LONG"))).upper()
        return "BUY", position
    if normalized == "SELL":
        position = str(extras.get("position_side", extras.get("positionSide", "SHORT"))).upper()
        return "SELL", position
    if normalized in {"LONG", "BUY_OPEN"}:
        return "BUY", "LONG"
    if normalized in {"SHORT", "SELL_OPEN"}:
        return "SELL", "SHORT"
    if normalized == "BUY_CLOSE":
        return "BUY", "SHORT"
    if normalized == "SELL_CLOSE":
        return "SELL", "LONG"
    raise ValueError(f"unsupported futures side/direction: {intent.side!r}")
