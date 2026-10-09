"""Alert evaluation for ops dry-run (no external notification channel)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aegis.ops.metrics import OpsMetrics

# Engineering thresholds for dry-run only — not risk-policy numbers.
_DEFAULT_STALE_LAG_MS = 60_000
_DEFAULT_DISK_FREE_MIN_BYTES = 1_073_741_824  # 1 GiB


@dataclass(frozen=True)
class AlertSignal:
    """One evaluated alert condition."""

    name: str
    severity: str  # info | warning | critical
    firing: bool
    detail: str


class AlertEvaluator:
    """Evaluate Phase 8 alert conditions against :class:`OpsMetrics`.

    Does not send notifications — on-call channel remains UNRESOLVED in
    OPERATIONS.md. Use :meth:`dry_run` for TEST_PLAN alert wiring checks.
    """

    def __init__(
        self,
        *,
        stale_lag_ms: int = _DEFAULT_STALE_LAG_MS,
        disk_free_min_bytes: int = _DEFAULT_DISK_FREE_MIN_BYTES,
    ) -> None:
        self._stale_lag_ms = stale_lag_ms
        self._disk_free_min_bytes = disk_free_min_bytes

    def evaluate(self, metrics: OpsMetrics) -> list[AlertSignal]:
        snap = metrics.snapshot()
        signals: list[AlertSignal] = []

        lag = snap["market_data_lag_ms"]
        stale = bool(snap["market_data_stale"]) or (
            lag is not None and int(lag) >= self._stale_lag_ms
        )
        signals.append(
            AlertSignal(
                name="stale_market_data",
                severity="critical",
                firing=stale,
                detail=f"lag_ms={lag} stale_flag={snap['market_data_stale']}",
            )
        )
        signals.append(
            AlertSignal(
                name="provider_failures",
                severity="warning",
                firing=int(snap["provider_failures"]) > 0,
                detail=f"provider_failures={snap['provider_failures']}",
            )
        )
        signals.append(
            AlertSignal(
                name="unknown_order_state",
                severity="critical",
                firing=int(snap["order_unknown"]) > 0,
                detail=f"order_unknown={snap['order_unknown']}",
            )
        )
        signals.append(
            AlertSignal(
                name="position_discrepancy",
                severity="critical",
                firing=int(snap["position_discrepancies"]) > 0,
                detail=f"discrepancies={snap['position_discrepancies']}",
            )
        )
        signals.append(
            AlertSignal(
                name="reconcile_failure",
                severity="critical",
                firing=int(snap["reconcile_failures"]) > 0,
                detail=f"reconcile_failures={snap['reconcile_failures']}",
            )
        )
        disk = snap["disk_free_bytes"]
        disk_low = disk is not None and int(disk) < self._disk_free_min_bytes
        signals.append(
            AlertSignal(
                name="disk_exhaustion",
                severity="critical",
                firing=disk_low,
                detail=f"disk_free_bytes={disk} min={self._disk_free_min_bytes}",
            )
        )
        signals.append(
            AlertSignal(
                name="emergency_stop",
                severity="critical",
                firing=bool(snap["kill_switch_active"]),
                detail=f"kill_switch_active={snap['kill_switch_active']}",
            )
        )
        return signals

    def dry_run(self, metrics: OpsMetrics) -> dict[str, Any]:
        signals = self.evaluate(metrics)
        return {
            "channel": "dry_run",
            "notification_delivered": False,
            "note": "On-call channel UNRESOLVED; evaluation only.",
            "firing_count": sum(1 for s in signals if s.firing),
            "signals": [
                {
                    "name": s.name,
                    "severity": s.severity,
                    "firing": s.firing,
                    "detail": s.detail,
                }
                for s in signals
            ],
        }
