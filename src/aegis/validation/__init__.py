"""Market-data validation stage — freshness, integrity, finalized candles."""

from __future__ import annotations

from aegis.validation.stage import ValidationResult, validate_candles

__all__ = ["ValidationResult", "validate_candles"]
