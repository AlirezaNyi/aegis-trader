"""Shared enums and primitives."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    # Research/backtest timeframe (Binance archive). Toobit 1h klines are not yet
    # verified in docs/API_CONTRACTS.md; do not use for live soak until verified.
    H1 = "1h"
    # Research higher-timeframe regime (Binance archive); Toobit 1d unverified.
    D1 = "1d"


class LedgerKind(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class EvidenceStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class IdModel(ApiModel):
    id: UUID
