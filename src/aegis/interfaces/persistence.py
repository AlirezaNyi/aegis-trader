"""Persistence port — SQLAlchemy UnitOfWork wired in later phases."""

from __future__ import annotations

from typing import Protocol


class UnitOfWork(Protocol):
    def commit(self) -> None:
        """Commit the current unit of work."""

    def rollback(self) -> None:
        """Roll back the current unit of work."""
