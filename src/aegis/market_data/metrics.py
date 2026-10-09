"""In-memory market-data health metrics for readiness and ops."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock


@dataclass
class MarketDataMetrics:
    reconnects: int = 0
    gaps: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    malformed: int = 0
    events_received: int = 0
    last_event_at: datetime | None = None
    last_exchange_event_at: datetime | None = None
    connected: bool = False
    _lock: Lock = field(default_factory=Lock, repr=False)

    def mark_connected(self, connected: bool) -> None:
        with self._lock:
            self.connected = connected

    def record_reconnect(self) -> None:
        with self._lock:
            self.reconnects += 1

    def record_event(self, *, received_at: datetime, exchange_event_at: datetime | None) -> None:
        with self._lock:
            self.events_received += 1
            self.last_event_at = received_at
            if exchange_event_at is not None:
                self.last_exchange_event_at = exchange_event_at

    def record_gap(self) -> None:
        with self._lock:
            self.gaps += 1

    def record_duplicate(self) -> None:
        with self._lock:
            self.duplicates += 1

    def record_out_of_order(self) -> None:
        with self._lock:
            self.out_of_order += 1

    def record_malformed(self) -> None:
        with self._lock:
            self.malformed += 1

    def lag_ms(self, now: datetime) -> int | None:
        with self._lock:
            if self.last_event_at is None:
                return None
            return max(0, int((now - self.last_event_at).total_seconds() * 1000))

    def snapshot(self, now: datetime) -> dict[str, object]:
        with self._lock:
            return {
                "connected": self.connected,
                "reconnects": self.reconnects,
                "gaps": self.gaps,
                "duplicates": self.duplicates,
                "out_of_order": self.out_of_order,
                "malformed": self.malformed,
                "events_received": self.events_received,
                "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
                "last_exchange_event_at": (
                    self.last_exchange_event_at.isoformat() if self.last_exchange_event_at else None
                ),
                "event_lag_ms": (
                    max(0, int((now - self.last_event_at).total_seconds() * 1000))
                    if self.last_event_at
                    else None
                ),
            }
