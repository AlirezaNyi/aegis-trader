"""Deterministic, tunable rule-based decider (long-only).

``decide`` maps an :class:`EvidencePackage` to a :class:`SupervisorDecision`
without an LLM, so backtests can replay the full pipeline cheaply and parameters
can be tuned with walk-forward analysis.

This is **not** trade authority. The output is a candidate decision that still
flows through proposal assembly and the deterministic Risk Engine, which alone
may APPROVE a trade. No I/O, no clocks, no randomness: the result depends only
on the package and the parameters. All arithmetic is ``Decimal`` under a fixed
local context so a caller-modified global decimal context cannot change output.

Parameter values are research inputs supplied by the caller (backtest / tuner).
They are not owner-approved risk-policy values and carry no profitability claim.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Any

from aegis.schemas.common import EvidenceStatus
from aegis.schemas.evidence import AnalystEvidence, AnalystType, EvidencePackage
from aegis.schemas.proposal import ProposalAction, TradeDirection
from aegis.supervisor.decision import SupervisorDecision

STRATEGY_ID = "rule_trend_long"
STRATEGY_VERSION = "rules-v1"

_RSI_LOWER = Decimal("0")
_RSI_UPPER = Decimal("100")
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


class NoTradeReason(StrEnum):
    """Machine-readable reason codes; the first failed condition is reported."""

    MARKET_CONTEXT_MISSING = "market_context_missing"
    MARKET_CONTEXT_AFTER_AS_OF = "market_context_after_as_of"
    LAST_CLOSE_INVALID = "last_close_invalid"
    ATR_UNAVAILABLE = "atr_unavailable"
    ATR_PCT_UNAVAILABLE = "atr_pct_unavailable"
    TECHNICAL_MISSING = "technical_missing"
    TECHNICAL_AMBIGUOUS = "technical_ambiguous"
    TECHNICAL_NOT_OK = "technical_not_ok"
    TREND_NOT_UP = "trend_not_up"
    QUANTITATIVE_MISSING = "quantitative_missing"
    QUANTITATIVE_AMBIGUOUS = "quantitative_ambiguous"
    QUANTITATIVE_NOT_OK = "quantitative_not_ok"
    MEAN_RETURN_UNAVAILABLE = "mean_return_unavailable"
    MEAN_RETURN_NOT_POSITIVE = "mean_return_not_positive"
    RSI_UNAVAILABLE = "rsi_unavailable"
    RSI_OUT_OF_RANGE = "rsi_out_of_range"
    ATR_PCT_OUT_OF_RANGE = "atr_pct_out_of_range"
    VOLUME_RATIO_UNAVAILABLE = "volume_ratio_unavailable"
    VOLUME_RATIO_BELOW_MIN = "volume_ratio_below_min"
    STOP_NOT_POSITIVE = "stop_not_positive"


NO_TRADE_PREFIX = "no_trade:"
BUY_RATIONALE = "buy:all_conditions_met"


def _canonical(value: Decimal) -> str:
    """Stable string for equal Decimal values (``1.0`` and ``1.00`` -> ``1``)."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class RuleParams:
    """Tunable thresholds for :func:`decide`. No defaults; the caller supplies all.

    * ``k_atr_stop``: stop distance in ATR multiples (``> 0``).
    * ``r_target``: take-profit distance as a multiple of stop distance (``> 0``).
    * ``rsi_min`` / ``rsi_max``: inclusive RSI(14) band within ``[0, 100]``.
    * ``atr_pct_min`` / ``atr_pct_max``: inclusive band on
      ``market_context.atr_pct_of_close``, which is in **percent** units
      (``0.42`` means 0.42%). Lower bound ``>= 0``.
    * ``volume_ratio_min``: minimum ``volume_ratio`` (``>= 0``).
    """

    k_atr_stop: Decimal
    r_target: Decimal
    rsi_min: Decimal
    rsi_max: Decimal
    atr_pct_min: Decimal
    atr_pct_max: Decimal
    volume_ratio_min: Decimal

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if not isinstance(value, Decimal):
                msg = f"{f.name} must be Decimal, got {type(value).__name__}"
                raise TypeError(msg)
            if not value.is_finite():
                msg = f"{f.name} must be finite"
                raise ValueError(msg)
        if self.k_atr_stop <= 0:
            raise ValueError("k_atr_stop must be > 0")
        if self.r_target <= 0:
            raise ValueError("r_target must be > 0")
        if self.rsi_min < _RSI_LOWER or self.rsi_max > _RSI_UPPER:
            raise ValueError("rsi_min/rsi_max must lie within [0, 100]")
        if self.rsi_min >= self.rsi_max:
            raise ValueError("rsi_min must be < rsi_max")
        if self.atr_pct_min < 0:
            raise ValueError("atr_pct_min must be >= 0")
        if self.atr_pct_min >= self.atr_pct_max:
            raise ValueError("atr_pct_min must be < atr_pct_max")
        if self.volume_ratio_min < 0:
            raise ValueError("volume_ratio_min must be >= 0")

    def to_dict(self) -> dict[str, str]:
        """Canonical string form in field order (JSON-safe, round-trips via from_dict)."""
        return {f.name: _canonical(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuleParams:
        """Build from ``to_dict`` output; values must be str or Decimal (no float)."""
        names = [f.name for f in fields(cls)]
        unknown = sorted(set(data) - set(names))
        missing = [n for n in names if n not in data]
        if unknown or missing:
            msg = f"invalid RuleParams keys: missing={missing} unknown={unknown}"
            raise ValueError(msg)
        values: dict[str, Decimal] = {}
        for name in names:
            raw = data[name]
            if isinstance(raw, Decimal):
                values[name] = raw
            elif isinstance(raw, str):
                try:
                    values[name] = Decimal(raw)
                except InvalidOperation as exc:
                    msg = f"{name} is not a decimal string"
                    raise ValueError(msg) from exc
            else:
                msg = f"{name} must be str or Decimal, got {type(raw).__name__}"
                raise TypeError(msg)
        return cls(**values)

    def param_hash(self) -> str:
        """SHA-256 over strategy version + canonical params (stable across runs)."""
        body = json.dumps(
            {"strategy_version": STRATEGY_VERSION, "params": self.to_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _no_trade(reason: NoTradeReason) -> SupervisorDecision:
    return SupervisorDecision(
        action=ProposalAction.NO_TRADE,
        direction=TradeDirection.FLAT,
        rationale=f"{NO_TRADE_PREFIX}{reason.value}",
        confidence_note="deterministic_rules_uncalibrated",
    )


def _as_decimal(value: Any) -> Decimal | None:
    """Parse a payload number; floats/bools and non-finite values are rejected."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (str, int)):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return None
    else:
        return None
    return parsed if parsed.is_finite() else None


def _single_evidence(
    package: EvidencePackage, analyst_type: AnalystType
) -> tuple[AnalystEvidence | None, int]:
    matches = [ev for ev in package.analyst_evidence if ev.analyst_type == analyst_type]
    return (matches[0] if len(matches) == 1 else None), len(matches)


def decide(package: EvidencePackage, params: RuleParams) -> SupervisorDecision:
    """Return BUY/long when every condition holds, else NO_TRADE/flat.

    Conditions (checked in this order; the first failure is named in
    ``rationale`` as ``no_trade:<reason>``):

    1. ``market_context`` present, not after ``package.as_of``, ``last_close > 0``,
       ``atr_14 > 0`` and ``atr_pct_of_close`` available.
    2. Exactly one technical evidence with status ``ok`` and ``trend == "up"``.
    3. Exactly one quantitative evidence with status ``ok`` and
       ``mean_simple_return > 0``.
    4. ``rsi_min <= rsi_14 <= rsi_max`` (technical payload).
    5. ``atr_pct_min <= atr_pct_of_close <= atr_pct_max``.
    6. ``volume_ratio >= volume_ratio_min`` (technical payload).

    Prices: ``entry = last_close``; ``stop = entry - k_atr_stop * atr_14``;
    ``take_profit = entry + r_target * (entry - stop)``. A non-positive stop
    yields NO_TRADE. Uses only data inside ``package`` (no look-ahead).
    """
    with localcontext(_DECIMAL_CONTEXT):
        return _decide(package, params)


def _decide(package: EvidencePackage, params: RuleParams) -> SupervisorDecision:
    ctx = package.market_context
    if ctx is None:
        return _no_trade(NoTradeReason.MARKET_CONTEXT_MISSING)
    if ctx.as_of > package.as_of:
        return _no_trade(NoTradeReason.MARKET_CONTEXT_AFTER_AS_OF)
    last_close = _as_decimal(ctx.last_close)
    if last_close is None or last_close <= 0:
        return _no_trade(NoTradeReason.LAST_CLOSE_INVALID)
    atr = _as_decimal(ctx.atr_14)
    if atr is None or atr <= 0:
        return _no_trade(NoTradeReason.ATR_UNAVAILABLE)
    atr_pct = _as_decimal(ctx.atr_pct_of_close)
    if atr_pct is None:
        return _no_trade(NoTradeReason.ATR_PCT_UNAVAILABLE)

    technical, n_technical = _single_evidence(package, AnalystType.TECHNICAL)
    if n_technical == 0:
        return _no_trade(NoTradeReason.TECHNICAL_MISSING)
    if technical is None:
        return _no_trade(NoTradeReason.TECHNICAL_AMBIGUOUS)
    if technical.status != EvidenceStatus.OK:
        return _no_trade(NoTradeReason.TECHNICAL_NOT_OK)
    if technical.payload.get("trend") != "up":
        return _no_trade(NoTradeReason.TREND_NOT_UP)

    quant, n_quant = _single_evidence(package, AnalystType.QUANTITATIVE)
    if n_quant == 0:
        return _no_trade(NoTradeReason.QUANTITATIVE_MISSING)
    if quant is None:
        return _no_trade(NoTradeReason.QUANTITATIVE_AMBIGUOUS)
    if quant.status != EvidenceStatus.OK:
        return _no_trade(NoTradeReason.QUANTITATIVE_NOT_OK)
    mean_ret = _as_decimal(quant.payload.get("mean_simple_return"))
    if mean_ret is None:
        return _no_trade(NoTradeReason.MEAN_RETURN_UNAVAILABLE)
    if mean_ret <= 0:
        return _no_trade(NoTradeReason.MEAN_RETURN_NOT_POSITIVE)

    rsi = _as_decimal(technical.payload.get("rsi_14"))
    if rsi is None:
        return _no_trade(NoTradeReason.RSI_UNAVAILABLE)
    if not (params.rsi_min <= rsi <= params.rsi_max):
        return _no_trade(NoTradeReason.RSI_OUT_OF_RANGE)

    if not (params.atr_pct_min <= atr_pct <= params.atr_pct_max):
        return _no_trade(NoTradeReason.ATR_PCT_OUT_OF_RANGE)

    volume_ratio = _as_decimal(technical.payload.get("volume_ratio"))
    if volume_ratio is None:
        return _no_trade(NoTradeReason.VOLUME_RATIO_UNAVAILABLE)
    if volume_ratio < params.volume_ratio_min:
        return _no_trade(NoTradeReason.VOLUME_RATIO_BELOW_MIN)

    entry = last_close
    stop = entry - params.k_atr_stop * atr
    if stop <= 0:
        return _no_trade(NoTradeReason.STOP_NOT_POSITIVE)
    take_profit = entry + params.r_target * (entry - stop)

    return SupervisorDecision(
        action=ProposalAction.BUY,
        direction=TradeDirection.LONG,
        entry_price=entry,
        stop_loss=stop,
        take_profit=take_profit,
        rationale=f"{BUY_RATIONALE};params={params.param_hash()[:16]}",
        invalidation=f"close_at_or_below_stop:{stop}",
        confidence_note="deterministic_rules_uncalibrated",
    )
