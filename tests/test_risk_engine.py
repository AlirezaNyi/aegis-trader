"""Phase 5 Deterministic Risk Engine tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from aegis.config.settings import Settings, TradingMode
from aegis.risk.context import RiskContext
from aegis.risk.engine import evaluate_risk
from aegis.risk.factory import build_risk_policy_from_settings
from aegis.risk.handoff import (
    RiskHandoffDenied,
    assert_may_submit_to_order_manager,
    may_submit_to_order_manager,
)
from aegis.risk.policy import (
    ParamStatus,
    default_draft_policy,
    with_approved_params,
)
from aegis.schemas.common import EvidenceStatus, MarketType, Timeframe
from aegis.schemas.evidence import JevResult
from aegis.schemas.market import InstrumentRef
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.schemas.risk import RiskDecisionType


def _now() -> datetime:
    return datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _proposal(
    *,
    action: ProposalAction = ProposalAction.BUY,
    direction: TradeDirection | None = TradeDirection.LONG,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    stop_loss: Decimal | None = Decimal("90"),
    take_profit: Decimal | None = Decimal("120"),
    leverage: Decimal | None = None,
    market: MarketType = MarketType.SPOT,
    symbol: str = "ETHUSDT",
    strategy_id: str = "demo",
    strategy_version: str = "0.0.1",
    sizing: dict[str, Any] | None = None,
    entry_conditions: dict[str, Any] | None = None,
    supervisor_model_meta: dict[str, Any] | None = None,
) -> TradeProposal:
    now = _now()
    return TradeProposal(
        proposal_id=uuid4(),
        correlation_id="corr-risk-1",
        instrument=InstrumentRef(market_type=market, symbol=symbol),
        direction=direction,
        timeframe=Timeframe.M15,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        action=action,
        entry_conditions=entry_conditions
        or {
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "entry_price": "100",
        },
        expires_at=expires_at or (now + timedelta(minutes=5)),
        stop_loss=stop_loss,
        take_profit=take_profit,
        sizing=sizing or {"method": "fixed_notional", "notional": "50"},
        leverage=leverage,
        evidence_refs=[],
        analyst_results=[],
        jev_result=JevResult(status=EvidenceStatus.UNAVAILABLE),
        uncertainty={},
        invalidation={},
        supervisor_model_meta=supervisor_model_meta or {},
        created_at=created_at or now,
    )


def _healthy_context(**overrides: Any) -> RiskContext:
    base = dict(
        now=_now(),
        market_data_age_ms=100,
        market_integrity_ok=True,
        account_available=True,
        balance_reconciled=True,
        open_positions_count=0,
        pending_orders_count=0,
        aggregate_notional=Decimal("0"),
        instrument_notional=Decimal("0"),
        proposed_notional=Decimal("50"),
        daily_loss=Decimal("0"),
        drawdown=Decimal("0"),
        spread=Decimal("1"),
        liquidity_ok=True,
        fee_estimate=Decimal("0.1"),
        funding_ok=True,
        slippage_model_bps=Decimal("5"),
        symbol_precision_ok=True,
        min_size_ok=True,
        reconciliation_ok=True,
        recent_proposal_ids=[],
        duplicate_detected=False,
        cooldown_active=False,
        account_state_refs=["acct:paper"],
        market_state_refs=["md:eth"],
    )
    base.update(overrides)
    return RiskContext(**base)


def _approved_test_policy(**overrides: Any):
    """Owner-style APPROVED snapshot for tests only — not production defaults."""
    mapping: dict[str, Any] = {
        "RP-INSTRUMENT-ALLOWLIST-SPOT": {"ETHUSDT", "BTCUSDT"},
        "RP-INSTRUMENT-ALLOWLIST-FUTURES": {"ETHUSDT"},
        "RP-MARKETS-ALLOWED": {"spot", "futures"},
        "RP-DIRECTIONS-ALLOWED": {"spot": ["long", "short"], "futures": ["long", "short"]},
        "RP-MAX-LEVERAGE": Decimal("5"),
        "RP-MAX-NOTIONAL-PER-ORDER": Decimal("100"),
        "RP-MAX-NOTIONAL-PER-INSTRUMENT": Decimal("200"),
        "RP-MAX-AGGREGATE-NOTIONAL": Decimal("500"),
        "RP-MAX-OPEN-POSITIONS": 3,
        "RP-MAX-PENDING-ORDERS": 5,
        "RP-DAILY-LOSS-LIMIT": Decimal("50"),
        "RP-DRAWDOWN-LIMIT": Decimal("10"),
        "RP-SIZING-METHOD": "fixed_notional",
        "RP-STOP-LOSS-REQUIRED": True,
        "RP-STOP-LOSS-MAX-DISTANCE": Decimal("20"),
        "RP-TAKE-PROFIT-POLICY": "optional",
        "RP-ORDER-TYPES-ALLOWED": {"LIMIT", "MARKET"},
        "RP-TIME-IN-FORCE-ALLOWED": {"GTC", "IOC"},
        "RP-MAX-SPREAD": Decimal("10"),
        "RP-MIN-LIQUIDITY": "depth_ok",
        "RP-MAX-SLIPPAGE-MODEL": Decimal("50"),
        "RP-MAX-FEE-ESTIMATE": Decimal("5"),
        "RP-FUNDING-CONSTRAINT": "ok_flag",
        "RP-DATA-FRESHNESS-MS": 1000,
        "RP-PROPOSAL-TTL-MS": 600_000,
        "RP-COOLDOWN-AFTER-LOSS": "0s",
        "RP-STRATEGY-ALLOWLIST": {"demo", "demo@0.0.1"},
        "RP-DUPLICATE-WINDOW": "60s",
    }
    mapping.update(overrides)
    return with_approved_params(
        default_draft_policy(),
        mapping,
        policy_version="0.1-test-approved",
    )


def _rule_ids(decision: Any) -> set[str]:
    return {r["rule_id"] for r in decision.rejection_reasons}


def test_default_draft_buy_rejects_unapproved() -> None:
    decision = evaluate_risk(_proposal(), _healthy_context(), _settings())
    assert decision.decision == RiskDecisionType.REJECT
    assert decision.policy_version == "0.1-draft"
    assert decision.validated_order_params is None
    ids = _rule_ids(decision)
    assert "RP-MAX-NOTIONAL-PER-ORDER" in ids
    assert "RP-DATA-FRESHNESS-MS" in ids
    assert any("UNAPPROVED" in r["detail"] for r in decision.rejection_reasons)


def test_factory_returns_draft() -> None:
    policy = build_risk_policy_from_settings(_settings())
    assert policy.policy_version == "0.1-draft"
    assert policy.get("RP-MAX-LEVERAGE") is not None
    assert policy.get("RP-MAX-LEVERAGE").status == ParamStatus.UNAPPROVED  # type: ignore[union-attr]
    assert policy.get("RP-MODE-DEFAULT").status == ParamStatus.APPROVED  # type: ignore[union-attr]


def test_kill_switch_reject() -> None:
    decision = evaluate_risk(
        _proposal(),
        _healthy_context(),
        _settings(kill_switch=True),
        policy=_approved_test_policy(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-KILL-SWITCH" in _rule_ids(decision)


def test_live_without_arm_reject() -> None:
    settings = Settings.model_construct(
        trading_mode=TradingMode.LIVE,
        live_armed=False,
        kill_switch=False,
        require_database=False,
    )
    decision = evaluate_risk(
        _proposal(),
        _healthy_context(),
        settings,  # type: ignore[arg-type]
        policy=_approved_test_policy(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-LIVE-ARMED" in _rule_ids(decision)


def test_no_trade_and_hold_reject() -> None:
    for action in (ProposalAction.NO_TRADE, ProposalAction.HOLD):
        decision = evaluate_risk(
            _proposal(action=action, direction=None, stop_loss=None),
            _healthy_context(),
            _settings(),
            policy=_approved_test_policy(),
        )
        assert decision.decision == RiskDecisionType.REJECT
        assert "RP-ACTION" in _rule_ids(decision)


def test_expired_proposal_reject() -> None:
    now = _now()
    decision = evaluate_risk(
        _proposal(expires_at=now - timedelta(seconds=1)),
        _healthy_context(now=now),
        _settings(),
        policy=_approved_test_policy(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-PROPOSAL-TTL" in _rule_ids(decision)


def test_missing_freshness_integrity_account_reconcile() -> None:
    policy = _approved_test_policy()
    settings = _settings()
    proposal = _proposal()

    d1 = evaluate_risk(
        proposal,
        _healthy_context(market_data_age_ms=None),
        settings,
        policy=policy,
    )
    assert "RP-DATA-FRESHNESS-MS" in _rule_ids(d1)

    d2 = evaluate_risk(
        proposal,
        _healthy_context(market_integrity_ok=False),
        settings,
        policy=policy,
    )
    assert "RP-DATA-INTEGRITY" in _rule_ids(d2)

    d3 = evaluate_risk(
        proposal,
        _healthy_context(account_available=False),
        settings,
        policy=policy,
    )
    assert "RP-ACCOUNT-STATE" in _rule_ids(d3)

    d4 = evaluate_risk(
        proposal,
        _healthy_context(reconciliation_ok=None),
        settings,
        policy=policy,
    )
    assert "RP-RECONCILE" in _rule_ids(d4)


def test_approved_policy_healthy_context_approves_buy() -> None:
    decision = evaluate_risk(
        _proposal(),
        _healthy_context(),
        _settings(),
        policy=_approved_test_policy(),
    )
    assert decision.decision == RiskDecisionType.APPROVE
    assert decision.validated_order_params is not None
    assert decision.validated_order_params["action"] == "BUY"
    assert decision.validated_order_params["stop_loss_fill_not_guaranteed"] is True
    assert may_submit_to_order_manager(decision) is True
    assert_may_submit_to_order_manager(decision)


def test_notional_over_max_reject() -> None:
    decision = evaluate_risk(
        _proposal(),
        _healthy_context(proposed_notional=Decimal("100.01")),
        _settings(),
        policy=_approved_test_policy(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-MAX-NOTIONAL-PER-ORDER" in _rule_ids(decision)


def test_notional_exactly_at_max_approve() -> None:
    decision = evaluate_risk(
        _proposal(),
        _healthy_context(proposed_notional=Decimal("100")),
        _settings(),
        policy=_approved_test_policy(),
    )
    assert decision.decision == RiskDecisionType.APPROVE


def test_decimal_precision_notional_compare() -> None:
    policy = _approved_test_policy(**{"RP-MAX-NOTIONAL-PER-ORDER": Decimal("100")})
    equal = evaluate_risk(
        _proposal(),
        _healthy_context(proposed_notional=Decimal("100.000")),
        _settings(),
        policy=policy,
    )
    assert equal.decision == RiskDecisionType.APPROVE

    over = evaluate_risk(
        _proposal(),
        _healthy_context(proposed_notional=Decimal("100.0000001")),
        _settings(),
        policy=policy,
    )
    assert over.decision == RiskDecisionType.REJECT
    assert "RP-MAX-NOTIONAL-PER-ORDER" in _rule_ids(over)


def test_force_approve_meta_ignored() -> None:
    decision = evaluate_risk(
        _proposal(supervisor_model_meta={"force_approve": True}),
        _healthy_context(market_integrity_ok=False),
        _settings(),
        policy=_approved_test_policy(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-DATA-INTEGRITY" in _rule_ids(decision)


def test_unapproved_blocks_live_even_with_healthy_context() -> None:
    settings = _settings(trading_mode=TradingMode.LIVE, live_armed=True)
    decision = evaluate_risk(
        _proposal(),
        _healthy_context(),
        settings,
        policy=default_draft_policy(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-POLICY-VERSION" in _rule_ids(decision)
    assert any(
        "live path blocked" in r["detail"] or "draft policy" in r["detail"]
        for r in decision.rejection_reasons
    )


def test_policy_params_immutable() -> None:
    policy = default_draft_policy()
    with pytest.raises(TypeError):
        policy.params["RP-MAX-LEVERAGE"] = policy.params["RP-MAX-LEVERAGE"]  # type: ignore[index]


def test_may_submit_false_on_reject() -> None:
    decision = evaluate_risk(_proposal(), _healthy_context(), _settings())
    assert decision.decision == RiskDecisionType.REJECT
    assert may_submit_to_order_manager(decision) is False
    with pytest.raises(RiskHandoffDenied):
        assert_may_submit_to_order_manager(decision)


def test_determinism_identical_inputs() -> None:
    proposal = _proposal()
    context = _healthy_context()
    settings = _settings()
    policy = default_draft_policy()
    a = evaluate_risk(proposal, context, settings, policy=policy)
    b = evaluate_risk(proposal, context, settings, policy=policy)
    assert a.decision == b.decision
    assert a.policy_version == b.policy_version
    assert a.rules_evaluated == b.rules_evaluated
    assert _rule_ids(a) == _rule_ids(b)
    assert len(a.rejection_reasons) == len(b.rejection_reasons)
