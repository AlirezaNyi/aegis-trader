"""Read-only paper operator dashboard (HTML + JSON). No live controls."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response

from aegis.paper.equity import PaperEquityTracker, compute_paper_equity, instrument_mark_key
from aegis.paper.ledger import PaperLedger
from aegis.pipeline.activity import ActivityState
from aegis.pipeline.journal import PaperCycleJournal
from aegis.pipeline.review import summarize_soak_outcome
from aegis.pipeline.soak import PaperSoakRunner

router = APIRouter(tags=["dashboard"])

_HTML_PATH = Path(__file__).resolve().parent / "static" / "dashboard.html"


@router.get("/")
def dashboard_page() -> Response:
    html = _HTML_PATH.read_text(encoding="utf-8")
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get("/ops/dashboard")
def dashboard_payload(request: Request) -> dict[str, Any]:
    activity: ActivityState | None = getattr(request.app.state, "activity", None)
    journal: PaperCycleJournal | None = getattr(request.app.state, "paper_journal", None)
    runner: PaperSoakRunner | None = getattr(request.app.state, "paper_soak_runner", None)
    ledger: PaperLedger | None = getattr(request.app.state, "paper_ledger", None)
    deps = getattr(request.app.state, "paper_cycle_deps", None)
    runtime = getattr(request.app.state, "runtime", None)

    reviews = (
        []
        if journal is None
        else [r.to_public_dict() for r in journal.list_reviews(limit=40)]
    )
    signals = [
        r
        for r in reviews
        if r.get("is_trade_signal")
    ]
    aggregate = (
        {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "open": 0,
            "mean_return_pct": "0",
            "sum_return_pct": "0",
            "disclaimer": (
                "جمع درصدها بازدهٔ یک سبد نیست؛ سایز سرمایه ساخته نمی‌شود. "
                "Sum of percents is not a portfolio return."
            ),
        }
        if journal is None
        else journal.signal_aggregate()
    )

    last_cycle: dict[str, Any] | None = None
    if runner is not None and runner.last_outcome is not None:
        last_cycle = summarize_soak_outcome(runner.last_outcome)
        # Prefer detailed journal summary when available.
        if reviews:
            last_cycle = {
                **last_cycle,
                "cycle": reviews[0].get("summary"),
                "from_journal": True,
            }

    paper_account = _paper_account_snapshot(
        ledger=ledger,
        equity_tracker=None if deps is None else deps.equity_tracker,
        runner=runner,
    )

    persist_warning = None if journal is None else journal.last_persist_error

    return {
        "service": "aegis",
        "mode": (
            None
            if runtime is None
            else getattr(runtime.trading_mode, "value", runtime.trading_mode)
        ),
        "live_armed": None if runtime is None else runtime.live_armed,
        "kill_switch": None if runtime is None else runtime.kill_switch,
        "soak_running": runner is not None,
        "cycles_run": 0 if runner is None else runner.cycles_run,
        "symbols": [] if runner is None else runner.symbols,
        "timeframe": None if runner is None else runner.timeframe.value,
        "activity": (
            {
                "stage": "idle",
                "symbol": None,
                "timeframe": None,
                "final_open_time": None,
                "correlation_id": None,
                "detail": "paper_soak_not_running",
                "updated_at": None,
            }
            if activity is None
            else activity.snapshot()
        ),
        "last_cycle": last_cycle,
        "reviews": reviews,
        "signals": signals,
        "signal_aggregate": aggregate,
        "paper_account": paper_account,
        "persist_warning": persist_warning,
        "limitations": [
            "بازدهٔ درصدی سیگنال فرضی است؛ سایز سرمایه ساخته نمی‌شود.",
            "اگر پروسه چند کندل خاموش باشد، برخورد استاپ داخل شکاف دیده نمی‌شود.",
            "برچسب‌های تحلیلگر (trend / RSI) سیگنال معامله نیستند.",
            "سود/ضرر دلاری فقط برای حساب کاغذی واقعی است.",
        ],
    }


def _paper_account_snapshot(
    *,
    ledger: PaperLedger | None,
    equity_tracker: PaperEquityTracker | None,
    runner: PaperSoakRunner | None,
) -> dict[str, Any]:
    if ledger is None:
        return {
            "available": False,
            "cash": None,
            "equity": None,
            "initial_equity": None,
            "pnl_dollar": None,
            "positions": [],
        }
    marks: dict[str, Decimal] = {}
    if runner is not None and runner.last_outcome is not None:
        outcome = runner.last_outcome
        if outcome.result is not None and outcome.result.validation.candles:
            last = outcome.result.validation.candles[-1]
            marks[instrument_mark_key(last.instrument)] = last.close
    equity = compute_paper_equity(ledger, marks=marks)
    initial = None if equity_tracker is None else equity_tracker.initial_equity
    pnl = None if initial is None else equity - initial
    positions = []
    for pos in ledger.positions.values():
        if pos.quantity == 0:
            continue
        positions.append(
            {
                "symbol": pos.instrument.symbol,
                "market_type": pos.instrument.market_type.value,
                "quantity": str(pos.quantity),
                "entry_price": None if pos.entry_price is None else str(pos.entry_price),
                "unrealized_pnl": (
                    None if pos.unrealized_pnl is None else str(pos.unrealized_pnl)
                ),
            }
        )
    return {
        "available": True,
        "cash": str(ledger.get_balance()),
        "equity": str(equity),
        "initial_equity": None if initial is None else str(initial),
        "pnl_dollar": None if pnl is None else str(pnl),
        "positions": positions,
    }
