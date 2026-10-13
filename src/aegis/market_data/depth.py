"""Parse public Toobit order-book (depth) snapshots into a top-of-book view.

Verified shape (official docs + live public request 2026-10-13, spot ADAUSDT and
futures BTC-SWAP-USDT)::

    {"t": <ms matching time>, "b": [[price, qty], ...], "a": [[price, qty], ...]}

Prices/quantities are decimal strings. Sort order is not documented (live data
showed bids descending, asks ascending), so best bid/ask are computed as max/min
rather than taken by index.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from aegis.market_data.timeutil import ms_to_datetime
from aegis.schemas.market import InstrumentRef, OrderBookTop

_BPS = Decimal("10000")


class MalformedDepthError(ValueError):
    """Depth payload does not match the verified `t`/`b`/`a` shape."""


class EmptyOrderBookError(MalformedDepthError):
    """One side of the book is empty; no spread can be computed."""


def _parse_levels(raw: Any, side: str) -> list[tuple[Decimal, Decimal]]:
    if not isinstance(raw, list):
        raise MalformedDepthError(f"depth field {side!r} must be a list")
    levels: list[tuple[Decimal, Decimal]] = []
    for level in raw:
        if not isinstance(level, list) or len(level) < 2:
            raise MalformedDepthError(f"depth level on {side!r} must be [price, qty]")
        try:
            price = Decimal(str(level[0]))
            qty = Decimal(str(level[1]))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise MalformedDepthError(f"non-numeric depth level on {side!r}") from exc
        if not price.is_finite() or not qty.is_finite() or price <= 0 or qty < 0:
            raise MalformedDepthError(f"invalid depth level on {side!r}")
        levels.append((price, qty))
    return levels


def parse_depth_top(
    payload: Any,
    *,
    instrument: InstrumentRef,
    received_at: datetime,
) -> OrderBookTop:
    if not isinstance(payload, dict):
        raise MalformedDepthError("depth response must be an object")
    if "b" not in payload or "a" not in payload:
        raise MalformedDepthError("depth response missing 'b' or 'a'")
    bids = _parse_levels(payload["b"], "b")
    asks = _parse_levels(payload["a"], "a")
    if not bids or not asks:
        raise EmptyOrderBookError("depth response has an empty side")
    best_bid = max(bids, key=lambda lv: lv[0])
    best_ask = min(asks, key=lambda lv: lv[0])
    if best_bid[0] > best_ask[0]:
        raise MalformedDepthError("crossed book: best bid above best ask")
    exchange_time: datetime | None = None
    raw_t = payload.get("t")
    if raw_t is not None:
        try:
            exchange_time = ms_to_datetime(raw_t)
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise MalformedDepthError("depth 't' is not a millisecond timestamp") from exc
    return OrderBookTop(
        instrument=instrument,
        best_bid=best_bid[0],
        best_bid_qty=best_bid[1],
        best_ask=best_ask[0],
        best_ask_qty=best_ask[1],
        exchange_time=exchange_time,
        received_at=received_at,
    )


def spread_bps(top: OrderBookTop) -> Decimal:
    """Quoted spread in basis points: (ask - bid) / mid * 10000."""
    mid = (top.best_ask + top.best_bid) / 2
    if mid <= 0:
        raise MalformedDepthError("mid price must be positive")
    return (top.best_ask - top.best_bid) / mid * _BPS
