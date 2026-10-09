"""Versioned risk policy snapshot — financial params start UNAPPROVED."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ParamStatus(StrEnum):
    UNAPPROVED = "UNAPPROVED"
    APPROVED = "APPROVED"


@dataclass(frozen=True)
class PolicyParam:
    id: str
    status: ParamStatus
    value: Any | None = None
    unit: str | None = None


# Financial / allowlist parameters from docs/RISK_POLICY.md §2 (all UNAPPROVED in draft).
FINANCIAL_PARAM_IDS: tuple[str, ...] = (
    "RP-INSTRUMENT-ALLOWLIST-SPOT",
    "RP-INSTRUMENT-ALLOWLIST-FUTURES",
    "RP-MARKETS-ALLOWED",
    "RP-DIRECTIONS-ALLOWED",
    "RP-MAX-LEVERAGE",
    "RP-MAX-NOTIONAL-PER-ORDER",
    "RP-MAX-NOTIONAL-PER-INSTRUMENT",
    "RP-MAX-AGGREGATE-NOTIONAL",
    "RP-MAX-OPEN-POSITIONS",
    "RP-MAX-PENDING-ORDERS",
    "RP-DAILY-LOSS-LIMIT",
    "RP-DRAWDOWN-LIMIT",
    "RP-SIZING-METHOD",
    "RP-STOP-LOSS-REQUIRED",
    "RP-STOP-LOSS-MAX-DISTANCE",
    "RP-TAKE-PROFIT-POLICY",
    "RP-ORDER-TYPES-ALLOWED",
    "RP-TIME-IN-FORCE-ALLOWED",
    "RP-MAX-SPREAD",
    "RP-MIN-LIQUIDITY",
    "RP-MAX-SLIPPAGE-MODEL",
    "RP-MAX-FEE-ESTIMATE",
    "RP-FUNDING-CONSTRAINT",
    "RP-DATA-FRESHNESS-MS",
    "RP-PROPOSAL-TTL-MS",
    "RP-COOLDOWN-AFTER-LOSS",
    "RP-STRATEGY-ALLOWLIST",
    "RP-DUPLICATE-WINDOW",
    "RP-EMERGENCY-CANCEL",
    "RP-EMERGENCY-PROTECT",
)

# Required for new-trade BUY/SELL evaluation (emergency cancel/protect are separate — ADR 0006).
NEW_TRADE_REQUIRED_PARAM_IDS: tuple[str, ...] = tuple(
    pid
    for pid in FINANCIAL_PARAM_IDS
    if pid not in {"RP-EMERGENCY-CANCEL", "RP-EMERGENCY-PROTECT"}
)

ENGINEERING_DEFAULT_PARAMS: dict[str, PolicyParam] = {
    "RP-MODE-DEFAULT": PolicyParam(
        id="RP-MODE-DEFAULT",
        status=ParamStatus.APPROVED,
        value="paper",
        unit="enum",
    ),
    "RP-LIVE-ARMED-DEFAULT": PolicyParam(
        id="RP-LIVE-ARMED-DEFAULT",
        status=ParamStatus.APPROVED,
        value=False,
        unit="bool",
    ),
    "RP-KILL-SWITCH-DEFAULT": PolicyParam(
        id="RP-KILL-SWITCH-DEFAULT",
        status=ParamStatus.APPROVED,
        value=False,
        unit="bool",
    ),
}


@dataclass(frozen=True)
class RiskPolicy:
    """Immutable policy snapshot. Mutate only via ``with_approved_params`` (new object)."""

    policy_version: str = "0.1-draft"
    params: Mapping[str, PolicyParam] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Freeze the mapping so in-process callers cannot flip UNAPPROVED → APPROVED.
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    def get(self, param_id: str) -> PolicyParam | None:
        return self.params.get(param_id)

    @property
    def is_draft(self) -> bool:
        """True when this snapshot is a draft (not an owner-signed live policy)."""
        return "draft" in self.policy_version.lower()


def default_draft_policy() -> RiskPolicy:
    """Draft policy: engineering defaults APPROVED; all financial limits UNAPPROVED."""
    params: dict[str, PolicyParam] = dict(ENGINEERING_DEFAULT_PARAMS)
    for param_id in FINANCIAL_PARAM_IDS:
        params[param_id] = PolicyParam(
            id=param_id,
            status=ParamStatus.UNAPPROVED,
            value=None,
            unit=None,
        )
    return RiskPolicy(policy_version="0.1-draft", params=params)


def require_approved(policy: RiskPolicy, param_id: str) -> tuple[Any | None, str | None]:
    """Return (value, None) when APPROVED; otherwise (None, failure detail)."""
    param = policy.get(param_id)
    if param is None:
        return None, f"{param_id} missing from policy snapshot"
    if param.status != ParamStatus.APPROVED:
        return None, f"{param_id} is UNAPPROVED"
    if param.value is None:
        return None, f"{param_id} is APPROVED but value is None"
    return param.value, None


def with_approved_params(
    base: RiskPolicy,
    mapping: dict[str, Any],
    *,
    policy_version: str | None = None,
) -> RiskPolicy:
    """Copy *base* and mark listed params APPROVED (tests / owner-loaded versions only)."""
    params = {k: deepcopy(v) for k, v in base.params.items()}
    for param_id, value in mapping.items():
        existing = params.get(param_id)
        unit = existing.unit if existing is not None else None
        params[param_id] = PolicyParam(
            id=param_id,
            status=ParamStatus.APPROVED,
            value=value,
            unit=unit,
        )
    return RiskPolicy(
        policy_version=policy_version or base.policy_version,
        params=params,
    )
