"""Deterministic candle replay with look-ahead guard."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime

from aegis.schemas.market import Candle


class LookAheadError(RuntimeError):
    """Raised when code attempts to read candles after as_of."""


class CandleReplay:
    """Replay finalized (or provided) candles with a strict as_of cursor.

    Only candles with ``close_time <= as_of`` are visible. Requests for future
    bars raise ``LookAheadError``.
    """

    def __init__(
        self,
        candles: Sequence[Candle],
        *,
        seed: int = 0,
    ) -> None:
        self._candles = sorted(candles, key=lambda c: c.close_time)
        self._seed = seed
        self._rng = random.Random(seed)
        self._as_of: datetime | None = None
        self._index = -1

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def as_of(self) -> datetime | None:
        return self._as_of

    @property
    def all_candles(self) -> list[Candle]:
        return list(self._candles)

    def reset(self, *, seed: int | None = None) -> None:
        if seed is not None:
            self._seed = seed
        self._rng = random.Random(self._seed)
        self._as_of = None
        self._index = -1

    def set_as_of(self, as_of: datetime) -> None:
        if as_of.tzinfo is None and self._candles and self._candles[0].close_time.tzinfo:
            raise LookAheadError("as_of must be timezone-aware when candles are aware")
        self._as_of = as_of
        self._index = -1
        for i, candle in enumerate(self._candles):
            if candle.close_time <= as_of:
                self._index = i
            else:
                break

    def advance_to(self, candle: Candle) -> None:
        self.set_as_of(candle.close_time)

    def visible_candles(self) -> list[Candle]:
        if self._as_of is None:
            return []
        return [c for c in self._candles if c.close_time <= self._as_of]

    def current_candle(self) -> Candle | None:
        if self._index < 0:
            return None
        return self._candles[self._index]

    def candles_in_window(self, start: datetime, end: datetime) -> list[Candle]:
        """Return candles whose close_time is within [start, end] (inclusive).

        Does not bypass look-ahead: if as_of is set, end is clamped to as_of.
        """
        effective_end = end
        if self._as_of is not None and self._as_of < end:
            effective_end = self._as_of
        return [
            c
            for c in self._candles
            if start <= c.close_time <= effective_end
        ]

    def require_no_future(self, candle: Candle) -> None:
        if self._as_of is None:
            raise LookAheadError("as_of not set; cannot access candles")
        if candle.close_time > self._as_of:
            raise LookAheadError(
                f"look-ahead refused: candle close_time={candle.close_time.isoformat()} "
                f"> as_of={self._as_of.isoformat()}"
            )

    def random(self) -> random.Random:
        """Seeded RNG for reproducible strategy hooks (not market data)."""
        return self._rng
