"""In-memory operator activity — what the paper soak/cycle is doing now."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Any


class ActivityStage(StrEnum):
    IDLE = "idle"
    FETCHING = "fetching"
    VALIDATING = "validating"
    FEATURES = "features"
    ANALYSTS = "analysts"
    EVIDENCE = "evidence"
    JEV = "jev"
    SUPERVISOR = "supervisor"
    RISK = "risk"
    PAPER = "paper"
    SLEEPING = "sleeping"


@dataclass
class ActivityState:
    """Thread-safe snapshot of the current paper pipeline stage."""

    stage: ActivityStage = ActivityStage.IDLE
    symbol: str | None = None
    timeframe: str | None = None
    final_open_time: datetime | None = None
    correlation_id: str | None = None
    detail: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def set(
        self,
        stage: ActivityStage,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        final_open_time: datetime | None = None,
        correlation_id: str | None = None,
        detail: str | None = None,
        clear_context: bool = False,
    ) -> None:
        with self._lock:
            self.stage = stage
            self.updated_at = datetime.now(tz=UTC)
            if clear_context:
                self.symbol = symbol
                self.timeframe = timeframe
                self.final_open_time = final_open_time
                self.correlation_id = correlation_id
                self.detail = detail
                return
            if symbol is not None:
                self.symbol = symbol
            if timeframe is not None:
                self.timeframe = timeframe
            if final_open_time is not None:
                self.final_open_time = final_open_time
            if correlation_id is not None:
                self.correlation_id = correlation_id
            if detail is not None:
                self.detail = detail

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "stage": self.stage.value,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "final_open_time": (
                    None
                    if self.final_open_time is None
                    else self.final_open_time.isoformat()
                ),
                "correlation_id": self.correlation_id,
                "detail": self.detail,
                "updated_at": self.updated_at.isoformat(),
            }
