"""Clock abstraction for deterministic tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return current UTC time."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
