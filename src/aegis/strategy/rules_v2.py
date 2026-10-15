"""Deterministic ``rules-v2`` decider: daily regime + intraday pullback (long-only).

Implements exactly the strategy pre-registered in
``docs/BACKTEST_PREREGISTRATION_V2_2026-10-15.md``. BUY when all hold:

1. Daily regime up: ``htf_context.last_close > htf_context.sma[str(regime_n)]``
   (built only from completed higher-timeframe bars; no look-ahead).
2. Intraday pullback: technical ``rsi_14 <= rsi_entry_max``.
3. Market context present with ``atr_14`` available.

Levels: ``stop = entry - k_atr_stop * atr_14``;
``take_profit = entry + r_target * (entry - stop)``. ``max_hold_bars`` is the
pre-registered time stop; it is carried on the params for the caller (backtest /
paper exit simulation) and is not evaluated here.

This is **not** trade authority: the output still flows through proposal assembly
and the deterministic Risk Engine. Pure: no I/O, no clock, no randomness; all
arithmetic is ``Decimal`` under a fixed local context. Parameter values are
research inputs, not owner-approved risk-policy values, and carry no
profitability claim.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Any

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystType, EvidencePackage
from aegis.schemas.proposal import ProposalAction, TradeDirection
from aegis.strategy.rules import NO_TRADE_PREFIX, _as_decimal, _canonical, _single_evidence
from aegis.supervisor.decision import SupervisorDecision

STRATEGY_ID_V2 = "rule_daily_regime_pullback_long"
STRATEGY_VERSION_V2 = "rules-v2"
BUY_RATIONALE_V2 = "buy:daily_regime_up_and_pullback"
ALLOWED_REGIME_N: tuple[int, ...] = (20, 50, 100, 200)

_RSI_LOWER = Decimal("0")
_RSI_UPPER = Decimal("100")
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
_DECIMAL_FIELDS = ("rsi_entry_max", "k_atr_stop", "r_target")
_INT_FIELDS = ("regime_n", "max_hold_bars")
_FIELD_ORDER = ("regime_n", "rsi_entry_max", "k_atr_stop", "r_target", "max_hold_bars")
_STRATEGY_KEY = "strategy"


class NoTradeReasonV2(StrEnum):
    """Machine-readable reason codes; the first failed condition is reported."""

    MARKET_CONTEXT_MISSING = "market_context_missing"
    MARKET_CONTEXT_AFTER_AS_OF = "market_context_after_as_of"
    LAST_CLOSE_INVALID = "last_close_invalid"
    ATR_UNAVAILABLE = "atr_unavailable"
    HTF_CONTEXT_MISSING = "htf_context_missing"
    HTF_CONTEXT_AFTER_AS_OF = "htf_context_after_as_of"
    HTF_SMA_UNAVAILABLE = "htf_sma_unavailable"
    REGIME_NOT_UP = "regime_not_up"
    TECHNICAL_MISSING = "technical_missing"
    TECHNICAL_AMBIGUOUS = "technical_ambiguous"
    TECHNICAL_NOT_OK = "technical_not_ok"
    RSI_UNAVAILABLE = "rsi_unavailable"
    RSI_ABOVE_ENTRY_MAX = "rsi_above_entry_max"
    STOP_NOT_POSITIVE = "stop_not_positive"


@dataclass(frozen=True)
class RuleParamsV2:
    """Tunable inputs for :func:`decide_v2`. No defaults; the caller supplies all.

    * ``regime_n``: daily SMA lookback, one of ``20, 50, 100, 200``.
    * ``rsi_entry_max``: inclusive RSI(14) ceiling within ``[0, 100]``.
    * ``k_atr_stop``: stop distance in ATR multiples (``> 0``).
    * ``r_target``: take-profit distance as a multiple of stop distance (``> 0``).
    * ``max_hold_bars``: time stop in trading bars (``>= 1``); used by the caller.
    """

    regime_n: int
    rsi_entry_max: Decimal
    k_atr_stop: Decimal
    r_target: Decimal
    max_hold_bars: int

    def __post_init__(self) -> None:
        for name in _INT_FIELDS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                msg = f"{name} must be int, got {type(value).__name__}"
                raise TypeError(msg)
        for name in _DECIMAL_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                msg = f"{name} must be Decimal, got {type(value).__name__}"
                raise TypeError(msg)
            if not value.is_finite():
                msg = f"{name} must be finite"
                raise ValueError(msg)
        if self.regime_n not in ALLOWED_REGIME_N:
            msg = f"regime_n must be one of {ALLOWED_REGIME_N}"
            raise ValueError(msg)
        if not (_RSI_LOWER <= self.rsi_entry_max <= _RSI_UPPER):
            raise ValueError("rsi_entry_max must lie within [0, 100]")
        if self.k_atr_stop <= 0:
            raise ValueError("k_atr_stop must be > 0")
        if self.r_target <= 0:
            raise ValueError("r_target must be > 0")
        if self.max_hold_bars < 1:
            raise ValueError("max_hold_bars must be >= 1")

    def to_dict(self) -> dict[str, str]:
        """Canonical JSON-safe strings incl. ``strategy: rules-v2`` (round-trips)."""
        out: dict[str, str] = {_STRATEGY_KEY: STRATEGY_VERSION_V2}
        for name in _FIELD_ORDER:
            value = getattr(self, name)
            out[name] = _canonical(value) if isinstance(value, Decimal) else str(value)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuleParamsV2:
        """Build from ``to_dict`` output.

        The ``strategy`` key is optional but, if present, must be ``rules-v2``.
        Decimal fields accept ``str``/``Decimal``; int fields accept ``int`` or a
        base-10 integer ``str``. Floats and booleans are rejected.
        """
        if _STRATEGY_KEY in data and data[_STRATEGY_KEY] != STRATEGY_VERSION_V2:
            msg = f"strategy must be {STRATEGY_VERSION_V2!r}, got {data[_STRATEGY_KEY]!r}"
            raise ValueError(msg)
        keys = set(data) - {_STRATEGY_KEY}
        unknown = sorted(keys - set(_FIELD_ORDER))
        missing = [n for n in _FIELD_ORDER if n not in keys]
        if unknown or missing:
            msg = f"invalid RuleParamsV2 keys: missing={missing} unknown={unknown}"
            raise ValueError(msg)
        ints: dict[str, int] = {}
        for name in _INT_FIELDS:
            raw = data[name]
            if isinstance(raw, bool):
                msg = f"{name} must be int or str, got bool"
                raise TypeError(msg)
            if isinstance(raw, int):
                ints[name] = raw
            elif isinstance(raw, str):
                try:
                    ints[name] = int(raw, 10)
                except ValueError as exc:
                    msg = f"{name} is not an integer string"
                    raise ValueError(msg) from exc
            else:
                msg = f"{name} must be int or str, got {type(raw).__name__}"
                raise TypeError(msg)
        decs: dict[str, Decimal] = {}
        for name in _DECIMAL_FIELDS:
            raw = data[name]
            if isinstance(raw, Decimal):
                decs[name] = raw
            elif isinstance(raw, str):
                try:
                    decs[name] = Decimal(raw)
                except InvalidOperation as exc:
                    msg = f"{name} is not a decimal string"
                    raise ValueError(msg) from exc
            else:
                msg = f"{name} must be str or Decimal, got {type(raw).__name__}"
                raise TypeError(msg)
        return cls(
            regime_n=ints["regime_n"],
            rsi_entry_max=decs["rsi_entry_max"],
            k_atr_stop=decs["k_atr_stop"],
            r_target=decs["r_target"],
            max_hold_bars=ints["max_hold_bars"],
        )

    def param_hash(self) -> str:
        """SHA-256 over strategy version + canonical params (stable across runs)."""
        body = json.dumps(
            {"strategy_version": STRATEGY_VERSION_V2, "params": self.to_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _no_trade(reason: NoTradeReasonV2) -> SupervisorDecision:
    return SupervisorDecision(
        action=ProposalAction.NO_TRADE,
        direction=TradeDirection.FLAT,
        rationale=f"{NO_TRADE_PREFIX}{reason.value}",
        confidence_note="deterministic_rules_uncalibrated",
    )


def decide_v2(package: EvidencePackage, params: RuleParamsV2) -> SupervisorDecision:
    """Return BUY/long when every rules-v2 condition holds, else NO_TRADE/flat.

    Checks, in order (the first failure is named as ``no_trade:<reason>``):

    1. ``market_context`` present, not after ``package.as_of``, ``last_close > 0``,
       ``atr_14 > 0``.
    2. ``htf_context`` present, not after ``package.as_of``,
       ``sma[str(regime_n)]`` not ``None``, and ``htf last_close > sma``.
    3. Exactly one technical evidence with status ``ok`` and a numeric
       ``rsi_14 <= rsi_entry_max``.
    4. ``stop = entry - k_atr_stop * atr_14 > 0``.

    ``entry = market_context.last_close``;
    ``take_profit = entry + r_target * (entry - stop)``.
    """
    with localcontext(_DECIMAL_CONTEXT):
        return _decide_v2(package, params)


def _decide_v2(package: EvidencePackage, params: RuleParamsV2) -> SupervisorDecision:
    ctx = package.market_context
    if ctx is None:
        return _no_trade(NoTradeReasonV2.MARKET_CONTEXT_MISSING)
    if ctx.as_of > package.as_of:
        return _no_trade(NoTradeReasonV2.MARKET_CONTEXT_AFTER_AS_OF)
    last_close = _as_decimal(ctx.last_close)
    if last_close is None or last_close <= 0:
        return _no_trade(NoTradeReasonV2.LAST_CLOSE_INVALID)
    atr = _as_decimal(ctx.atr_14)
    if atr is None or atr <= 0:
        return _no_trade(NoTradeReasonV2.ATR_UNAVAILABLE)

    htf = package.htf_context
    if htf is None:
        return _no_trade(NoTradeReasonV2.HTF_CONTEXT_MISSING)
    if htf.as_of > package.as_of:
        return _no_trade(NoTradeReasonV2.HTF_CONTEXT_AFTER_AS_OF)
    sma = _as_decimal(htf.sma.get(str(params.regime_n)))
    htf_close = _as_decimal(htf.last_close)
    if sma is None or htf_close is None:
        return _no_trade(NoTradeReasonV2.HTF_SMA_UNAVAILABLE)
    if not htf_close > sma:
        return _no_trade(NoTradeReasonV2.REGIME_NOT_UP)

    technical, n_technical = _single_evidence(package, AnalystType.TECHNICAL)
    if n_technical == 0:
        return _no_trade(NoTradeReasonV2.TECHNICAL_MISSING)
    if technical is None:
        return _no_trade(NoTradeReasonV2.TECHNICAL_AMBIGUOUS)
    if technical.status != EvidenceStatus.OK:
        return _no_trade(NoTradeReasonV2.TECHNICAL_NOT_OK)
    rsi = _as_decimal(technical.payload.get("rsi_14"))
    if rsi is None:
        return _no_trade(NoTradeReasonV2.RSI_UNAVAILABLE)
    if rsi > params.rsi_entry_max:
        return _no_trade(NoTradeReasonV2.RSI_ABOVE_ENTRY_MAX)

    entry = last_close
    stop = entry - params.k_atr_stop * atr
    if stop <= 0:
        return _no_trade(NoTradeReasonV2.STOP_NOT_POSITIVE)
    take_profit = entry + params.r_target * (entry - stop)

    return SupervisorDecision(
        action=ProposalAction.BUY,
        direction=TradeDirection.LONG,
        entry_price=entry,
        stop_loss=stop,
        take_profit=take_profit,
        rationale=f"{BUY_RATIONALE_V2};params={params.param_hash()[:16]}",
        invalidation=f"close_at_or_below_stop:{stop}",
        confidence_note="deterministic_rules_uncalibrated",
    )
