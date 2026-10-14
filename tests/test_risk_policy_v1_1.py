"""Policy 1.1 ("1.1-env"): owner env limits as % of initial capital + new risk rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from aegis.config.settings import Settings, TradingMode
from aegis.risk import missing_policy_env
from aegis.risk.context import RiskContext
from aegis.risk.engine import evaluate_risk
from aegis.risk.factory import build_risk_policy_from_settings
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.risk.policy import (
    FINANCIAL_PARAM_IDS,
    OPTIONAL_FINANCIAL_PARAM_IDS,
    ParamStatus,
    PolicyParam,
    RiskPolicy,
    default_draft_policy,
    with_approved_params,
)
from aegis.schemas.common import EvidenceStatus, MarketType, Timeframe
from aegis.schemas.evidence import JevResult
from aegis.schemas.market import InstrumentRef
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.schemas.risk import RiskDecision, RiskDecisionType

_POLICY_ENV_KEYS = (
    "AEGIS_RISK_POLICY_VERSION",
    "AEGIS_PAPER_INITIAL_BALANCE",
    "AEGIS_RISK_POSITION_PCT",
    "AEGIS_RISK_PER_TRADE_PCT",
    "AEGIS_RISK_DAILY_LOSS_PCT",
    "AEGIS_RISK_WEEKLY_LOSS_PCT",
    "AEGIS_RISK_MONTHLY_LOSS_PCT",
    "AEGIS_RISK_MAX_DRAWDOWN_PCT",
    "AEGIS_RISK_MIN_REWARD_RISK",
    "AEGIS_TRADING_FEE_BPS",
)

# Test-only owner inputs (not production defaults).
_ENV_VALUES: dict[str, object] = {
    "risk_policy_version": "1.1",
    "paper_initial_balance": Decimal("1000"),
    "risk_position_pct": Decimal("10"),
    "risk_daily_loss_pct": Decimal("2"),
    "risk_weekly_loss_pct": Decimal("5"),
    "risk_monthly_loss_pct": Decimal("10"),
    "risk_max_drawdown_pct": Decimal("15"),
    "risk_min_reward_risk": Decimal("1.5"),
}


@pytest.fixture(autouse=True)
def _no_policy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _POLICY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _now() -> datetime:
    return datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
    }
    base.update(_ENV_VALUES)
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _without(*fields: str, **extra: object) -> Settings:
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
    }
    base.update({k: v for k, v in _ENV_VALUES.items() if k not in fields})
    base.update(extra)
    return Settings(**base)  # type: ignore[arg-type]


def _policy(**kwargs: object) -> RiskPolicy:
    return build_risk_policy_from_settings(_settings(**kwargs))


def _value(policy: RiskPolicy, param_id: str) -> Any:
    param = policy.get(param_id)
    assert param is not None, param_id
    assert param.status == ParamStatus.APPROVED, param_id
    return param.value


def _proposal(
    *,
    entry: str = "100",
    stop_loss: Decimal | None = Decimal("97"),
    take_profit: Decimal | None = Decimal("106"),
    method: str = "pct_capital",
    notional: str = "100",
    action: ProposalAction = ProposalAction.BUY,
    direction: TradeDirection = TradeDirection.LONG,
) -> TradeProposal:
    now = _now()
    return TradeProposal(
        proposal_id=uuid4(),
        correlation_id="corr-v11",
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT"),
        direction=direction,
        timeframe=Timeframe.M15,
        strategy_id="demo",
        strategy_version="0.0.1",
        action=action,
        entry_conditions={
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "entry_price": entry,
        },
        expires_at=now + timedelta(seconds=20),
        stop_loss=stop_loss,
        take_profit=take_profit,
        sizing={"method": method, "notional": notional},
        leverage=None,
        evidence_refs=[],
        analyst_results=[],
        jev_result=JevResult(status=EvidenceStatus.UNAVAILABLE),
        uncertainty={},
        invalidation={},
        supervisor_model_meta={},
        created_at=now,
    )


def _context(**overrides: Any) -> RiskContext:
    # Round-trip cost = 2*10 + 5 + 2*5 = 35 bps ⇒ Cu = 0.35 at entry 100.
    base: dict[str, Any] = dict(
        now=_now(),
        market_data_age_ms=100,
        market_integrity_ok=True,
        account_available=True,
        balance_reconciled=True,
        open_positions_count=0,
        pending_orders_count=0,
        aggregate_notional=Decimal("0"),
        instrument_notional=Decimal("0"),
        proposed_notional=Decimal("100"),
        daily_loss=Decimal("0"),
        drawdown=Decimal("0"),
        weekly_loss=Decimal("0"),
        monthly_loss=Decimal("0"),
        spread=Decimal("5"),
        liquidity_ok=True,
        fee_estimate=Decimal("10"),
        funding_ok=True,
        slippage_model_bps=Decimal("5"),
        symbol_precision_ok=True,
        min_size_ok=True,
        reconciliation_ok=True,
        recent_proposal_ids=[],
        duplicate_detected=False,
        cooldown_active=False,
        best_bid=Decimal("99.9"),
        best_ask=Decimal("100.1"),
        account_state_refs=["acct:paper"],
        market_state_refs=["md:eth"],
    )
    base.update(overrides)
    return RiskContext(**base)


def _ids(decision: RiskDecision) -> set[str]:
    return {r["rule_id"] for r in decision.rejection_reasons}


def _detail(decision: RiskDecision, rule_id: str) -> str:
    return " | ".join(r["detail"] for r in decision.rejection_reasons if r["rule_id"] == rule_id)


def _evaluate(
    proposal: TradeProposal | None = None,
    context: RiskContext | None = None,
    policy: RiskPolicy | None = None,
) -> RiskDecision:
    return evaluate_risk(
        proposal or _proposal(),
        context or _context(),
        _settings(),
        policy=policy or _policy(),
    )


# --- Factory: conversions ---------------------------------------------------------


def test_factory_v1_1_converts_percent_of_capital_to_usdt() -> None:
    policy = _policy()
    assert _value(policy, "RP-MAX-NOTIONAL-PER-ORDER") == Decimal("100")
    assert _value(policy, "RP-MAX-NOTIONAL-PER-INSTRUMENT") == Decimal("100")
    # Aggregate = min(position cap * v1.0 open-position count (2), capital).
    assert _value(policy, "RP-MAX-AGGREGATE-NOTIONAL") == Decimal("200")
    assert _value(policy, "RP-DAILY-LOSS-LIMIT") == Decimal("20")
    assert _value(policy, "RP-WEEKLY-LOSS-LIMIT") == Decimal("50")
    assert _value(policy, "RP-MONTHLY-LOSS-LIMIT") == Decimal("100")
    assert _value(policy, "RP-DRAWDOWN-LIMIT") == Decimal("150")
    assert _value(policy, "RP-MIN-REWARD-RISK") == Decimal("1.5")
    assert _value(policy, "RP-TAKE-PROFIT-POLICY") == "required"
    assert _value(policy, "RP-SIZING-METHOD") == "pct_capital"
    assert policy.get("RP-MAX-RISK-PER-TRADE") is None
    for pid in ("RP-WEEKLY-LOSS-LIMIT", "RP-MONTHLY-LOSS-LIMIT", "RP-DRAWDOWN-LIMIT"):
        assert policy.get(pid).unit == "USDT"  # type: ignore[union-attr]
    assert policy.get("RP-MIN-REWARD-RISK").unit == "ratio"  # type: ignore[union-attr]


def test_factory_v1_1_per_trade_enables_risk_sizing() -> None:
    policy = _policy(risk_per_trade_pct=Decimal("0.5"))
    assert _value(policy, "RP-MAX-RISK-PER-TRADE") == Decimal("5")
    assert policy.get("RP-MAX-RISK-PER-TRADE").unit == "USDT"  # type: ignore[union-attr]
    assert _value(policy, "RP-SIZING-METHOD") == "risk_pct_capital"


def test_factory_v1_1_inherits_v1_non_overlaid_params() -> None:
    policy = _policy()
    v1 = owner_approved_policy_v1()
    overlaid = {
        "RP-MAX-NOTIONAL-PER-ORDER",
        "RP-MAX-NOTIONAL-PER-INSTRUMENT",
        "RP-MAX-AGGREGATE-NOTIONAL",
        "RP-DAILY-LOSS-LIMIT",
        "RP-DRAWDOWN-LIMIT",
        "RP-TAKE-PROFIT-POLICY",
        "RP-SIZING-METHOD",
    }
    for pid, param in v1.params.items():
        if pid in overlaid:
            continue
        assert policy.get(pid) == param, pid


def test_factory_v1_1_version_hash_format_and_not_draft() -> None:
    policy = _policy()
    prefix, _, digest = policy.policy_version.partition("+")
    assert prefix == "1.1-env"
    assert len(digest) == 8
    int(digest, 16)  # hex
    assert "draft" not in policy.policy_version.lower()
    assert policy.is_draft is False


def test_factory_v1_1_hash_stable_and_value_sensitive() -> None:
    a = _policy()
    b = _policy()
    assert a.policy_version == b.policy_version
    # Alias selects the same policy.
    assert _policy(risk_policy_version="1.1-env").policy_version == a.policy_version
    # Formatting of the same number does not change the fingerprint.
    assert _policy(risk_position_pct=Decimal("10.00")).policy_version == a.policy_version
    # Any value change does.
    assert _policy(risk_position_pct=Decimal("11")).policy_version != a.policy_version
    assert _policy(risk_min_reward_risk=Decimal("2")).policy_version != a.policy_version
    assert (
        _policy(risk_per_trade_pct=Decimal("0.5")).policy_version != a.policy_version
    )


def test_factory_v1_0_and_draft_unchanged() -> None:
    v1 = build_risk_policy_from_settings(_settings(risk_policy_version="1.0"))
    assert v1.policy_version == "1.0"
    for pid in OPTIONAL_FINANCIAL_PARAM_IDS:
        assert v1.get(pid) is None
    assert v1.get("RP-TAKE-PROFIT-POLICY").value == "optional"  # type: ignore[union-attr]
    draft = build_risk_policy_from_settings(_settings(risk_policy_version="0.1-draft"))
    assert draft.policy_version == "0.1-draft"
    for pid in OPTIONAL_FINANCIAL_PARAM_IDS:
        assert draft.get(pid) is None
    # New ids are not part of the v1.0 completeness set.
    assert not set(OPTIONAL_FINANCIAL_PARAM_IDS) & set(FINANCIAL_PARAM_IDS)


def test_factory_unknown_version_fails_closed_to_draft() -> None:
    policy = build_risk_policy_from_settings(_settings(risk_policy_version="1.2"))
    assert policy.policy_version == "0.1-draft"


# --- Factory: missing env -> UNAPPROVED -> REJECT ---------------------------------


def test_missing_policy_env_lists_names_only_for_v1_1() -> None:
    assert missing_policy_env(_settings()) == []
    assert missing_policy_env(_without("risk_weekly_loss_pct")) == [
        "AEGIS_RISK_WEEKLY_LOSS_PCT"
    ]
    assert missing_policy_env(_without("paper_initial_balance", "risk_min_reward_risk")) == [
        "AEGIS_PAPER_INITIAL_BALANCE",
        "AEGIS_RISK_MIN_REWARD_RISK",
    ]
    # Not selected ⇒ nothing reported (env limits are irrelevant to v1.0 / draft).
    assert missing_policy_env(_without("paper_initial_balance", risk_policy_version="1.0")) == []


def test_missing_capital_marks_capital_params_unapproved_and_rejects() -> None:
    policy = build_risk_policy_from_settings(_without("paper_initial_balance"))
    for pid in (
        "RP-MAX-NOTIONAL-PER-ORDER",
        "RP-MAX-NOTIONAL-PER-INSTRUMENT",
        "RP-MAX-AGGREGATE-NOTIONAL",
        "RP-DAILY-LOSS-LIMIT",
        "RP-WEEKLY-LOSS-LIMIT",
        "RP-MONTHLY-LOSS-LIMIT",
        "RP-DRAWDOWN-LIMIT",
    ):
        param = policy.get(pid)
        assert param is not None
        assert param.status == ParamStatus.UNAPPROVED
        assert param.value is None
    assert policy.policy_version.startswith("1.1-env+")
    decision = _evaluate(policy=policy)
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-MAX-NOTIONAL-PER-ORDER" in _ids(decision)
    assert "UNAPPROVED" in _detail(decision, "RP-WEEKLY-LOSS-LIMIT")


@pytest.mark.parametrize(
    ("field", "param_id"),
    [
        ("risk_position_pct", "RP-MAX-NOTIONAL-PER-ORDER"),
        ("risk_daily_loss_pct", "RP-DAILY-LOSS-LIMIT"),
        ("risk_weekly_loss_pct", "RP-WEEKLY-LOSS-LIMIT"),
        ("risk_monthly_loss_pct", "RP-MONTHLY-LOSS-LIMIT"),
        ("risk_max_drawdown_pct", "RP-DRAWDOWN-LIMIT"),
        ("risk_min_reward_risk", "RP-MIN-REWARD-RISK"),
    ],
)
def test_each_missing_env_value_rejects_new_trades(field: str, param_id: str) -> None:
    policy = build_risk_policy_from_settings(_without(field))
    assert policy.get(param_id).status == ParamStatus.UNAPPROVED  # type: ignore[union-attr]
    decision = _evaluate(policy=policy)
    assert decision.decision == RiskDecisionType.REJECT
    assert param_id in _ids(decision)
    assert "UNAPPROVED" in _detail(decision, param_id)


def test_per_trade_without_capital_is_unapproved() -> None:
    policy = build_risk_policy_from_settings(
        _without("paper_initial_balance", risk_per_trade_pct=Decimal("0.5"))
    )
    param = policy.get("RP-MAX-RISK-PER-TRADE")
    assert param is not None and param.status == ParamStatus.UNAPPROVED


def test_present_but_unapproved_optional_param_rejects() -> None:
    base = _policy()
    params = dict(base.params)
    params["RP-MONTHLY-LOSS-LIMIT"] = PolicyParam(
        id="RP-MONTHLY-LOSS-LIMIT", status=ParamStatus.UNAPPROVED, value=None, unit="USDT"
    )
    decision = _evaluate(policy=RiskPolicy(policy_version="1.1-test", params=params))
    assert decision.decision == RiskDecisionType.REJECT
    assert _ids(decision) == {"RP-MONTHLY-LOSS-LIMIT"}


# --- Engine: healthy approve ------------------------------------------------------


def test_v1_1_healthy_proposal_approves_with_reward_risk_net() -> None:
    decision = _evaluate()
    assert decision.decision == RiskDecisionType.APPROVE, decision.rejection_reasons
    assert decision.policy_version.startswith("1.1-env+")
    for pid in (
        "RP-WEEKLY-LOSS-LIMIT",
        "RP-MONTHLY-LOSS-LIMIT",
        "RP-MIN-REWARD-RISK",
        "RP-PERIOD-LOCK",
    ):
        assert pid in decision.rules_evaluated
    assert decision.validated_order_params is not None
    # (6 - 0.35) / (3 + 0.35) = 1.68656... truncated to 4 dp.
    assert decision.validated_order_params["reward_risk_net"] == "1.6865"


# --- Weekly / monthly loss --------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "rule", "ok", "bad"),
    [
        ("weekly_loss", "RP-WEEKLY-LOSS-LIMIT", "50", "50.01"),
        ("monthly_loss", "RP-MONTHLY-LOSS-LIMIT", "100", "100.01"),
    ],
)
def test_period_loss_limits_pass_fail_and_missing(
    field: str, rule: str, ok: str, bad: str
) -> None:
    at_limit = _evaluate(context=_context(**{field: Decimal(ok)}))
    assert at_limit.decision == RiskDecisionType.APPROVE, at_limit.rejection_reasons

    over = _evaluate(context=_context(**{field: Decimal(bad)}))
    assert over.decision == RiskDecisionType.REJECT
    assert _ids(over) == {rule}

    missing = _evaluate(context=_context(**{field: None}))
    assert missing.decision == RiskDecisionType.REJECT
    assert _ids(missing) == {rule}
    assert "missing" in _detail(missing, rule)


def test_period_loss_rules_not_enforced_without_param() -> None:
    v1 = owner_approved_policy_v1()
    decision = evaluate_risk(
        _proposal(method="fixed_notional", notional="2", stop_loss=Decimal("96")),
        _context(
            proposed_notional=Decimal("2"),
            weekly_loss=None,
            monthly_loss=None,
        ),
        _settings(risk_policy_version="1.0"),
        policy=v1,
    )
    assert decision.decision == RiskDecisionType.APPROVE, decision.rejection_reasons
    assert "RP-WEEKLY-LOSS-LIMIT" not in decision.rules_evaluated
    assert "RP-MIN-REWARD-RISK" not in decision.rules_evaluated
    assert "reward_risk_net" not in (decision.validated_order_params or {})


# --- Period lock ------------------------------------------------------------------


def test_period_lock_rejects_until_expiry() -> None:
    locked = _evaluate(context=_context(period_lock_until=_now() + timedelta(hours=1)))
    assert locked.decision == RiskDecisionType.REJECT
    assert _ids(locked) == {"RP-PERIOD-LOCK"}

    at_expiry = _evaluate(context=_context(period_lock_until=_now()))
    assert at_expiry.decision == RiskDecisionType.APPROVE

    expired = _evaluate(context=_context(period_lock_until=_now() - timedelta(hours=1)))
    assert expired.decision == RiskDecisionType.APPROVE


def test_period_lock_enforced_under_v1_0_policy() -> None:
    decision = evaluate_risk(
        _proposal(method="fixed_notional", notional="2", stop_loss=Decimal("96")),
        _context(
            proposed_notional=Decimal("2"),
            period_lock_until=_now() + timedelta(days=3),
        ),
        _settings(risk_policy_version="1.0"),
        policy=owner_approved_policy_v1(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert _ids(decision) == {"RP-PERIOD-LOCK"}


def test_period_lock_naive_datetime_fails_closed() -> None:
    naive = datetime(2026, 10, 14, 11, 0)  # noqa: DTZ001 — deliberately naive
    decision = _evaluate(context=_context(period_lock_until=naive))
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-PERIOD-LOCK" in _ids(decision)


# --- Take-profit required ---------------------------------------------------------


def test_take_profit_required_rejects_missing_tp() -> None:
    decision = _evaluate(proposal=_proposal(take_profit=None))
    assert decision.decision == RiskDecisionType.REJECT
    ids = _ids(decision)
    assert "RP-TAKE-PROFIT-POLICY" in ids
    assert "RP-MIN-REWARD-RISK" in ids


@pytest.mark.parametrize("value", [True, "required", "REQUIRED"])
def test_take_profit_policy_required_values(value: object) -> None:
    policy = with_approved_params(
        _policy(), {"RP-TAKE-PROFIT-POLICY": value}, policy_version="1.1-test"
    )
    decision = _evaluate(proposal=_proposal(take_profit=None), policy=policy)
    assert "RP-TAKE-PROFIT-POLICY" in _ids(decision)


def test_take_profit_policy_optional_allows_missing_tp() -> None:
    policy = owner_approved_policy_v1()
    decision = evaluate_risk(
        _proposal(
            method="fixed_notional", notional="2", stop_loss=Decimal("96"), take_profit=None
        ),
        _context(proposed_notional=Decimal("2")),
        _settings(risk_policy_version="1.0"),
        policy=policy,
    )
    assert decision.decision == RiskDecisionType.APPROVE, decision.rejection_reasons


# --- Minimum net reward:risk ------------------------------------------------------


def test_reward_risk_below_min_rejects_with_r_net_detail() -> None:
    # (4 - 0.35) / 3.35 = 1.0895...
    decision = _evaluate(proposal=_proposal(take_profit=Decimal("104")))
    assert decision.decision == RiskDecisionType.REJECT
    assert _ids(decision) == {"RP-MIN-REWARD-RISK"}
    detail = _detail(decision, "RP-MIN-REWARD-RISK")
    assert "reward_risk_net=1.0895" in detail
    assert "min=1.5" in detail


def test_reward_risk_costs_turn_gross_pass_into_net_fail() -> None:
    # Gross 4.6/3 = 1.533 >= 1.5; net (4.6-0.35)/(3+0.35) = 1.2686 < 1.5.
    proposal = _proposal(take_profit=Decimal("104.6"))
    with_costs = _evaluate(proposal=proposal)
    assert with_costs.decision == RiskDecisionType.REJECT
    assert _ids(with_costs) == {"RP-MIN-REWARD-RISK"}
    no_costs = _evaluate(
        proposal=proposal,
        context=_context(
            fee_estimate=Decimal("0"), spread=Decimal("0"), slippage_model_bps=Decimal("0")
        ),
    )
    assert no_costs.decision == RiskDecisionType.APPROVE, no_costs.rejection_reasons


def test_reward_risk_exactly_at_min_passes() -> None:
    decision = _evaluate(
        proposal=_proposal(stop_loss=Decimal("98"), take_profit=Decimal("103")),
        context=_context(
            fee_estimate=Decimal("0"), spread=Decimal("0"), slippage_model_bps=Decimal("0")
        ),
    )
    assert decision.decision == RiskDecisionType.APPROVE, decision.rejection_reasons
    assert decision.validated_order_params["reward_risk_net"] == "1.5000"  # type: ignore[index]


def test_reward_risk_short_side_geometry() -> None:
    policy = with_approved_params(
        _policy(),
        {"RP-DIRECTIONS-ALLOWED": {"spot": ["long", "short"]}},
        policy_version="1.1-test",
    )
    decision = _evaluate(
        proposal=_proposal(
            action=ProposalAction.SELL,
            direction=TradeDirection.SHORT,
            entry="100",
            stop_loss=Decimal("103"),
            take_profit=Decimal("94"),
        ),
        policy=policy,
    )
    assert decision.decision == RiskDecisionType.APPROVE, decision.rejection_reasons
    assert decision.validated_order_params["reward_risk_net"] == "1.6865"  # type: ignore[index]


@pytest.mark.parametrize("missing", ["spread", "fee_estimate", "slippage_model_bps"])
def test_reward_risk_missing_cost_input_fails_closed(missing: str) -> None:
    decision = _evaluate(context=_context(**{missing: None}))
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-MIN-REWARD-RISK" in _ids(decision)
    assert missing in _detail(decision, "RP-MIN-REWARD-RISK")


def test_reward_risk_negative_cost_input_fails_closed() -> None:
    decision = _evaluate(context=_context(fee_estimate=Decimal("-50")))
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-MIN-REWARD-RISK" in _ids(decision)


def test_reward_risk_missing_entry_fails_closed() -> None:
    proposal = _proposal()
    proposal = proposal.model_copy(
        update={"entry_conditions": {"order_type": "LIMIT", "time_in_force": "GTC"}}
    )
    decision = _evaluate(proposal=proposal)
    assert "RP-MIN-REWARD-RISK" in _ids(decision)


# --- Max risk per trade -----------------------------------------------------------


def _risk_policy() -> RiskPolicy:
    # Budget = 0.5% of 1000 = 5 USDT.
    return _policy(risk_per_trade_pct=Decimal("0.5"))


def test_risk_per_trade_within_budget_approves() -> None:
    # Loss = 100/100 * (3 + 0.35) = 3.35 <= 5.
    decision = _evaluate(proposal=_proposal(method="risk_pct_capital"), policy=_risk_policy())
    assert decision.decision == RiskDecisionType.APPROVE, decision.rejection_reasons
    assert "RP-MAX-RISK-PER-TRADE" in decision.rules_evaluated


def test_risk_per_trade_over_budget_rejects() -> None:
    # SL 95 (5% = v1 max stop distance), TP 110 keeps R_net >= 1.5.
    # Loss = 100/100 * (5 + 0.35) = 5.35 > 5.
    decision = _evaluate(
        proposal=_proposal(
            method="risk_pct_capital", stop_loss=Decimal("95"), take_profit=Decimal("110")
        ),
        policy=_risk_policy(),
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert _ids(decision) == {"RP-MAX-RISK-PER-TRADE"}
    assert "5.35" in _detail(decision, "RP-MAX-RISK-PER-TRADE")


def test_risk_per_trade_costs_count_toward_loss() -> None:
    # Without costs: 100/100 * 5 = 5.00 <= 5 passes; with 35 bps costs it fails.
    proposal = _proposal(
        method="risk_pct_capital", stop_loss=Decimal("95"), take_profit=Decimal("110")
    )
    no_costs = _evaluate(
        proposal=proposal,
        context=_context(
            fee_estimate=Decimal("0"), spread=Decimal("0"), slippage_model_bps=Decimal("0")
        ),
        policy=_risk_policy(),
    )
    assert no_costs.decision == RiskDecisionType.APPROVE, no_costs.rejection_reasons


def test_risk_per_trade_missing_inputs_fail_closed() -> None:
    no_notional = _evaluate(
        proposal=_proposal(method="risk_pct_capital"),
        context=_context(proposed_notional=None),
        policy=_risk_policy(),
    )
    assert "RP-MAX-RISK-PER-TRADE" in _ids(no_notional)

    no_stop = _evaluate(
        proposal=_proposal(method="risk_pct_capital", stop_loss=None),
        policy=_risk_policy(),
    )
    assert "RP-MAX-RISK-PER-TRADE" in _ids(no_stop)


def test_sizing_method_mismatch_rejects_under_v1_1() -> None:
    decision = _evaluate(proposal=_proposal(method="fixed_notional"))
    assert _ids(decision) == {"RP-SIZING-METHOD"}


def test_draft_policy_still_rejects_and_has_no_optional_rules() -> None:
    decision = evaluate_risk(
        _proposal(), _context(), _settings(), policy=default_draft_policy()
    )
    assert decision.decision == RiskDecisionType.REJECT
    assert "RP-MIN-REWARD-RISK" not in decision.rules_evaluated


# --- Aggregate cap bounded by capital base (H2) -------------------------------------


@pytest.mark.parametrize(
    ("position_pct", "expected"),
    [
        ("10", "200"),  # 2 x 100 = 200 < 1000
        ("50", "1000"),  # 2 x 500 = 1000 == capital
        ("80", "1000"),  # 2 x 800 = 1600 -> capped at 100% of capital
        ("100", "1000"),  # 2 x 1000 = 2000 -> capped
    ],
)
def test_aggregate_cap_bounded_by_capital(position_pct: str, expected: str) -> None:
    policy = _policy(risk_position_pct=Decimal(position_pct))
    assert _value(policy, "RP-MAX-AGGREGATE-NOTIONAL") == Decimal(expected)
    # Per-order / per-instrument caps are not affected by the aggregate bound.
    assert _value(policy, "RP-MAX-NOTIONAL-PER-ORDER") == Decimal(position_pct) * 10


def test_aggregate_cap_rejects_second_position_beyond_capital() -> None:
    # Position 80% => per-order 800, aggregate min(1600, 1000) = 1000.
    policy = _policy(risk_position_pct=Decimal("80"))
    ok = _evaluate(
        proposal=_proposal(notional="800"),
        context=_context(proposed_notional=Decimal("200"), aggregate_notional=Decimal("800")),
        policy=policy,
    )
    assert "RP-MAX-AGGREGATE-NOTIONAL" not in _ids(ok)
    over = _evaluate(
        proposal=_proposal(notional="800"),
        context=_context(
            proposed_notional=Decimal("800"),
            aggregate_notional=Decimal("800"),
            open_positions_count=1,
        ),
        policy=policy,
    )
    assert over.decision == RiskDecisionType.REJECT
    assert "RP-MAX-AGGREGATE-NOTIONAL" in _ids(over)


# --- Env-derived policy is paper-only (H2) -----------------------------------------


def _live_settings() -> Settings:
    return _settings(trading_mode=TradingMode.LIVE, live_armed=True, kill_switch=False)


def test_live_mode_env_policy_rejects_with_env_policy_live_rule() -> None:
    policy = _policy()
    assert policy.policy_version.startswith("1.1-env+")
    # Sanity: the identical proposal/context approves in paper.
    paper = _evaluate(policy=policy)
    assert paper.decision == RiskDecisionType.APPROVE, paper.rejection_reasons

    live = evaluate_risk(_proposal(), _context(), _live_settings(), policy=policy)
    assert live.decision == RiskDecisionType.REJECT
    # Only the new structural rule fails: every other gate would approve.
    assert _ids(live) == {"RP-ENV-POLICY-LIVE"}
    assert "paper-only" in _detail(live, "RP-ENV-POLICY-LIVE")
    assert live.validated_order_params is None


def test_live_mode_env_policy_rejects_with_per_trade_sizing() -> None:
    policy = _risk_policy()
    live = evaluate_risk(
        _proposal(method="risk_pct_capital"), _context(), _live_settings(), policy=policy
    )
    assert live.decision == RiskDecisionType.REJECT
    assert _ids(live) == {"RP-ENV-POLICY-LIVE"}


def test_paper_mode_env_policy_unaffected_by_env_policy_live_rule() -> None:
    decision = _evaluate()
    assert decision.decision == RiskDecisionType.APPROVE, decision.rejection_reasons
    assert "RP-ENV-POLICY-LIVE" in decision.rules_evaluated
    assert "RP-ENV-POLICY-LIVE" not in _ids(decision)


def test_live_mode_v1_0_policy_not_blocked_by_env_policy_live_rule() -> None:
    decision = evaluate_risk(
        _proposal(method="fixed_notional", notional="2", stop_loss=Decimal("96")),
        _context(proposed_notional=Decimal("2")),
        _settings(
            risk_policy_version="1.0",
            trading_mode=TradingMode.LIVE,
            live_armed=True,
            kill_switch=False,
        ),
        policy=owner_approved_policy_v1(),
    )
    assert "RP-ENV-POLICY-LIVE" not in _ids(decision)
