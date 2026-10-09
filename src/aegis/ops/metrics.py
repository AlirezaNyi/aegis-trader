"""In-process ops metrics for readiness, scrape, and alert dry-run."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class OpsMetrics:
    """Counters and last-value gauges for Phase 8 monitoring signals.

    Not a full Prometheus client; exposes a Prometheus-compatible text scrape
    via :meth:`prometheus_text`. Call sites may instrument when wired; defaults
    stay at zero so paper boots remain safe.
    """

    # Data freshness / market path (mirrors MarketDataMetrics when wired)
    market_data_lag_ms: int | None = None
    market_data_stale: bool = False
    market_data_reconnects: int = 0

    # Model / Jev / Supervisor
    model_calls: int = 0
    model_errors: int = 0
    model_latency_ms_total: int = 0
    model_tokens_total: int = 0
    model_cost_units_total: float = 0.0
    provider_failures: int = 0

    # Risk
    risk_approve: int = 0
    risk_reject: int = 0
    risk_no_trade: int = 0

    # Orders / reconcile
    order_submit: int = 0
    order_ack: int = 0
    order_fill: int = 0
    order_unknown: int = 0
    order_reject: int = 0
    reconcile_runs: int = 0
    reconcile_failures: int = 0
    position_discrepancies: int = 0

    # Ops / host signals (injected or updated externally)
    kill_switch_active: bool = False
    disk_free_bytes: int | None = None
    shutting_down: bool = False

    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_model_call(
        self,
        *,
        latency_ms: int,
        tokens: int = 0,
        cost_units: float = 0.0,
        error: bool = False,
    ) -> None:
        with self._lock:
            self.model_calls += 1
            self.model_latency_ms_total += max(0, latency_ms)
            self.model_tokens_total += max(0, tokens)
            self.model_cost_units_total += max(0.0, cost_units)
            if error:
                self.model_errors += 1
                self.provider_failures += 1

    def record_risk(self, decision: str) -> None:
        key = decision.strip().upper()
        with self._lock:
            if key == "APPROVE":
                self.risk_approve += 1
            elif key == "REJECT":
                self.risk_reject += 1
            else:
                self.risk_no_trade += 1

    def record_order(self, event: str) -> None:
        key = event.strip().lower()
        with self._lock:
            if key == "submit":
                self.order_submit += 1
            elif key == "ack":
                self.order_ack += 1
            elif key == "fill":
                self.order_fill += 1
            elif key == "unknown":
                self.order_unknown += 1
            elif key == "reject":
                self.order_reject += 1

    def record_reconcile(self, *, ok: bool) -> None:
        with self._lock:
            self.reconcile_runs += 1
            if not ok:
                self.reconcile_failures += 1

    def record_position_discrepancy(self) -> None:
        with self._lock:
            self.position_discrepancies += 1

    def set_market_lag(self, lag_ms: int | None, *, stale: bool = False) -> None:
        with self._lock:
            self.market_data_lag_ms = lag_ms
            self.market_data_stale = stale

    def set_kill_switch(self, active: bool) -> None:
        with self._lock:
            self.kill_switch_active = active

    def set_disk_free(self, free_bytes: int | None) -> None:
        with self._lock:
            self.disk_free_bytes = free_bytes

    def set_shutting_down(self, value: bool) -> None:
        with self._lock:
            self.shutting_down = value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg_latency: float | None = None
            if self.model_calls > 0:
                avg_latency = self.model_latency_ms_total / self.model_calls
            return {
                "market_data_lag_ms": self.market_data_lag_ms,
                "market_data_stale": self.market_data_stale,
                "market_data_reconnects": self.market_data_reconnects,
                "model_calls": self.model_calls,
                "model_errors": self.model_errors,
                "model_latency_ms_avg": avg_latency,
                "model_tokens_total": self.model_tokens_total,
                "model_cost_units_total": self.model_cost_units_total,
                "provider_failures": self.provider_failures,
                "risk_approve": self.risk_approve,
                "risk_reject": self.risk_reject,
                "risk_no_trade": self.risk_no_trade,
                "order_submit": self.order_submit,
                "order_ack": self.order_ack,
                "order_fill": self.order_fill,
                "order_unknown": self.order_unknown,
                "order_reject": self.order_reject,
                "reconcile_runs": self.reconcile_runs,
                "reconcile_failures": self.reconcile_failures,
                "position_discrepancies": self.position_discrepancies,
                "kill_switch_active": self.kill_switch_active,
                "disk_free_bytes": self.disk_free_bytes,
                "shutting_down": self.shutting_down,
            }

    def prometheus_text(self) -> str:
        snap = self.snapshot()
        lines: list[str] = [
            "# HELP aegis_ops_info Aegis Phase 8 ops scrape (in-process).",
            "# TYPE aegis_ops_info gauge",
            'aegis_ops_info{service="aegis"} 1',
        ]
        gauges = (
            ("aegis_market_data_lag_ms", snap["market_data_lag_ms"]),
            ("aegis_market_data_stale", 1 if snap["market_data_stale"] else 0),
            ("aegis_market_data_reconnects_total", snap["market_data_reconnects"]),
            ("aegis_model_calls_total", snap["model_calls"]),
            ("aegis_model_errors_total", snap["model_errors"]),
            ("aegis_model_tokens_total", snap["model_tokens_total"]),
            ("aegis_model_cost_units_total", snap["model_cost_units_total"]),
            ("aegis_provider_failures_total", snap["provider_failures"]),
            ("aegis_risk_approve_total", snap["risk_approve"]),
            ("aegis_risk_reject_total", snap["risk_reject"]),
            ("aegis_risk_no_trade_total", snap["risk_no_trade"]),
            ("aegis_order_submit_total", snap["order_submit"]),
            ("aegis_order_ack_total", snap["order_ack"]),
            ("aegis_order_fill_total", snap["order_fill"]),
            ("aegis_order_unknown_total", snap["order_unknown"]),
            ("aegis_order_reject_total", snap["order_reject"]),
            ("aegis_reconcile_runs_total", snap["reconcile_runs"]),
            ("aegis_reconcile_failures_total", snap["reconcile_failures"]),
            ("aegis_position_discrepancies_total", snap["position_discrepancies"]),
            ("aegis_kill_switch_active", 1 if snap["kill_switch_active"] else 0),
            ("aegis_shutting_down", 1 if snap["shutting_down"] else 0),
        )
        for name, value in gauges:
            if value is None:
                continue
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")
        if snap["disk_free_bytes"] is not None:
            lines.append("# TYPE aegis_disk_free_bytes gauge")
            lines.append(f"aegis_disk_free_bytes {snap['disk_free_bytes']}")
        if snap["model_latency_ms_avg"] is not None:
            lines.append("# TYPE aegis_model_latency_ms_avg gauge")
            lines.append(f"aegis_model_latency_ms_avg {snap['model_latency_ms_avg']}")
        return "\n".join(lines) + "\n"
