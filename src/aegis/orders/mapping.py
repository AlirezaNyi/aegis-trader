"""Re-export side mapping for Order Manager callers."""

from __future__ import annotations

from aegis.exchange.side_map import futures_side_and_position, spot_side

__all__ = ["futures_side_and_position", "spot_side"]
