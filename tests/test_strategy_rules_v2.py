"""rules-v2 (daily regime + intraday pullback) decider and dispatcher tests."""

from __future__ import annotations

import ast
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from aegis.schemas.common import EvidenceStatus, MarketType, Timeframe
from aegis.schemas.evidence import (
    AnalystEvidence,
    AnalystType,
    EvidencePackage,
    HigherTimeframeContext,
    MarketContext,
)
from aegis.schemas.market import InstrumentRef
from aegis.schemas.proposal import ProposalAction, TradeDirection
from aegis.strategy import (
    BUY_RATIONALE_V2,
    NO_TRADE_PREFIX,
    STRATEGY_VERSION,
    STRATEGY_VERSION_V2,
    NoTradeReasonV2,
    RuleParams,
    RuleParamsV2,
    decide,
    decide_any,
    decide_v2,
    max_hold_bars_of,
    params_from_dict,
    strategy_version_of,
)

AS_OF = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
HTF_AS_OF = datetime(2026, 9, 30, 23, 59, 59, 999000, tzinfo=UTC)


def _params(**overrides: Any) -> RuleParamsV2:
    base: dict[str, Any] = {
        "regime_n": 50,
        "rsi_entry_max": Decimal("40"),
        "k_atr_stop": Decimal("1.5"),
        "r_target": Decimal("2.5"),
        "max_hold_bars": 16,
    }
    base.update(overrides)
    return RuleParamsV2(**base)


def _v1_params() -> RuleParams:
    return RuleParams(
        k_atr_stop=Decimal("1.5"),
        r_target=Decimal("2"),
        rsi_min=Decimal("40"),
        rsi_max=Decimal("70"),
        atr_pct_min=Decimal("0.5"),
        atr_pct_max=Decimal("5"),
        volume_ratio_min=Decimal("1"),
    )


def _technical(*, status: EvidenceStatus = EvidenceStatus.OK, **payload: Any) -> AnalystEvidence:
    data: dict[str, Any] = {"trend": "down", "rsi_14": "35", "volume_ratio": "0.5"}
    data.update(payload)
    return AnalystEvidence(
        analyst_type=AnalystType.TECHNICAL, status=status, payload=data, evidence_time=AS_OF
    )


def _context(**overrides: Any) -> MarketContext:
    data: dict[str, Any] = {
        "last_close": Decimal("100"),
        "recent_high": Decimal("105"),
        "recent_low": Decimal("95"),
        "lookback_candles": 20,
        "atr_14": Decimal("2"),
        "atr_pct_of_close": Decimal("2"),
        "as_of": AS_OF,
    }
    data.update(overrides)
    return MarketContext(**data)


def _htf(**overrides: Any) -> HigherTimeframeContext:
    data: dict[str, Any] = {
        "timeframe": Timeframe.D1,
        "as_of": HTF_AS_OF,
        "last_close": Decimal("110"),
        "sma": {
            "20": Decimal("105"),
            "50": Decimal("100"),
            "100": Decimal("120"),
            "200": None,
        },
        "bars_available": 150,
    }
    data.update(overrides)
    return HigherTimeframeContext(**data)


_UNSET: Any = object()


def _package(
    *,
    evidence: list[AnalystEvidence] | None = None,
    context: Any = _UNSET,
    htf: Any = _UNSET,
) -> EvidencePackage:
    return EvidencePackage(
        package_id=UUID("00000000-0000-0000-0000-000000000021"),
        correlation_id="corr-v2",
        created_at=AS_OF,
        instrument=InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT"),
        market_type=MarketType.SPOT,
        timeframe=Timeframe.M15,
        as_of=AS_OF,
        analyst_evidence=evidence if evidence is not None else [_technical()],
        market_context=_context() if context is _UNSET else context,
        htf_context=_htf() if htf is _UNSET else htf,
    )


def _reason(decision: Any) -> str:
    assert decision.action == ProposalAction.NO_TRADE
    assert decision.direction == TradeDirection.FLAT
    assert decision.entry_price is None
    assert decision.stop_loss is None
    assert decision.take_profit is None
    assert decision.rationale.startswith(NO_TRADE_PREFIX)
    return str(decision.rationale[len(NO_TRADE_PREFIX) :])


# --- BUY path and level math ------------------------------------------------


def test_buy_when_regime_up_pullback_and_atr_present() -> None:
    decision = decide_v2(_package(), _params())
    assert decision.action == ProposalAction.BUY
    assert decision.direction == TradeDirection.LONG
    assert decision.entry_price == Decimal("100")
    # stop = 100 - 1.5*2 = 97 ; tp = 100 + 2.5*3 = 107.5
    assert decision.stop_loss == Decimal("97")
    assert decision.take_profit == Decimal("107.5")
    assert decision.rationale.startswith(BUY_RATIONALE_V2)
    assert "uncalibrated" in decision.confidence_note


@pytest.mark.parametrize(
    ("k", "r", "atr", "close", "stop", "tp"),
    [
        ("1", "1", "2", "100", "98", "102"),
        ("2.5", "1.5", "0.4", "50", "49.0", "51.50"),
        ("1.5", "2.5", "12.5", "2500.5", "2481.75", "2547.375"),
    ],
)
def test_level_formula(k: str, r: str, atr: str, close: str, stop: str, tp: str) -> None:
    decision = decide_v2(
        _package(context=_context(last_close=Decimal(close), atr_14=Decimal(atr))),
        _params(k_atr_stop=Decimal(k), r_target=Decimal(r)),
    )
    assert decision.action == ProposalAction.BUY
    assert decision.entry_price == Decimal(close)
    assert decision.stop_loss == Decimal(stop)
    assert decision.take_profit == Decimal(tp)


def test_v1_only_conditions_do_not_gate_v2() -> None:
    # Down intraday trend, low volume, missing quant, missing atr_pct: all v1 gates,
    # none part of the pre-registered v2 rule.
    pkg = _package(context=_context(atr_pct_of_close=None))
    assert decide_v2(pkg, _params()).action == ProposalAction.BUY


# --- Gates --------------------------------------------------------------------

_Build = Callable[[], EvidencePackage]

_GATES: list[tuple[str, _Build, NoTradeReasonV2]] = [
    ("no_context", lambda: _package(context=None), NoTradeReasonV2.MARKET_CONTEXT_MISSING),
    (
        "context_after_as_of",
        lambda: _package(context=_context(as_of=AS_OF + timedelta(minutes=1))),
        NoTradeReasonV2.MARKET_CONTEXT_AFTER_AS_OF,
    ),
    (
        "zero_close",
        lambda: _package(context=_context(last_close=Decimal("0"))),
        NoTradeReasonV2.LAST_CLOSE_INVALID,
    ),
    (
        "atr_none",
        lambda: _package(context=_context(atr_14=None)),
        NoTradeReasonV2.ATR_UNAVAILABLE,
    ),
    (
        "atr_zero",
        lambda: _package(context=_context(atr_14=Decimal("0"))),
        NoTradeReasonV2.ATR_UNAVAILABLE,
    ),
    ("no_htf", lambda: _package(htf=None), NoTradeReasonV2.HTF_CONTEXT_MISSING),
    (
        "htf_after_as_of",
        lambda: _package(htf=_htf(as_of=AS_OF + timedelta(days=1))),
        NoTradeReasonV2.HTF_CONTEXT_AFTER_AS_OF,
    ),
    (
        "sma_none",
        lambda: _package(htf=_htf(sma={"20": None, "50": None, "100": None, "200": None})),
        NoTradeReasonV2.HTF_SMA_UNAVAILABLE,
    ),
    (
        "sma_key_missing",
        lambda: _package(htf=_htf(sma={"20": Decimal("1")})),
        NoTradeReasonV2.HTF_SMA_UNAVAILABLE,
    ),
    (
        "regime_equal",
        lambda: _package(htf=_htf(last_close=Decimal("100"))),
        NoTradeReasonV2.REGIME_NOT_UP,
    ),
    (
        "regime_below",
        lambda: _package(htf=_htf(last_close=Decimal("99.99"))),
        NoTradeReasonV2.REGIME_NOT_UP,
    ),
    ("technical_missing", lambda: _package(evidence=[]), NoTradeReasonV2.TECHNICAL_MISSING),
    (
        "technical_duplicate",
        lambda: _package(evidence=[_technical(), _technical()]),
        NoTradeReasonV2.TECHNICAL_AMBIGUOUS,
    ),
    (
        "technical_unavailable",
        lambda: _package(evidence=[_technical(status=EvidenceStatus.UNAVAILABLE)]),
        NoTradeReasonV2.TECHNICAL_NOT_OK,
    ),
    (
        "rsi_missing",
        lambda: _package(evidence=[_technical(rsi_14=None)]),
        NoTradeReasonV2.RSI_UNAVAILABLE,
    ),
    (
        "rsi_float",
        lambda: _package(evidence=[_technical(rsi_14=30.0)]),
        NoTradeReasonV2.RSI_UNAVAILABLE,
    ),
    (
        "rsi_nan",
        lambda: _package(evidence=[_technical(rsi_14="NaN")]),
        NoTradeReasonV2.RSI_UNAVAILABLE,
    ),
    (
        "rsi_above",
        lambda: _package(evidence=[_technical(rsi_14="40.01")]),
        NoTradeReasonV2.RSI_ABOVE_ENTRY_MAX,
    ),
]


@pytest.mark.parametrize(
    ("build", "expected"),
    [(b, e) for _, b, e in _GATES],
    ids=[n for n, _, _ in _GATES],
)
def test_each_gate(build: _Build, expected: NoTradeReasonV2) -> None:
    assert _reason(decide_v2(build(), _params())) == expected.value


def test_regime_uses_selected_lookback() -> None:
    pkg = _package()  # close 110: > sma20 105, > sma50 100, < sma100 120, sma200 None
    assert decide_v2(pkg, _params(regime_n=20)).action == ProposalAction.BUY
    assert decide_v2(pkg, _params(regime_n=50)).action == ProposalAction.BUY
    assert _reason(decide_v2(pkg, _params(regime_n=100))) == "regime_not_up"
    assert _reason(decide_v2(pkg, _params(regime_n=200))) == "htf_sma_unavailable"


def test_rsi_bound_inclusive_and_stop_not_positive() -> None:
    assert (
        decide_v2(_package(evidence=[_technical(rsi_14="40")]), _params()).action
        == ProposalAction.BUY
    )
    assert _reason(decide_v2(_package(), _params(k_atr_stop=Decimal("50")))) == (
        "stop_not_positive"
    )


def test_deterministic_pure_and_context_independent() -> None:
    pkg = _package(
        context=_context(last_close=Decimal("2500.123456789"), atr_14=Decimal("12.3456789123"))
    )
    before = pkg.model_dump()
    params = _params(k_atr_stop=Decimal("1.37"), r_target=Decimal("2.11"))
    expected = decide_v2(pkg, params)
    with localcontext(Context(prec=4)):
        assert decide_v2(pkg, params) == expected
    assert decide_v2(pkg, params) == expected
    assert pkg.model_dump() == before


def test_rules_v2_module_has_no_io_or_execution_imports() -> None:
    path = Path(__file__).resolve().parents[1] / "src" / "aegis" / "strategy" / "rules_v2.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = (
        "aegis.exchange",
        "aegis.orders",
        "aegis.risk",
        "aegis.paper",
        "aegis.db",
        "aegis.market_data",
        "aegis.config",
        "httpx",
        "requests",
        "socket",
        "os",
        "time",
        "random",
        "datetime",
        "openai",
    )
    for name in imported:
        assert not name.startswith(forbidden), name


# --- RuleParamsV2 -------------------------------------------------------------


def test_to_dict_round_trip_and_canonical_hash() -> None:
    params = _params()
    data = params.to_dict()
    assert data == {
        "strategy": "rules-v2",
        "regime_n": "50",
        "rsi_entry_max": "40",
        "k_atr_stop": "1.5",
        "r_target": "2.5",
        "max_hold_bars": "16",
    }
    assert RuleParamsV2.from_dict(data) == params
    without_key = {k: v for k, v in data.items() if k != "strategy"}
    assert RuleParamsV2.from_dict(without_key) == params
    assert RuleParamsV2.from_dict({**without_key, "regime_n": 50, "max_hold_bars": 16}) == params
    equal = _params(rsi_entry_max=Decimal("4E+1"), k_atr_stop=Decimal("1.50"))
    assert equal.param_hash() == params.param_hash()
    assert len(params.param_hash()) == 64
    assert params.param_hash() != _params(max_hold_bars=48).param_hash()
    assert params.param_hash() != _params(regime_n=20).param_hash()


def test_param_hash_is_pinned() -> None:
    assert STRATEGY_VERSION_V2 == "rules-v2"
    # Stored experiment rows key on this hash; changing it must be deliberate.
    assert (
        _params().param_hash() == "b8e7d1ae2a36f0bc604e4ebbc6adeaa1d841bbb5680139fe9592e60b3efae9ff"
    )


def test_from_dict_rejects_bad_input() -> None:
    data: dict[str, Any] = dict(_params().to_dict())
    with pytest.raises(ValueError, match="missing"):
        RuleParamsV2.from_dict({k: v for k, v in data.items() if k != "r_target"})
    with pytest.raises(ValueError, match="unknown"):
        RuleParamsV2.from_dict({**data, "leverage": "10"})
    with pytest.raises(ValueError, match="strategy"):
        RuleParamsV2.from_dict({**data, "strategy": "rules-v1"})
    with pytest.raises(TypeError):
        RuleParamsV2.from_dict({**data, "r_target": 2.5})
    with pytest.raises(TypeError):
        RuleParamsV2.from_dict({**data, "regime_n": 50.0})
    with pytest.raises(TypeError):
        RuleParamsV2.from_dict({**data, "max_hold_bars": True})
    with pytest.raises(ValueError):
        RuleParamsV2.from_dict({**data, "regime_n": "fifty"})
    with pytest.raises(ValueError):
        RuleParamsV2.from_dict({**data, "k_atr_stop": "x"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"regime_n": 30},
        {"regime_n": 0},
        {"rsi_entry_max": Decimal("-1")},
        {"rsi_entry_max": Decimal("100.1")},
        {"k_atr_stop": Decimal("0")},
        {"r_target": Decimal("-1")},
        {"max_hold_bars": 0},
        {"k_atr_stop": Decimal("NaN")},
        {"r_target": Decimal("Infinity")},
    ],
)
def test_validation_rejects(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _params(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"regime_n": True},
        {"regime_n": Decimal("50")},
        {"max_hold_bars": 16.0},
        {"k_atr_stop": 1.5},
    ],
)
def test_validation_rejects_types(overrides: dict[str, Any]) -> None:
    with pytest.raises(TypeError):
        _params(**overrides)


def test_frozen() -> None:
    params = _params()
    with pytest.raises(AttributeError):
        params.regime_n = 20  # type: ignore[misc]


# --- Dispatcher -------------------------------------------------------------


def test_params_from_dict_dispatch_round_trip() -> None:
    v2 = _params()
    assert params_from_dict(v2.to_dict()) == v2
    v1 = _v1_params()
    assert params_from_dict(v1.to_dict()) == v1  # no 'strategy' key -> rules-v1
    assert params_from_dict({**v1.to_dict(), "strategy": "rules-v1"}) == v1
    with pytest.raises(ValueError, match="unknown strategy"):
        params_from_dict({**v1.to_dict(), "strategy": "rules-v9"})


def test_decide_any_and_helpers() -> None:
    pkg = _package()
    assert decide_any(pkg, _params()) == decide_v2(pkg, _params())
    assert decide_any(pkg, _v1_params()) == decide(pkg, _v1_params())
    assert max_hold_bars_of(_params(max_hold_bars=36)) == 36
    assert max_hold_bars_of(_v1_params()) is None
    assert strategy_version_of(_params()) == STRATEGY_VERSION_V2
    assert strategy_version_of(_v1_params()) == STRATEGY_VERSION
    with pytest.raises(TypeError):
        decide_any(pkg, object())  # type: ignore[arg-type]


def test_v1_unchanged_by_htf_context() -> None:
    # v1 ignores htf_context entirely; its hash stays pinned.
    assert STRATEGY_VERSION == "rules-v1"
    assert "strategy" not in _v1_params().to_dict()
    with_htf = _package(evidence=[_technical()])
    without_htf = _package(evidence=[_technical()], htf=None)
    assert decide(with_htf, _v1_params()) == decide(without_htf, _v1_params())
