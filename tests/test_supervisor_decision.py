"""Compact SupervisorDecision contract and policy-derived proposal defaults."""

from __future__ import annotations

from datetime import timedelta

from aegis.pipeline.audit import DecisionAuditStore
from aegis.pipeline.defaults import proposal_defaults_from_policy
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.risk.policy import default_draft_policy
from aegis.supervisor.decision import is_compact_decision


def test_defaults_from_owner_policy_v1() -> None:
    defaults = proposal_defaults_from_policy(owner_approved_policy_v1())
    assert defaults.entry_conditions == {"order_type": "LIMIT", "time_in_force": "GTC"}
    assert defaults.sizing == {"method": "fixed_notional", "notional": "2"}
    assert defaults.ttl == timedelta(milliseconds=30_000)


def test_defaults_from_draft_policy_are_empty() -> None:
    defaults = proposal_defaults_from_policy(default_draft_policy())
    assert defaults.entry_conditions == {}
    assert defaults.sizing == {}


def test_compact_detection() -> None:
    assert is_compact_decision({"action": "NO_TRADE"})
    assert not is_compact_decision({"proposal_id": "x", "action": "BUY"})
    assert not is_compact_decision(["action"])


def test_audit_store_without_database_is_disabled() -> None:
    store = DecisionAuditStore(database_url=None)
    assert store.enabled is False



def test_defaults_from_env_policy_v1_1_risk_sizing() -> None:
    from decimal import Decimal

    from aegis.config.settings import Settings, TradingMode
    from aegis.risk.owner_v1_1 import missing_policy_env, owner_env_policy_v1_1

    settings = Settings(
        trading_mode=TradingMode.PAPER,
        require_database=False,
        risk_policy_version="1.1",
        paper_initial_balance="100",
        risk_position_pct="20",
        risk_per_trade_pct="1",
        risk_daily_loss_pct="3",
        risk_weekly_loss_pct="6",
        risk_monthly_loss_pct="10",
        risk_max_drawdown_pct="15",
        risk_min_reward_risk="1.5",
    )
    assert missing_policy_env(settings) == []
    policy = owner_env_policy_v1_1(settings)
    defaults = proposal_defaults_from_policy(
        policy,
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
        spread_bps=Decimal("4"),
    )
    assert defaults.sizing == {"method": "risk_pct_capital"}
    assert defaults.position_cap_usdt == Decimal("20")
    assert defaults.risk_budget_usdt == Decimal("1")
    assert defaults.cost_bps_round_trip == Decimal("34")
    assert defaults.min_reward_risk == Decimal("1.5")


def test_defaults_missing_spread_leaves_cost_unset() -> None:
    defaults = proposal_defaults_from_policy(owner_approved_policy_v1())
    assert defaults.cost_bps_round_trip is None
    assert defaults.sizing == {"method": "fixed_notional", "notional": "2"}
