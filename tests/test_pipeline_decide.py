"""Decision source wiring: llm | rules | rules+llm (LLM may only veto)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aegis.config.settings import Settings, TradingMode
from aegis.pipeline.cycle import run_paper_cycle
from aegis.pipeline.decide import RuleParamsUnavailable, load_rule_params
from aegis.schemas.common import Timeframe
from aegis.schemas.proposal import ProposalAction
from aegis.schemas.risk import RiskDecisionType
from aegis.strategy.rules import RuleParams
from aegis.supervisor.budgets import BudgetConfig
from test_paper_cycle import (  # type: ignore[import-not-found]
    CountingLlm,
    _book,
    _deps,
    _instrument,
    _uptrend_series,
)

_LOOSE = {
    "k_atr_stop": "1",
    "r_target": "2",
    "rsi_min": "0",
    "rsi_max": "100",
    "atr_pct_min": "0",
    "atr_pct_max": "100",
    "volume_ratio_min": "0",
}
_BUDGETS = BudgetConfig(token_budget=2000, cost_budget=Decimal("1"), latency_budget_ms=5000)


def _params() -> RuleParams:
    return RuleParams.from_dict(_LOOSE)


async def _run(source: str, llm: Any, params: RuleParams | None) -> Any:
    start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    candles = _uptrend_series(45, start)
    now = candles[-1].received_at + timedelta(milliseconds=100)
    deps = dataclasses.replace(
        _deps(llm=llm, budgets=_BUDGETS), decision_source=source, rule_params=params
    )
    return await run_paper_cycle(
        candles,
        deps=deps,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        now=now,
        book_top=_book(candles[-1]),
    )


async def test_rules_mode_trades_without_llm() -> None:
    llm = CountingLlm({"action": "NO_TRADE", "_meta": {"total_tokens": 1, "cost": "0"}})
    result = await _run("rules", llm, _params())
    assert llm.calls == 0
    assert result.proposal is not None
    assert result.proposal.action == ProposalAction.BUY
    assert result.proposal.supervisor_model_meta["decision_source"] == "rules"
    assert result.decision is not None
    assert result.decision.decision == RiskDecisionType.APPROVE, (
        result.decision.rejection_reasons
    )
    assert result.order is not None


async def test_rules_mode_without_params_is_no_trade() -> None:
    llm = CountingLlm({"action": "BUY"})
    result = await _run("rules", llm, None)
    assert llm.calls == 0
    assert result.proposal is not None
    assert result.proposal.action == ProposalAction.NO_TRADE
    assert "rule_params_unavailable" in result.proposal.uncertainty["reasons"]
    assert result.order is None


async def test_rules_plus_llm_veto_blocks_trade() -> None:
    llm = CountingLlm(
        {"action": "NO_TRADE", "direction": "flat", "_meta": {"total_tokens": 1, "cost": "0"}}
    )
    result = await _run("rules+llm", llm, _params())
    assert llm.calls == 1
    assert result.proposal is not None
    assert result.proposal.action == ProposalAction.NO_TRADE
    assert result.proposal.supervisor_model_meta["llm_veto"] is True
    assert result.order is None


async def test_rules_plus_llm_agreement_keeps_rule_levels() -> None:
    llm = CountingLlm(
        {
            "action": "BUY",
            "direction": "long",
            "entry_price": "1",
            "stop_loss": "0.5",
            "take_profit": "9",
            "_meta": {"total_tokens": 1, "cost": "0"},
        }
    )
    result = await _run("rules+llm", llm, _params())
    assert llm.calls == 1
    assert result.proposal is not None
    assert result.proposal.action == ProposalAction.BUY
    assert result.proposal.supervisor_model_meta["llm_veto"] is False
    # Levels come from the rules, never from the LLM.
    assert result.proposal.stop_loss != Decimal("0.5")
    assert result.proposal.take_profit != Decimal("9")


def test_load_rule_params_file_formats(tmp_path: Path) -> None:
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(_LOOSE))
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"params": _LOOSE, "fold": 3}))
    assert load_rule_params(str(plain)) == load_rule_params(str(wrapped))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(RuleParamsUnavailable):
        load_rule_params(str(bad))
    with pytest.raises(RuleParamsUnavailable):
        load_rule_params(None)
    with pytest.raises(RuleParamsUnavailable):
        load_rule_params(str(tmp_path / "missing.json"))


def test_decision_source_setting_validation() -> None:
    from pydantic import ValidationError

    base = {"trading_mode": TradingMode.PAPER, "require_database": False}
    assert Settings(**base, decision_source="RULES+LLM").decision_source == "rules+llm"
    assert Settings(**base, decision_source="").decision_source == "llm"
    with pytest.raises(ValidationError):
        Settings(**base, decision_source="magic")


async def test_rules_plus_llm_cannot_replace_trade_with_its_own() -> None:
    """LLM saying SELL on a rule BUY must veto (NO_TRADE), never become a SELL."""
    llm = CountingLlm(
        {
            "action": "SELL",
            "direction": "short",
            "entry_price": "1",
            "stop_loss": "2",
            "take_profit": "0.5",
            "_meta": {"total_tokens": 1, "cost": "0"},
        }
    )
    result = await _run("rules+llm", llm, _params())
    assert llm.calls == 1
    assert result.proposal is not None
    assert result.proposal.action == ProposalAction.NO_TRADE
    assert result.proposal.supervisor_model_meta["llm_action"] == "SELL"
    assert "llm_veto" in result.proposal.uncertainty["reasons"]
    assert result.order is None
