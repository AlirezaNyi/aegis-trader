"""Risk handoff gates for material decision-to-intent binding."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from aegis.schemas.orders import OrderIntent
from aegis.schemas.risk import RiskDecision, RiskDecisionType


class RiskHandoffDenied(RuntimeError):
    """Raised when a decision cannot be handed to execution."""


class IntentBindingError(RiskHandoffDenied):
    """Raised when an OrderIntent is not exactly covered by Risk approval."""


def may_submit_to_order_manager(
    decision: RiskDecision,
    *,
    now: datetime | None = None,
) -> bool:
    """True only for non-expired APPROVE decisions.

    Material field binding is enforced separately by
    :func:`assert_intent_matches_decision`.
    """
    if decision.decision != RiskDecisionType.APPROVE:
        return False
    clock = now if now is not None else datetime.now(tz=UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    expires = decision.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > clock


def assert_may_submit_to_order_manager(
    decision: RiskDecision,
    *,
    now: datetime | None = None,
) -> None:
    if not may_submit_to_order_manager(decision, now=now):
        raise RiskHandoffDenied(
            f"Order Manager handoff denied: decision={decision.decision.value} "
            f"expires_at={decision.expires_at.isoformat()}"
        )


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise IntentBindingError(f"approved {field} is not numeric") from exc
    if not result.is_finite():
        raise IntentBindingError(f"approved {field} is not finite")
    return result


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentBindingError(f"approved {field} must be an object")
    return value


def assert_intent_matches_decision(
    decision: RiskDecision,
    intent: OrderIntent,
) -> None:
    """Require every executable material field to be covered by Risk approval.

    Backtest shortcuts must not pass through PaperBroker or OrderManager with
    identity-only approval. They must construct a complete approved parameter
    block or remain a no-trade result.
    """
    if decision.decision != RiskDecisionType.APPROVE:
        raise IntentBindingError("only APPROVE decisions may bind an intent")
    if decision.proposal_id != intent.proposal_id:
        raise IntentBindingError(
            "RiskDecision.proposal_id does not match OrderIntent.proposal_id"
        )
    if decision.correlation_id != intent.risk_decision_correlation_id:
        raise IntentBindingError(
            "RiskDecision.correlation_id does not match "
            "OrderIntent.risk_decision_correlation_id"
        )

    if decision.validated_order_params is None:
        raise IntentBindingError("validated_order_params is required for execution")
    params = _mapping(decision.validated_order_params, field="validated_order_params")

    instrument = _mapping(params.get("instrument"), field="instrument")
    for field, actual in (
        ("exchange", intent.instrument.exchange),
        ("market_type", intent.instrument.market_type.value),
        ("symbol", intent.instrument.symbol),
    ):
        approved = instrument.get(field)
        if approved is None or str(approved) != actual:
            raise IntentBindingError(
                f"intent {field} {actual!r} != approved {approved!r}"
            )

    approved_action = params.get("action")
    if approved_action is None or str(approved_action).upper() != intent.side.value:
        raise IntentBindingError(
            f"intent side {intent.side.value!r} != approved action {approved_action!r}"
        )

    entry = _mapping(params.get("entry_conditions"), field="entry_conditions")
    approved_order_type = entry.get("order_type")
    if approved_order_type is None or str(approved_order_type).upper() != intent.order_type.upper():
        raise IntentBindingError(
            f"intent order_type {intent.order_type!r} != approved order_type "
            f"{approved_order_type!r}"
        )
    sizing = _mapping(params.get("sizing"), field="sizing")
    approved_tif = entry.get("time_in_force", entry.get("tif", sizing.get("time_in_force")))
    if intent.time_in_force is not None:
        if approved_tif is None or str(approved_tif).upper() != intent.time_in_force.upper():
            raise IntentBindingError(
                f"intent time_in_force {intent.time_in_force!r} != approved "
                f"time_in_force {approved_tif!r}"
            )
    elif approved_tif is not None:
        raise IntentBindingError("approved time_in_force is missing from intent")

    approved_entry = entry.get("entry_price", sizing.get("entry_price"))
    if intent.price is not None:
        if approved_entry is None:
            raise IntentBindingError("approved entry_price is required for priced intent")
        if intent.price != _decimal(approved_entry, field="entry_price"):
            raise IntentBindingError(
                f"intent price {intent.price} != approved entry_price {approved_entry}"
            )

    approved_notional = sizing.get("notional")
    if approved_notional is None:
        raise IntentBindingError("approved notional is required for execution")
    sizing_price = (
        intent.price
        if intent.price is not None
        else None
        if approved_entry is None
        else _decimal(approved_entry, field="entry_price")
    )
    if sizing_price is None or sizing_price <= 0:
        raise IntentBindingError("positive approved sizing price is required")
    try:
        max_quantity = _decimal(approved_notional, field="notional") / sizing_price
    except ArithmeticError as exc:  # e.g. decimal.Overflow on absurd exponents
        raise IntentBindingError("approved quantity ceiling is not computable") from exc
    if max_quantity <= 0 or intent.quantity <= 0 or intent.quantity > max_quantity:
        raise IntentBindingError(
            f"intent quantity {intent.quantity} exceeds approved quantity ceiling "
            f"{max_quantity}"
        )
