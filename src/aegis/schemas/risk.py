"""Risk Engine decision contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from aegis.schemas.common import ApiModel


class RiskDecisionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class RiskDecision(ApiModel):
    decision: RiskDecisionType
    policy_version: str
    rules_evaluated: list[str] = Field(default_factory=list)
    rejection_reasons: list[dict[str, Any]] = Field(default_factory=list)
    validated_order_params: dict[str, Any] | None = None
    account_state_refs: list[str] = Field(default_factory=list)
    market_state_refs: list[str] = Field(default_factory=list)
    decided_at: datetime
    expires_at: datetime
    correlation_id: str
    proposal_id: UUID
