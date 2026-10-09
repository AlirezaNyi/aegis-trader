"""Deterministic Feature Engine (Phase 3)."""

from aegis.features.engine import (
    FEATURES_SCHEMA_VERSION,
    WARMUP,
    compute_features,
    select_candles,
    window_has_gap,
)

__all__ = [
    "FEATURES_SCHEMA_VERSION",
    "WARMUP",
    "compute_features",
    "select_candles",
    "window_has_gap",
]
