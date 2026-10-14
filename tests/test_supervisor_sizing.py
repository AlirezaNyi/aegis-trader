"""Policy 1.1 proposal sizing (pct / risk-based) and reward:risk prompt hints."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from aegis.schemas.common import EvidenceStatus, MarketType, Timeframe
from aegis.schemas.evidence import AnalystEvidence, AnalystType, EvidencePackage, JevResult
from aegis.schemas.market import InstrumentRef
from aegis.schemas.proposal import ProposalAction
from aegis.supervisor.budgets import BudgetConfig
from aegis.supervisor.decision import (
    ProposalDefaults,
    SupervisorDecision,
    pct_capital_notional,
    proposal_from_decision,
    risk_sized_notional,
)
from aegis.supervisor.prompt import build_supervisor_prompt
from aegis.supervisor.supervise import supervise

_ENTRY_CONDITIONS = {"order_type": "LIMIT", "time_in_force": "GTC"}


def _now() -> datetime:
    return datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _package() -> EvidencePackage:
    now = _now()
    return EvidencePackage(
        package_id=uuid4(),
        correlation_id="corr-sizing",
        created_at=now,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT"),
        market_type=MarketType.SPOT,
        timeframe=Timeframe.M15,
        as_of=now,
        feature_refs=["f1"],
        analyst_evidence=[
            AnalystEvidence(
                analyst_type=AnalystType.TECHNICAL,
                status=EvidenceStatus.OK,
                payload={"trend": "up"},
                evidence_time=now,
            ),
            AnalystEvidence(
                analyst_type=AnalystType.QUANTITATIVE,
                status=EvidenceStatus.OK,
                payload={"mean_simple_return": "0.01", "not_a_profitability_claim": True},
                evidence_time=now,
            ),
        ],
    )


def _jev() -> JevResult:
    return JevResult(
        evidence_package_id=uuid4(),
        model="jev-1.13.0",
        status=EvidenceStatus.OK,
        answers={
            "alignment": {"choice": "aligned_long", "confidence": 0.5},
            "evidence_adequacy": {"score": 4},
            "summary": {"text": "ok"},
        },
        usage={},
        latency_ms=1,
    )


def _decision(**overrides: Any) -> SupervisorDecision:
    base: dict[str, Any] = {
        "action": "BUY",
        "direction": "long",
        "entry_price": "100",
        "stop_loss": "97",
        "take_profit": "106",
    }
    base.update(overrides)
    return SupervisorDecision.model_validate(base)


def _defaults(method: str, **overrides: Any) -> ProposalDefaults:
    base: dict[str, Any] = {
        "entry_conditions": _ENTRY_CONDITIONS,
        "sizing": {"method": method},
        "position_cap_usdt": Decimal("100"),
        "risk_budget_usdt": Decimal("2"),
        "cost_bps_round_trip": Decimal("35"),
        "min_reward_risk": Decimal("1.5"),
    }
    base.update(overrides)
    return ProposalDefaults(**base)


def _sizing(decision: SupervisorDecision, defaults: ProposalDefaults) -> dict[str, Any]:
    proposal = proposal_from_decision(
        decision,
        _package(),
        _jev(),
        defaults=defaults,
        strategy_id="aegis-default",
        strategy_version="0.1.0",
        now=_now(),
    )
    return dict(proposal.sizing)


# --- risk_sized_notional math -----------------------------------------------------


def test_risk_sized_notional_rounds_down_to_cents() -> None:
    # Cu = 100 * 35 / 10000 = 0.35; 2 * 100 / 3.35 = 59.7014... → 59.70.
    assert risk_sized_notional(
        entry_price=Decimal("100"),
        stop_loss=Decimal("97"),
        position_cap_usdt=Decimal("100"),
        risk_budget_usdt=Decimal("2"),
        cost_bps_round_trip=Decimal("35"),
    ) == Decimal("59.70")
    # 1 * 100 / 3.35 = 29.8507... → 29.85 (never rounded up to 29.86).
    assert risk_sized_notional(
        entry_price=Decimal("100"),
        stop_loss=Decimal("97"),
        position_cap_usdt=Decimal("100"),
        risk_budget_usdt=Decimal("1"),
        cost_bps_round_trip=Decimal("35"),
    ) == Decimal("29.85")


def test_risk_sized_notional_capped_by_position_cap() -> None:
    # 5 * 100 / 3.35 = 149.25 > cap 33.333 → 33.33.
    assert risk_sized_notional(
        entry_price=Decimal("100"),
        stop_loss=Decimal("97"),
        position_cap_usdt=Decimal("33.333"),
        risk_budget_usdt=Decimal("5"),
        cost_bps_round_trip=Decimal("35"),
    ) == Decimal("33.33")


def test_risk_sized_notional_short_uses_absolute_distance() -> None:
    assert risk_sized_notional(
        entry_price=Decimal("100"),
        stop_loss=Decimal("103"),
        position_cap_usdt=Decimal("100"),
        risk_budget_usdt=Decimal("2"),
        cost_bps_round_trip=Decimal("35"),
    ) == Decimal("59.70")


def test_risk_sized_implied_loss_never_exceeds_budget() -> None:
    entry, stop = Decimal("2.3456"), Decimal("2.2001")
    budget, cost_bps = Decimal("3.7"), Decimal("41")
    notional = risk_sized_notional(
        entry_price=entry,
        stop_loss=stop,
        position_cap_usdt=Decimal("1000"),
        risk_budget_usdt=budget,
        cost_bps_round_trip=cost_bps,
    )
    assert notional is not None
    cost_per_unit = entry * cost_bps / Decimal("10000")
    implied_loss = notional / entry * (abs(entry - stop) + cost_per_unit)
    assert implied_loss <= budget


@pytest.mark.parametrize(
    "overrides",
    [
        {"entry_price": None},
        {"stop_loss": None},
        {"position_cap_usdt": None},
        {"risk_budget_usdt": None},
        {"cost_bps_round_trip": None},
        {"cost_bps_round_trip": Decimal("-1")},
        {"risk_budget_usdt": Decimal("0")},
        {"position_cap_usdt": Decimal("-5")},
        # Rounds down to 0.00 ⇒ no notional.
        {"risk_budget_usdt": Decimal("0.0001")},
    ],
)
def test_risk_sized_notional_missing_or_invalid_inputs_return_none(
    overrides: dict[str, Any],
) -> None:
    kwargs: dict[str, Any] = {
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("97"),
        "position_cap_usdt": Decimal("100"),
        "risk_budget_usdt": Decimal("2"),
        "cost_bps_round_trip": Decimal("35"),
    }
    kwargs.update(overrides)
    assert risk_sized_notional(**kwargs) is None


def test_pct_capital_notional_rounds_down() -> None:
    assert pct_capital_notional(Decimal("33.339")) == Decimal("33.33")
    assert pct_capital_notional(None) is None
    assert pct_capital_notional(Decimal("0")) is None


# --- proposal_from_decision sizing ------------------------------------------------


def test_proposal_risk_pct_capital_sizing() -> None:
    sizing = _sizing(_decision(), _defaults("risk_pct_capital"))
    assert sizing == {"method": "risk_pct_capital", "notional": "59.70"}


def test_proposal_pct_capital_sizing() -> None:
    sizing = _sizing(_decision(), _defaults("pct_capital", position_cap_usdt=Decimal("33.339")))
    assert sizing == {"method": "pct_capital", "notional": "33.33"}


def test_proposal_risk_sizing_without_stop_leaves_notional_unset() -> None:
    sizing = _sizing(_decision(stop_loss=None), _defaults("risk_pct_capital"))
    assert sizing == {"method": "risk_pct_capital"}


def test_proposal_risk_sizing_drops_stale_default_notional() -> None:
    defaults = _defaults("risk_pct_capital", cost_bps_round_trip=None)
    defaults = ProposalDefaults(
        entry_conditions=defaults.entry_conditions,
        sizing={"method": "risk_pct_capital", "notional": "999"},
        position_cap_usdt=defaults.position_cap_usdt,
        risk_budget_usdt=defaults.risk_budget_usdt,
        cost_bps_round_trip=None,
    )
    assert _sizing(_decision(), defaults) == {"method": "risk_pct_capital"}


def test_proposal_fixed_notional_unchanged() -> None:
    defaults = ProposalDefaults(
        entry_conditions=_ENTRY_CONDITIONS,
        sizing={"method": "fixed_notional", "notional": "2"},
        position_cap_usdt=Decimal("100"),
        risk_budget_usdt=Decimal("2"),
        cost_bps_round_trip=Decimal("35"),
    )
    assert _sizing(_decision(), defaults) == {"method": "fixed_notional", "notional": "2"}


def test_proposal_hold_has_no_sizing() -> None:
    sizing = _sizing(
        _decision(action="HOLD", direction="flat"), _defaults("risk_pct_capital")
    )
    assert sizing == {}


def test_model_supplied_sizing_keys_are_ignored() -> None:
    decision = SupervisorDecision.model_validate(
        {
            "action": "BUY",
            "direction": "long",
            "entry_price": "100",
            "stop_loss": "97",
            "take_profit": "106",
            "sizing": {"notional": "99999"},
            "risk_budget_usdt": "1000",
        }
    )
    sizing = _sizing(decision, _defaults("risk_pct_capital"))
    assert sizing == {"method": "risk_pct_capital", "notional": "59.70"}


# --- Prompt hints -----------------------------------------------------------------


def test_prompt_requires_take_profit_and_states_net_reward_risk() -> None:
    prompt = build_supervisor_prompt(
        _package(),
        _jev(),
        min_reward_risk=Decimal("1.50"),
        cost_bps_round_trip=Decimal("35.0"),
    )
    assert "take_profit: target price as a decimal string; REQUIRED for BUY/SELL" in prompt
    assert "take_profit: optional" not in prompt
    assert "~35 bps round-trip costs" in prompt
    assert ">= 1.5" in prompt
    assert "return NO_TRADE" in prompt


def test_prompt_without_min_reward_risk_keeps_optional_take_profit() -> None:
    prompt = build_supervisor_prompt(_package(), _jev())
    assert "- take_profit: optional decimal string, or null." in prompt
    assert "reward:risk" not in prompt


def test_prompt_min_reward_risk_without_cost() -> None:
    prompt = build_supervisor_prompt(_package(), _jev(), min_reward_risk=Decimal("2"))
    assert "REQUIRED for BUY/SELL" in prompt
    assert "must be >= 2" in prompt
    assert "bps" not in prompt.split("### DATA")[0]


class _CapturingLlm:
    def __init__(self) -> None:
        self.prompt = ""

    async def complete_structured(
        self, *, prompt: str, schema_name: str, timeout_seconds: float
    ) -> dict[str, Any]:
        _ = schema_name, timeout_seconds
        self.prompt = prompt
        return {
            "action": "BUY",
            "direction": "long",
            "entry_price": "100",
            "stop_loss": "97",
            "take_profit": "106",
            "_meta": {"total_tokens": 10, "cost": "0"},
        }


async def test_supervise_passes_reward_risk_defaults_to_prompt_and_sizes() -> None:
    llm = _CapturingLlm()
    result = await supervise(
        _package(),
        _jev(),
        llm,
        budgets=BudgetConfig(
            token_budget=2000, cost_budget=Decimal("1"), latency_budget_ms=5000
        ),
        defaults=_defaults("risk_pct_capital"),
        clock=_now,
    )
    assert "~35 bps round-trip costs" in llm.prompt
    assert ">= 1.5" in llm.prompt
    assert result.action == ProposalAction.BUY
    assert result.sizing == {"method": "risk_pct_capital", "notional": "59.70"}
