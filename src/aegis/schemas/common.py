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
