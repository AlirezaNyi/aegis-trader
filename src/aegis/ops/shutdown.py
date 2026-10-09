"""Graceful shutdown gate for readiness and restart recovery."""

from __future__ import annotations

from threading import Lock


class ShutdownGate:
    """Marks the process as draining so ``/ready`` fails closed for new work."""

    def __init__(self) -> None:
        self._shutting_down = False
        self._lock = Lock()

    def begin_shutdown(self) -> None:
        with self._lock:
            self._shutting_down = True

    @property
    def shutting_down(self) -> bool:
        with self._lock:
            return self._shutting_down
