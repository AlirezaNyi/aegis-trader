"""Fail-open persistence of walk-forward trials to ``backtest_runs`` / ``strategy_experiments``.

One ``backtest_runs`` row per evaluated (symbol, window, grid point) and one
``strategy_experiments`` row per walk-forward run. Any database error is
recorded (exception class name only, never the URL) and the caller continues;
the JSON report file remains the primary artifact.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from aegis.backtest.walk_forward import (
    DISCLAIMER,
    WALK_FORWARD_VERSION,
    TrialRecord,
    WalkForwardResult,
)
from aegis.db.models import BacktestRunRow, StrategyExperimentRow
from aegis.db.session import create_engine_from_url


def _dataset_version(record: TrialRecord, exchange_label: str, timeframe: str) -> str:
    # Column is String(64): label + short data hash (full hash kept in assumptions).
    return f"{exchange_label}:{record.symbol}:{timeframe}:{record.data_hash[:16]}"[:64]


def trial_row(record: TrialRecord, report: dict[str, Any]) -> BacktestRunRow:
    data = report["data"][record.symbol]
    config = report["config"]
    metrics = record.metrics.model_dump(mode="json")
    is_train = record.tag == "train"
    return BacktestRunRow(
        strategy_id=report["strategy"]["id"],
        strategy_version=report["strategy"]["version"],
        dataset_version=_dataset_version(record, data["exchange_label"], config["timeframe"]),
        seed=0,
        software_version=WALK_FORWARD_VERSION,
        splits={
            "walk_forward_run_id": report["run_id"],
            "tag": record.tag,
            "fold": record.fold,
            "window_start": record.window.start.isoformat(),
            "window_end": record.window.end.isoformat(),
        },
        assumptions={
            "walk_forward_run_id": report["run_id"],
            "symbol": record.symbol,
            "param_hash": record.param_hash,
            "params": record.params,
            "selected": record.selected,
            "data_hash": record.data_hash,
            "data_source": data["data_source"],
            "spread_bps": config["spread_bps"],
            "fee_bps": config["fee_bps"],
            "slippage_bps": config["slippage_bps"],
            "risk_policy_version": config["risk_policy_version"],
        },
        train_metrics=metrics if is_train else {},
        validation_metrics={},
        oos_metrics={} if is_train else metrics,
        uncertainty_notes=report["uncertainty_notes"],
        disclaimer=DISCLAIMER,
    )


def experiment_row(report: dict[str, Any]) -> StrategyExperimentRow:
    run_id = report["run_id"]
    return StrategyExperimentRow(
        strategy_id=report["strategy"]["id"],
        # Unique per (strategy_id, strategy_version): one row per walk-forward run.
        strategy_version=f"{report['strategy']['version']}+wf-{run_id[:8]}"[:64],
        statement=(
            "Walk-forward tuning of deterministic rule params; selection on train "
            "windows only, stitched OOS reported, holdout evaluated once."
        ),
        parameters={
            "run_id": run_id,
            "config": report["config"],
            "grid": report["grid"],
            "selected_final": report["holdout"].get("params"),
        },
        status="completed",
        leakage_controls={
            "folds": [{"train": f["train"], "test": f["test"]} for f in report["folds"]],
            "holdout": report["holdout"]["window"],
            "controls": report["assumptions"]["leakage_controls"],
        },
    )


def persist_walk_forward(database_url: str | None, result: WalkForwardResult) -> dict[str, Any]:
    """Write all trial rows + the experiment row in one transaction. Never raises."""
    if not database_url:
        return {"ok": False, "error": "no_database_url", "rows": 0}
    try:
        engine = create_engine_from_url(database_url)
        factory: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
        rows = [trial_row(r, result.report) for r in result.trial_records]
        with factory() as session, session.begin():
            session.add_all(rows)
            session.add(experiment_row(result.report))
        engine.dispose()
        return {"ok": True, "error": None, "rows": len(rows) + 1}
    except (SQLAlchemyError, ImportError, ValueError, OSError) as exc:
        return {"ok": False, "error": type(exc).__name__, "rows": 0}
