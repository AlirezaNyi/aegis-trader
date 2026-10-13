"""Compact SupervisorDecision contract and policy-derived proposal defaults."""

from __future__ import annotations

from datetime import timedelta

from aegis.pipeline.audit import DecisionAuditStore
from aegis.pipeline.defaults import proposal_defaults_from_policy
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.risk.policy import default_draft_policy
from aegis.supervisor.decision import is_compact_decision


def test_defaults_from_owner_policy_v1() -> None:
    defaults = proposal_defaults_from_policy(owner_approved_policy_v1())
    assert defaults.entry_conditions == {"order_type": "LIMIT", "time_in_force": "GTC"}
    assert defaults.sizing == {"method": "fixed_notional", "notional": "2"}
    assert defaults.ttl == timedelta(milliseconds=30_000)


def test_defaults_from_draft_policy_are_empty() -> None:
    defaults = proposal_defaults_from_policy(default_draft_policy())
    assert defaults.entry_conditions == {}
    assert defaults.sizing == {}


def test_compact_detection() -> None:
    assert is_compact_decision({"action": "NO_TRADE"})
    assert not is_compact_decision({"proposal_id": "x", "action": "BUY"})
    assert not is_compact_decision(["action"])


def test_audit_store_without_database_is_disabled() -> None:
    store = DecisionAuditStore(database_url=None)
    assert store.enabled is False

