"""Hypothetical percent returns for BUY/SELL suggestions (not portfolio PnL)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from aegis.schemas.market import Candle
from aegis.schemas.proposal import ProposalAction, TradeDirection

_PCT_QUANT = Decimal("0.0001")


class SignalMarkStatus(StrEnum):
    OPEN = "open"
    STOPPED = "stopped"
    TARGET = "target"


@dataclass(frozen=True)
class SignalMark:
    status: SignalMarkStatus
    return_pct: Decimal
    exit_price: Decimal
    marked_open_time: datetime | None = None


def direction_from_action(action: ProposalAction | str) -> TradeDirection | None:
    value = action.value if isinstance(action, ProposalAction) else str(action)
    if value == ProposalAction.BUY.value:
        return TradeDirection.LONG
    if value == ProposalAction.SELL.value:
        return TradeDirection.SHORT
    return None


def is_trade_signal_action(action: ProposalAction | str | None) -> bool:
    if action is None:
        return False
    value = action.value if isinstance(action, ProposalAction) else str(action)
    return value in {ProposalAction.BUY.value, ProposalAction.SELL.value}


def compute_return_pct(
    entry: Decimal,
    exit_price: Decimal,
    direction: TradeDirection,
) -> Decimal:
    if entry == 0:
        return Decimal("0")
    if direction == TradeDirection.LONG:
        raw = (exit_price - entry) / entry * Decimal("100")
    else:
        raw = (entry - exit_price) / entry * Decimal("100")
    return raw.quantize(_PCT_QUANT)


def mark_signal_on_candles(
    *,
    entry_price: Decimal,
    direction: TradeDirection,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    candles_after_entry: Sequence[Candle],
) -> SignalMark:
    """Mark a BUY/SELL suggestion on subsequent final candles.

    Same-bar rule: if both stop and target are touched, stop wins.
    Without SL/TP the mark stays open on the latest close.
    """
    last_close = entry_price
    last_open: datetime | None = None
    for candle in candles_after_entry:
        if not candle.is_final:
            continue
        last_close = candle.close
        last_open = candle.open_time
        if direction == TradeDirection.LONG:
            hit_stop = stop_loss is not None and candle.low <= stop_loss
            hit_target = take_profit is not None and candle.high >= take_profit
            if hit_stop:
                assert stop_loss is not None
                return SignalMark(
                    status=SignalMarkStatus.STOPPED,
                    return_pct=compute_return_pct(entry_price, stop_loss, direction),
                    exit_price=stop_loss,
                    marked_open_time=candle.open_time,
                )
            if hit_target:
                assert take_profit is not None
                return SignalMark(
                    status=SignalMarkStatus.TARGET,
                    return_pct=compute_return_pct(entry_price, take_profit, direction),
                    exit_price=take_profit,
                    marked_open_time=candle.open_time,
                )
        else:
            hit_stop = stop_loss is not None and candle.high >= stop_loss
            hit_target = take_profit is not None and candle.low <= take_profit
            if hit_stop:
                assert stop_loss is not None
                return SignalMark(
                    status=SignalMarkStatus.STOPPED,
                    return_pct=compute_return_pct(entry_price, stop_loss, direction),
                    exit_price=stop_loss,
                    marked_open_time=candle.open_time,
                )
            if hit_target:
                assert take_profit is not None
                return SignalMark(
                    status=SignalMarkStatus.TARGET,
                    return_pct=compute_return_pct(entry_price, take_profit, direction),
                    exit_price=take_profit,
                    marked_open_time=candle.open_time,
                )

    return SignalMark(
        status=SignalMarkStatus.OPEN,
        return_pct=compute_return_pct(entry_price, last_close, direction),
        exit_price=last_close,
        marked_open_time=last_open,
    )


def aggregate_signal_returns(
    return_pcts: Sequence[Decimal],
    statuses: Sequence[SignalMarkStatus | str],
) -> dict[str, Decimal | int]:
    """Descriptive aggregate of percent returns — not a portfolio return."""
    wins = 0
    losses = 0
    open_count = 0
    total = Decimal("0")
    for pct, status in zip(return_pcts, statuses, strict=True):
        st = status.value if isinstance(status, SignalMarkStatus) else str(status)
        total += pct
        if st == SignalMarkStatus.OPEN.value:
            open_count += 1
        elif pct > 0:
            wins += 1
        elif pct < 0:
            losses += 1
    count = len(return_pcts)
    mean = (total / Decimal(count)).quantize(_PCT_QUANT) if count else Decimal("0")
    return {
        "count": count,
        "wins": wins,
        "losses": losses,
        "open": open_count,
        "mean_return_pct": mean,
        "sum_return_pct": total.quantize(_PCT_QUANT),
    }
