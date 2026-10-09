"""Append-only audit event contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from aegis.schemas.common import ApiModel


class AuditEventType(StrEnum):
    APP_START = "app_start"
    CONFIG_LOADED = "config_loaded"
    READY_CHECK = "ready_check"
    LIVE_GATE_DENIED = "live_gate_denied"
    RISK_DECISION = "risk_decision"
    ORDER_TRANSITION = "order_transition"
    MODE_CHANGE = "mode_change"


class AuditEvent(ApiModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: AuditEventType
    actor: str = "system"
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
