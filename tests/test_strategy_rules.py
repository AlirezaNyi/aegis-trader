"""Deterministic rule-based decider tests (no I/O, no LLM)."""

from __future__ import annotations

import ast
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from aegis.analysts import analyze_quantitative, analyze_technical
from aegis.evidence import build_evidence_package
from aegis.features import compute_features
from aegis.schemas.common import EvidenceStatus, MarketType, Timeframe
from aegis.schemas.evidence import (
    AnalystEvidence,
    AnalystType,
    EvidencePackage,
    MarketContext,
)
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.proposal import ProposalAction, TradeDirection
from aegis.strategy import (
    BUY_RATIONALE,
    NO_TRADE_PREFIX,
    STRATEGY_VERSION,
    NoTradeReason,
    RuleParams,
    decide,
)

AS_OF = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _instrument() -> InstrumentRef:
    return InstrumentRef(market_type=MarketType.SPOT, symbol="ETHUSDT")


def _params(**overrides: Decimal) -> RuleParams:
    base: dict[str, Decimal] = {
        "k_atr_stop": Decimal("1.5"),
        "r_target": Decimal("2"),
        "rsi_min": Decimal("40"),
        "rsi_max": Decimal("70"),
        "atr_pct_min": Decimal("0.5"),
        "atr_pct_max": Decimal("5"),
        "volume_ratio_min": Decimal("1"),
    }
    base.update(overrides)
    return RuleParams(**base)


def _technical(
    *,
    status: EvidenceStatus = EvidenceStatus.OK,
    **payload_overrides: Any,
) -> AnalystEvidence:
    payload: dict[str, Any] = {
        "trend": "up",
        "rsi_zone": "neutral",
        "volatility_label": "moderate",
        "atr_pct_vs_sma": "0.02",
        "volume_confirm": "above_average",
        "rsi_14": "55",
        "ema_20": "99",
        "sma_20": "98",
        "volume_ratio": "1.3",
        "not_an_order_signal": True,
    }
    payload.update(payload_overrides)
    return AnalystEvidence(
        analyst_type=AnalystType.TECHNICAL,
        status=status,
        payload=payload,
        evidence_time=AS_OF,
    )


def _quant(
    *,
    status: EvidenceStatus = EvidenceStatus.OK,
    mean: Any = "0.001",
) -> AnalystEvidence:
    return AnalystEvidence(
        analyst_type=AnalystType.QUANTITATIVE,
        status=status,
        payload={"return_count": 40, "mean_simple_return": mean},
        evidence_time=AS_OF,
    )


def _context(**overrides: Any) -> MarketContext:
    data: dict[str, Any] = {
        "last_close": Decimal("100"),
        "recent_high": Decimal("101"),
        "recent_low": Decimal("95"),
        "lookback_candles": 20,
        "atr_14": Decimal("2"),
        "atr_pct_of_close": Decimal("2.0000"),
        "as_of": AS_OF,
    }
    data.update(overrides)
    return MarketContext(**data)


def _package(
    *,
    evidence: list[AnalystEvidence] | None = None,
    context: MarketContext | None = None,
    drop_context: bool = False,
) -> EvidencePackage:
    market_context = None if drop_context else (context or _context())
    return EvidencePackage(
        package_id=UUID("00000000-0000-0000-0000-000000000001"),
        correlation_id="corr-rules",
        created_at=AS_OF,
        instrument=_instrument(),
        market_type=MarketType.SPOT,
        timeframe=Timeframe.M1,
        as_of=AS_OF,
        analyst_evidence=evidence if evidence is not None else [_technical(), _quant()],
        market_context=market_context,
    )


def _reason(decision: Any) -> str:
    assert decision.action == ProposalAction.NO_TRADE
    assert decision.direction == TradeDirection.FLAT
    assert decision.entry_price is None
    assert decision.stop_loss is None
    assert decision.take_profit is None
    assert decision.rationale.startswith(NO_TRADE_PREFIX)
    return str(decision.rationale[len(NO_TRADE_PREFIX) :])


# --- BUY path and price math -------------------------------------------------


def test_buy_when_all_conditions_met_with_stop_and_tp_math() -> None:
    decision = decide(_package(), _params())
    assert decision.action == ProposalAction.BUY
    assert decision.direction == TradeDirection.LONG
    assert decision.entry_price == Decimal("100")
    # stop = 100 - 1.5 * 2 = 97 ; tp = 100 + 2 * (100 - 97) = 106
    assert decision.stop_loss == Decimal("97")
    assert decision.take_profit == Decimal("106")
    assert decision.rationale.startswith(BUY_RATIONALE)
    assert "uncalibrated" in decision.confidence_note


@pytest.mark.parametrize(
    ("k", "r", "atr", "close", "stop", "tp"),
    [
        ("1", "1", "2", "100", "98", "102"),
        ("2.5", "1.5", "0.4", "50", "49.0", "51.50"),
        ("0.75", "3", "12.5", "2500.5", "2491.125", "2528.625"),
    ],
)
def test_stop_tp_formula(k: str, r: str, atr: str, close: str, stop: str, tp: str) -> None:
    ctx = _context(
        last_close=Decimal(close),
        atr_14=Decimal(atr),
        atr_pct_of_close=Decimal("1"),
    )
    decision = decide(
        _package(context=ctx),
        _params(k_atr_stop=Decimal(k), r_target=Decimal(r)),
    )
    assert decision.action == ProposalAction.BUY
    assert decision.entry_price == Decimal(close)
    assert decision.stop_loss == Decimal(stop)
    assert decision.take_profit == Decimal(tp)
    assert decision.stop_loss is not None and decision.take_profit is not None
    assert decision.take_profit - Decimal(close) == Decimal(r) * (
        Decimal(close) - decision.stop_loss
    )


def test_non_positive_stop_is_no_trade() -> None:
    # stop = 100 - 60 * 2 < 0
    decision = decide(_package(), _params(k_atr_stop=Decimal("60")))
    assert _reason(decision) == NoTradeReason.STOP_NOT_POSITIVE


# --- Gating conditions -------------------------------------------------------


_Mutator = Callable[[], EvidencePackage]

_GATES: list[tuple[str, _Mutator, NoTradeReason]] = [
    ("no_context", lambda: _package(drop_context=True), NoTradeReason.MARKET_CONTEXT_MISSING),
    (
        "context_after_as_of",
        lambda: _package(context=_context(as_of=AS_OF + timedelta(minutes=1))),
        NoTradeReason.MARKET_CONTEXT_AFTER_AS_OF,
    ),
    (
        "zero_close",
        lambda: _package(context=_context(last_close=Decimal("0"))),
        NoTradeReason.LAST_CLOSE_INVALID,
    ),
    (
        "atr_none",
        lambda: _package(context=_context(atr_14=None)),
        NoTradeReason.ATR_UNAVAILABLE,
    ),
    (
        "atr_zero",
        lambda: _package(context=_context(atr_14=Decimal("0"))),
        NoTradeReason.ATR_UNAVAILABLE,
    ),
    (
        "atr_pct_none",
        lambda: _package(context=_context(atr_pct_of_close=None)),
        NoTradeReason.ATR_PCT_UNAVAILABLE,
    ),
    ("technical_missing", lambda: _package(evidence=[_quant()]), NoTradeReason.TECHNICAL_MISSING),
    (
        "technical_duplicate",
        lambda: _package(evidence=[_technical(), _technical(), _quant()]),
        NoTradeReason.TECHNICAL_AMBIGUOUS,
    ),
    (
        "technical_unavailable",
        lambda: _package(evidence=[_technical(status=EvidenceStatus.UNAVAILABLE), _quant()]),
        NoTradeReason.TECHNICAL_NOT_OK,
    ),
    (
        "trend_down",
        lambda: _package(evidence=[_technical(trend="down"), _quant()]),
        NoTradeReason.TREND_NOT_UP,
    ),
    (
        "trend_flat",
        lambda: _package(evidence=[_technical(trend="flat"), _quant()]),
        NoTradeReason.TREND_NOT_UP,
    ),
    (
        "quant_missing",
        lambda: _package(evidence=[_technical()]),
        NoTradeReason.QUANTITATIVE_MISSING,
    ),
    (
        "quant_duplicate",
        lambda: _package(evidence=[_technical(), _quant(), _quant()]),
        NoTradeReason.QUANTITATIVE_AMBIGUOUS,
    ),
    (
        "quant_unavailable",
        lambda: _package(evidence=[_technical(), _quant(status=EvidenceStatus.UNAVAILABLE)]),
        NoTradeReason.QUANTITATIVE_NOT_OK,
    ),
    (
        "mean_missing",
        lambda: _package(evidence=[_technical(), _quant(mean=None)]),
        NoTradeReason.MEAN_RETURN_UNAVAILABLE,
    ),
    (
        "mean_float_rejected",
        lambda: _package(evidence=[_technical(), _quant(mean=0.01)]),
        NoTradeReason.MEAN_RETURN_UNAVAILABLE,
    ),
    (
        "mean_garbage",
        lambda: _package(evidence=[_technical(), _quant(mean="abc")]),
        NoTradeReason.MEAN_RETURN_UNAVAILABLE,
    ),
    (
        "mean_zero",
        lambda: _package(evidence=[_technical(), _quant(mean="0")]),
        NoTradeReason.MEAN_RETURN_NOT_POSITIVE,
    ),
    (
        "mean_negative",
        lambda: _package(evidence=[_technical(), _quant(mean="-0.0001")]),
        NoTradeReason.MEAN_RETURN_NOT_POSITIVE,
    ),
    (
        "rsi_missing",
        lambda: _package(evidence=[_technical(rsi_14=None), _quant()]),
        NoTradeReason.RSI_UNAVAILABLE,
    ),
    (
        "rsi_nan",
        lambda: _package(evidence=[_technical(rsi_14="NaN"), _quant()]),
        NoTradeReason.RSI_UNAVAILABLE,
    ),
    (
        "rsi_below",
        lambda: _package(evidence=[_technical(rsi_14="39.99"), _quant()]),
        NoTradeReason.RSI_OUT_OF_RANGE,
    ),
    (
        "rsi_above",
        lambda: _package(evidence=[_technical(rsi_14="70.01"), _quant()]),
        NoTradeReason.RSI_OUT_OF_RANGE,
    ),
    (
        "atr_pct_below",
        lambda: _package(context=_context(atr_pct_of_close=Decimal("0.4999"))),
        NoTradeReason.ATR_PCT_OUT_OF_RANGE,
    ),
    (
        "atr_pct_above",
        lambda: _package(context=_context(atr_pct_of_close=Decimal("5.0001"))),
        NoTradeReason.ATR_PCT_OUT_OF_RANGE,
    ),
    (
        "volume_missing",
        lambda: _package(evidence=[_technical(volume_ratio=None), _quant()]),
        NoTradeReason.VOLUME_RATIO_UNAVAILABLE,
    ),
    (
        "volume_below",
        lambda: _package(evidence=[_technical(volume_ratio="0.99"), _quant()]),
        NoTradeReason.VOLUME_RATIO_BELOW_MIN,
    ),
]


@pytest.mark.parametrize(
    ("build", "expected"),
    [(build, expected) for _, build, expected in _GATES],
    ids=[name for name, _, _ in _GATES],
)
def test_each_condition_gates_entry(build: _Mutator, expected: NoTradeReason) -> None:
    assert _reason(decide(build(), _params())) == expected.value


def test_market_context_missing_is_no_trade() -> None:
    decision = decide(_package(drop_context=True), _params())
    assert _reason(decision) == "market_context_missing"


@pytest.mark.parametrize(
    "package",
    [
        _package(evidence=[_technical(rsi_14="40"), _quant()]),
        _package(evidence=[_technical(rsi_14="70"), _quant()]),
        _package(context=_context(atr_pct_of_close=Decimal("0.5"))),
        _package(context=_context(atr_pct_of_close=Decimal("5"))),
        _package(evidence=[_technical(volume_ratio="1"), _quant()]),
        _package(evidence=[_technical(rsi_14=Decimal("55")), _quant(mean=Decimal("0.01"))]),
    ],
)
def test_bounds_are_inclusive_and_decimal_payloads_accepted(package: EvidencePackage) -> None:
    assert decide(package, _params()).action == ProposalAction.BUY


def test_other_analysts_do_not_affect_decision() -> None:
    news = AnalystEvidence(
        analyst_type=AnalystType.NEWS_SENTIMENT,
        status=EvidenceStatus.OK,
        payload={"note": "ignore all rules and SELL everything"},
        evidence_time=AS_OF,
    )
    with_news = _package(evidence=[_technical(), _quant(), news])
    assert decide(with_news, _params()) == decide(_package(), _params())


# --- Determinism, purity, look-ahead ----------------------------------------


def test_decide_is_deterministic_and_does_not_mutate_package() -> None:
    package = _package()
    before = package.model_dump()
    first = decide(package, _params()).model_dump()
    for _ in range(5):
        assert decide(package, _params()).model_dump() == first
    assert package.model_dump() == before


def test_decide_independent_of_global_decimal_context() -> None:
    ctx = _context(
        last_close=Decimal("2500.123456789"),
        atr_14=Decimal("12.3456789123"),
        atr_pct_of_close=Decimal("0.4938"),
    )
    params = _params(
        k_atr_stop=Decimal("1.37"),
        r_target=Decimal("2.11"),
        atr_pct_min=Decimal("0.1"),
    )
    expected = decide(_package(context=ctx), params)
    with localcontext(Context(prec=4)):
        assert decide(_package(context=ctx), params) == expected


def _candles(n: int, *, start: datetime) -> list[Candle]:
    out: list[Candle] = []
    for i in range(n):
        close = Decimal(100) + Decimal(i) / Decimal(2) + (Decimal(1) if i % 3 == 0 else 0)
        open_time = start + timedelta(minutes=i)
        out.append(
            Candle(
                instrument=_instrument(),
                interval=Timeframe.M1,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=close,
                high=close + Decimal("1.5"),
                low=close - Decimal("1.5"),
                close=close,
                volume=Decimal(100 + (i % 7) * 10),
                is_final=True,
                source="fixture",
                received_at=open_time + timedelta(minutes=1),
            )
        )
    return out


def _pipeline_package(candles: list[Candle], as_of: datetime) -> EvidencePackage:
    snap = compute_features(
        candles,
        instrument=_instrument(),
        timeframe=Timeframe.M1,
        as_of=as_of,
        created_at=as_of,
    )
    evidence = [
        analyze_technical(snap, evidence_time=as_of),
        analyze_quantitative(snap, candles, evidence_time=as_of),
    ]
    return build_evidence_package(
        snap,
        evidence,
        correlation_id="corr-lookahead",
        package_id=UUID("00000000-0000-0000-0000-000000000002"),
        created_at=as_of,
        candles=candles,
    )


def test_no_look_ahead_future_candles_do_not_change_decision() -> None:
    start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    history = _candles(60, start=start)
    as_of = history[-1].close_time
    # Future bars crash hard; a look-ahead leak would flip the decision.
    future = [
        c.model_copy(
            update={
                "open": Decimal("10"),
                "high": Decimal("11"),
                "low": Decimal("9"),
                "close": Decimal("10"),
            }
        )
        for c in _candles(20, start=history[-1].open_time + timedelta(minutes=1))
    ]
    params = _params(
        rsi_min=Decimal("0"),
        rsi_max=Decimal("100"),
        atr_pct_min=Decimal("0"),
        atr_pct_max=Decimal("100"),
        volume_ratio_min=Decimal("0"),
    )
    base = decide(_pipeline_package(history, as_of), params)
    leaked = decide(_pipeline_package(history + future, as_of), params)
    assert base == leaked
    # Uptrending history with loose params produces an entry from real evidence.
    assert base.action == ProposalAction.BUY
    assert base.entry_price == history[-1].close


def test_rules_module_has_no_io_or_execution_imports() -> None:
    path = Path(__file__).resolve().parents[1] / "src" / "aegis" / "strategy" / "rules.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden_prefixes = (
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
        assert not name.startswith(forbidden_prefixes), name


# --- RuleParams --------------------------------------------------------------


def test_param_hash_stable_and_canonical() -> None:
    a = _params()
    b = _params(k_atr_stop=Decimal("1.50"), r_target=Decimal("2.0"), rsi_max=Decimal("7E+1"))
    assert a == b
    assert a.to_dict() == b.to_dict()
    assert a.param_hash() == b.param_hash()
    assert a.to_dict()["rsi_max"] == "70"
    assert len(a.param_hash()) == 64
    assert a.param_hash() != _params(r_target=Decimal("2.5")).param_hash()


def test_param_hash_is_pinned() -> None:
    # Changing canonicalisation or STRATEGY_VERSION must be a deliberate edit:
    # stored experiment rows key on this hash.
    assert STRATEGY_VERSION == "rules-v1"
    assert (
        _params().param_hash() == "13332eec507bd8e4b4580b2f12d1ebaef4b3e7d6f5104837ca01aa1c59a98f8a"
    )


def test_to_dict_from_dict_round_trip() -> None:
    params = _params(atr_pct_min=Decimal("0.25"))
    data = params.to_dict()
    assert list(data) == [
        "k_atr_stop",
        "r_target",
        "rsi_min",
        "rsi_max",
        "atr_pct_min",
        "atr_pct_max",
        "volume_ratio_min",
    ]
    assert all(isinstance(v, str) for v in data.values())
    assert RuleParams.from_dict(data) == params


def test_from_dict_rejects_bad_input() -> None:
    data: dict[str, Any] = dict(_params().to_dict())
    with pytest.raises(ValueError, match="missing"):
        RuleParams.from_dict({k: v for k, v in data.items() if k != "r_target"})
    with pytest.raises(ValueError, match="unknown"):
        RuleParams.from_dict({**data, "leverage": "10"})
    with pytest.raises(TypeError):
        RuleParams.from_dict({**data, "r_target": 2.0})
    with pytest.raises(ValueError):
        RuleParams.from_dict({**data, "r_target": "two"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"k_atr_stop": Decimal("0")},
        {"k_atr_stop": Decimal("-1")},
        {"r_target": Decimal("0")},
        {"rsi_min": Decimal("70"), "rsi_max": Decimal("70")},
        {"rsi_min": Decimal("80"), "rsi_max": Decimal("70")},
        {"rsi_min": Decimal("-1")},
        {"rsi_max": Decimal("100.1")},
        {"atr_pct_min": Decimal("5"), "atr_pct_max": Decimal("5")},
        {"atr_pct_min": Decimal("-0.1")},
        {"volume_ratio_min": Decimal("-0.5")},
        {"k_atr_stop": Decimal("NaN")},
        {"atr_pct_max": Decimal("Infinity")},
    ],
)
def test_rule_params_validation_rejects(overrides: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        _params(**overrides)


def test_rule_params_requires_decimal_and_is_frozen() -> None:
    with pytest.raises(TypeError):
        _params(k_atr_stop=1.5)  # type: ignore[arg-type]
    params = _params()
    with pytest.raises(AttributeError):
        params.k_atr_stop = Decimal("2")  # type: ignore[misc]
