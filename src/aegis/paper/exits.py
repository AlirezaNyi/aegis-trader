"""Simulated protective exits (stop-loss / take-profit) for paper long positions.

Reduce-only simulation on finalized candles. Exits do **not** go through the Risk
Engine: they can only close an existing paper position at a level that Risk already
approved at entry. Live protective exits are out of scope and need an owner ADR
(see ADR 0006); nothing here touches an exchange.

Per candle (in ``open_time`` order, each candle evaluated at most once):

* ``open <= stop``  -> stop fills at the open (gap through the stop)
* ``low <= stop``   -> stop fills at the stop
* ``high >= target``-> target fills at the target
* stop and target both inside one candle -> stop first (conservative)

Stop fills get ``broker.slippage_bps`` against the seller; target fills get none.
Fees use ``broker.fee_bps``. The full position quantity is closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from aegis.paper.broker import PaperBroker
from aegis.paper.exceptions import PaperFillError
from aegis.paper.ledger import instrument_key_for
from aegis.paper.protection import ClosedTrade, ExitReason
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.orders import Fill, Order

_BPS = Decimal("10000")
_PRICE_QUANT = Decimal("0.00000001")


@dataclass(frozen=True)
class ExitFill:
    """One simulated protective exit."""

    order: Order
    fill: Fill
    exit_reason: ExitReason
    # Level that triggered the exit: the stop, the target, or the gapped open.
    trigger_price: Decimal
    gap: bool
    candle_open_time: datetime
    realized_pnl: Decimal
    r_multiple: Decimal | None
    trade: ClosedTrade


def stop_fill_price(trigger: Decimal, slippage_bps: Decimal) -> Decimal:
    """Sell-side stop price after slippage (worse for the seller, rounded down)."""
    slip = Decimal(trigger) * Decimal(slippage_bps) / _BPS
    return (Decimal(trigger) - slip).quantize(_PRICE_QUANT, rounding=ROUND_DOWN)


def evaluate_exit_candle(
    candle: Candle,
    *,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
) -> tuple[ExitReason, Decimal, bool] | None:
    """Return ``(reason, trigger_price, gap)`` for a long position, or None."""
    if stop_loss is not None:
        if candle.open <= stop_loss:
            return ExitReason.STOP_LOSS, candle.open, True
        if candle.low <= stop_loss:
            return ExitReason.STOP_LOSS, stop_loss, False
    if take_profit is not None and candle.high >= take_profit:
        return ExitReason.TAKE_PROFIT, take_profit, False
    return None


def _same_instrument(a: InstrumentRef, b: InstrumentRef) -> bool:
    return a.exchange == b.exchange and a.market_type == b.market_type and a.symbol == b.symbol


def apply_exits(
    broker: PaperBroker,
    instrument: InstrumentRef,
    candles: Sequence[Candle],
    *,
    now: datetime,
) -> list[ExitFill]:
    """Scan final candles after entry and simulate a stop / target exit.

    Eligible candles: ``is_final``, same instrument, ``close_time <= now`` (no
    look-ahead), ``close_time > entry fill time`` (the entry bar is never scanned;
    the next bar is, even when its ``open_time`` equals the replay fill clock), and
    ``open_time > last checked``. Idempotent: calling twice with the same candles
    yields at most one exit. Returns ``[]`` or a single full-close ``ExitFill``.
    """
    broker.assert_exit_path_allowed()
    clock = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    ledger = broker.ledger
    key = instrument_key_for(instrument)
    protection = ledger.protections.get(key)
    position = ledger.positions.get(key)
    if protection is None or position is None or position.quantity <= 0:
        return []
    if protection.stop_loss is None and protection.take_profit is None:
        return []

    last_checked = protection.last_checked_open_time
    by_open: dict[datetime, Candle] = {}
    for candle in candles:
        if not candle.is_final or not _same_instrument(candle.instrument, instrument):
            continue
        if candle.close_time > clock or candle.close_time <= protection.opened_at:
            continue
        if last_checked is not None and candle.open_time <= last_checked:
            continue
        by_open.setdefault(candle.open_time, candle)
    if not by_open:
        return []
    if len({c.interval for c in by_open.values()}) > 1:
        raise PaperFillError("apply_exits requires candles of a single interval")

    for open_time in sorted(by_open):
        candle = by_open[open_time]
        hit = evaluate_exit_candle(
            candle,
            stop_loss=protection.stop_loss,
            take_profit=protection.take_profit,
        )
        protection.last_checked_open_time = open_time
        if hit is None:
            continue
        reason, trigger, gap = hit
        price = (
            stop_fill_price(trigger, broker.slippage_bps)
            if reason == ExitReason.STOP_LOSS
            else trigger
        )
        # Package-private, reduce-only fill path (no public Risk bypass exists).
        order, fill, trade = broker._fill_protective_exit(
            instrument,
            price=price,
            reason=reason,
            now=clock,
            exchanged_at=min(candle.close_time, clock),
        )
        return [
            ExitFill(
                order=order,
                fill=fill,
                exit_reason=reason,
                trigger_price=trigger,
                gap=gap,
                candle_open_time=open_time,
                realized_pnl=trade.realized_pnl,
                r_multiple=trade.r_multiple,
                trade=trade,
            )
        ]
    return []
