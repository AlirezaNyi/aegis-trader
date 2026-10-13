"""Paper operator dashboard — read-only HTML/JSON, fail-open journal."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.main import create_app
from aegis.pipeline.activity import ActivityStage
from aegis.pipeline.journal import PaperCycleJournal, ReviewRecord, build_review_record
from aegis.pipeline.signal_marks import SignalMark, SignalMarkStatus


def _settings(**kwargs: object) -> Settings:
    clear_settings_cache()
    base: dict[str, object] = {
        "trading_mode": TradingMode.PAPER,
        "live_armed": False,
        "kill_switch": False,
        "require_database": False,
        "paper_soak_enabled": False,
        # Unreachable URL — journal must fail open.
        "database_url": "postgresql+psycopg://nobody:bad@127.0.0.1:1/nope",
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def test_dashboard_page_and_json_no_secrets() -> None:
    client = TestClient(create_app(_settings()))
    page = client.get("/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "داشبورد" in page.text
    assert "api_key" not in page.text.lower()

    data = client.get("/ops/dashboard")
    assert data.status_code == 200
    body = data.json()
    assert body["soak_running"] is False
    assert "activity" in body
    assert "signal_aggregate" in body
    assert "paper_account" in body
    blob = str(body).lower()
    assert "api_key" not in blob
    assert "authorization" not in blob
    assert "toobit_api_secret" not in blob


def test_journal_fail_open_keeps_memory() -> None:
    journal = PaperCycleJournal(
        database_url="postgresql+psycopg://nobody:bad@127.0.0.1:1/nope"
    )
    record = build_review_record(
        correlation_id="c1",
        symbol="ADAUSDT",
        timeframe="1m",
        final_open_time=datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
        summary={"owner_hint": "test", "supervisor_model_meta": {"api_key": "SECRET"}},
        proposal_action="BUY",
        proposal_direction="long",
        risk_decision="REJECT",
        entry_price=Decimal("0.50"),
        stop_loss=Decimal("0.45"),
        take_profit=Decimal("0.60"),
        paper_order_id=None,
    )
    stored = journal.append(record)
    assert stored.db_persisted is False
    assert journal.last_persist_error is not None or stored.db_persisted is False
    assert "SECRET" not in str(stored.summary)
    assert "api_key" not in stored.summary.get("supervisor_model_meta", {})
    rows = journal.list_reviews()
    assert len(rows) == 1
    assert rows[0].proposal_action == "BUY"


def test_journal_update_mark_in_memory() -> None:
    journal = PaperCycleJournal(database_url=None)
    rid = uuid4()
    now = datetime.now(tz=UTC)
    journal.append(
        ReviewRecord(
            review_id=rid,
            correlation_id="c2",
            symbol="BTCUSDT",
            timeframe="1m",
            final_open_time=now,
            proposal_action="SELL",
            proposal_direction="short",
            risk_decision="REJECT",
            entry_price=Decimal("100"),
            stop_loss=Decimal("105"),
            take_profit=Decimal("90"),
            mark_status=SignalMarkStatus.OPEN.value,
            return_pct=Decimal("0"),
            exit_price=Decimal("100"),
            paper_order_id=None,
            summary={},
            created_at=now,
            updated_at=now,
        )
    )
    updated = journal.update_mark(
        rid,
        SignalMark(
            status=SignalMarkStatus.TARGET,
            return_pct=Decimal("10"),
            exit_price=Decimal("90"),
        ),
    )
    assert updated is not None
    assert updated.mark_status == "target"
    assert updated.return_pct == Decimal("10")
    agg = journal.signal_aggregate()
    assert agg["count"] == 1
    assert agg["wins"] == 1


def test_activity_exposed_on_dashboard() -> None:
    app = create_app(_settings())
    app.state.activity.set(
        ActivityStage.ANALYSTS,
        symbol="ADAUSDT",
        timeframe="1m",
        detail="running",
    )
    client = TestClient(app)
    body = client.get("/ops/dashboard").json()
    assert body["activity"]["stage"] == "analysts"
    assert body["activity"]["symbol"] == "ADAUSDT"


def test_dashboard_skip_does_not_overwrite_last_cycle_with_journal() -> None:
    """A skipped poll must not pull an unrelated journal summary into last_cycle."""
    from aegis.interfaces.market_data import NullMarketDataPort
    from aegis.pipeline.soak import PaperSoakRunner, SoakPollOutcome
    from aegis.schemas.common import MarketType, Timeframe
    from aegis.schemas.market import InstrumentRef

    app = create_app(_settings())
    journal: PaperCycleJournal = app.state.paper_journal
    open_time = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    journal.append(
        build_review_record(
            correlation_id="older-cycle",
            symbol="ADAUSDT",
            timeframe="1m",
            final_open_time=open_time,
            summary={"owner_hint": "prior full cycle"},
            proposal_action="NO_TRADE",
            proposal_direction=None,
            risk_decision="REJECT",
            entry_price=Decimal("0.50"),
            stop_loss=None,
            take_profit=None,
            paper_order_id=None,
        )
    )
    runner = PaperSoakRunner(
        market_data=NullMarketDataPort(),
        deps=app.state.paper_cycle_deps,
        instruments=(InstrumentRef(market_type=MarketType.SPOT, symbol="ADAUSDT"),),
        timeframe=Timeframe.M1,
        journal=journal,
    )
    runner.last_outcome = SoakPollOutcome(
        ran_cycle=False,
        skipped_reason="already_processed",
        final_open_time=open_time,
        symbol="ADAUSDT",
    )
    runner.last_outcomes_by_symbol["ADAUSDT"] = runner.last_outcome
    app.state.paper_soak_runner = runner

    body = TestClient(app).get("/ops/dashboard").json()
    assert body["last_cycle"]["ran_cycle"] is False
    assert body["last_cycle"]["skipped_reason"] == "already_processed"
    assert body["last_cycle"].get("from_journal") is not True
    assert body["last_cycle"]["cycle"] is None
    assert "ADAUSDT" in body["last_outcomes_by_symbol"]
    assert body["last_outcomes_by_symbol"]["ADAUSDT"]["skipped_reason"] == (
        "already_processed"
    )
