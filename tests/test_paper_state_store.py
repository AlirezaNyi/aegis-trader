"""PaperStateStore: restart persistence of paper ledger + period tracker (fail closed)."""

from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.db.models import (
    Base,
    FillRow,
    OrderRow,
    PaperBalanceRow,
    PositionRow,
    RiskPeriodStateRow,
)
from aegis.paper import (
    PaperBroker,
    PaperEquityTracker,
    PaperLedger,
    PaperStateStore,
    StoredPaperState,
    apply_exits,
    instrument_key_for,
)
from aegis.paper.state_store import (
    REASON_LOAD_FAILED,
    REASON_PERSIST_FAILED,
    REASON_STATE_INVALID,
    REASON_STATE_MISSING,
    build_state_payload,
)
from aegis.schemas.common import LedgerKind, MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef
from aegis.schemas.orders import OrderIntent
from aegis.schemas.risk import RiskDecision, RiskDecisionType

T0 = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
INST = InstrumentRef(market_type=MarketType.SPOT, symbol="BTCUSDT")
KEY = instrument_key_for(INST)
LIMIT = Decimal("2")  # arbitrary test limit (not a policy value)


def _settings() -> Settings:
    clear_settings_cache()
    return Settings(
        trading_mode=TradingMode.PAPER,
        live_armed=False,
        kill_switch=False,
        require_database=False,
        paper_fee_bps=Decimal("10"),
        paper_slippage_bps=Decimal("0"),
    )


class FakeBackend:
    """In-memory backend that round-trips through JSON-like snapshots."""

    def __init__(self) -> None:
        self.ledger_snapshot: dict[str, Any] | None = None
        self.state: dict[str, Any] | None = None
        self.fail_read = False
        self.fail_write = False
        self.writes = 0

    def read(self) -> StoredPaperState:
        if self.fail_read:
            raise ConnectionError("db down")
        if self.ledger_snapshot is None:
            return StoredPaperState(ledger=PaperLedger(), has_ledger_rows=False, state=None)
        snap = copy.deepcopy(self.ledger_snapshot)
        snap.pop("protections", None)  # ledger rows do not carry protective levels
        return StoredPaperState(
            ledger=PaperLedger.from_snapshot(snap),
            has_ledger_rows=True,
            state=copy.deepcopy(self.state),
        )

    def write(self, ledger: PaperLedger, state: dict[str, Any]) -> None:
        if self.fail_write:
            raise ConnectionError("db down")
        self.ledger_snapshot = copy.deepcopy(ledger.to_snapshot())
        self.state = copy.deepcopy(state)
        self.writes += 1


def _open_long(broker: PaperBroker, now: datetime) -> None:
    pid = uuid4()
    decision = RiskDecision(
        decision=RiskDecisionType.APPROVE,
        policy_version="test",
        rules_evaluated=["TEST"],
        validated_order_params={
            "action": "BUY",
            "instrument": INST.model_dump(mode="json"),
            "entry_conditions": {"entry_price": "100", "order_type": "LIMIT"},
            "sizing": {"entry_price": "100", "notional": "200"},
            "stop_loss": "95",
            "take_profit": "110",
        },
        decided_at=now,
        expires_at=now + timedelta(hours=1),
        correlation_id="corr-store",
        proposal_id=pid,
    )
    intent = OrderIntent(
        intent_id=uuid4(),
        proposal_id=pid,
        risk_decision_correlation_id="corr-store",
        client_order_id=f"paper-{uuid4().hex[:16]}",
        ledger_kind=LedgerKind.PAPER,
        instrument=INST,
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("2"),
        price=Decimal("100"),
        created_at=now,
    )
    broker.submit_from_risk_decision(decision, intent, now=now)


def _bar(open_time: datetime, low: str) -> Candle:
    return Candle(
        instrument=INST,
        interval=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=Decimal("99"),
        high=Decimal("99"),
        low=Decimal(low),
        close=Decimal("96"),
        volume=Decimal("1"),
        is_final=True,
        source="fixture",
        received_at=open_time + timedelta(minutes=1),
    )


def _fresh_runtime(store: PaperStateStore) -> tuple[PaperBroker, PaperEquityTracker]:
    """What main.py does at startup: construct, then restore in place."""
    ledger = PaperLedger()
    broker = PaperBroker(_settings(), ledger=ledger, initial_quote_balance=Decimal("1000"))
    tracker = PaperEquityTracker()
    store.restore(broker.ledger, tracker)
    return broker, tracker


def test_disabled_store_is_in_memory_noop() -> None:
    store = PaperStateStore(None)
    assert store.enabled is False
    broker, tracker = _fresh_runtime(store)
    assert tracker.state_available is True
    assert store.save(broker.ledger, tracker) is False
    snap = tracker.observe(Decimal("1000"), now=T0)
    assert snap.daily_loss == Decimal("0")


def test_fresh_store_saves_initial_state() -> None:
    backend = FakeBackend()
    store = PaperStateStore(None, backend=backend)
    broker, tracker = _fresh_runtime(store)
    assert store.restored is True
    assert backend.writes == 1
    assert backend.state is not None
    assert backend.state["ledger_checksum"] == broker.ledger.checksum()


def test_round_trip_restores_ledger_tracker_protections_and_lock() -> None:
    backend = FakeBackend()
    store = PaperStateStore(None, backend=backend)
    broker, tracker = _fresh_runtime(store)
    tracker.observe(Decimal("1000"), now=T0)
    _open_long(broker, T0)
    apply_exits(broker, INST, [_bar(T0, "96")], now=T0 + timedelta(minutes=2))
    tracker.observe(Decimal("997"), now=T0 + timedelta(minutes=2), weekly_loss_limit=LIMIT)
    assert store.save(broker.ledger, tracker) is True
    lock = tracker.snapshot(now=T0 + timedelta(minutes=2)).period_lock_until
    assert lock is not None

    # Restart: new objects, same backend.
    store2 = PaperStateStore(None, backend=backend)
    broker2, tracker2 = _fresh_runtime(store2)
    assert broker2.ledger.checksum() == broker.ledger.checksum()
    assert broker2.ledger.protections == broker.ledger.protections
    assert broker2.ledger.protections[KEY].last_checked_open_time == T0
    assert tracker2.to_state() == tracker.to_state()
    # Equity recovered after restart: the weekly lock still holds.
    snap = tracker2.observe(Decimal("1010"), now=T0 + timedelta(hours=1), weekly_loss_limit=LIMIT)
    assert snap.period_lock_until == lock
    assert snap.weekly_loss == Decimal("0")


def test_restore_keeps_external_ledger_reference() -> None:
    backend = FakeBackend()
    store = PaperStateStore(None, backend=backend)
    broker, tracker = _fresh_runtime(store)
    _open_long(broker, T0)
    store.save(broker.ledger, tracker)
    ledger = PaperLedger()  # e.g. app.state.paper_ledger
    broker2 = PaperBroker(_settings(), ledger=ledger, initial_quote_balance=Decimal("1000"))
    PaperStateStore(None, backend=backend).restore(broker2.ledger, PaperEquityTracker())
    assert broker2.ledger is ledger
    assert ledger.positions[KEY].quantity == Decimal("2")


def test_load_failure_reports_none_losses_and_refuses_save() -> None:
    backend = FakeBackend()
    PaperStateStore(None, backend=backend).restore(PaperLedger(), PaperEquityTracker())
    backend.fail_read = True
    store = PaperStateStore(None, backend=backend)
    broker = PaperBroker(_settings(), ledger=PaperLedger(), initial_quote_balance=Decimal("1000"))
    tracker = PaperEquityTracker()
    result = store.restore(broker.ledger, tracker)
    assert result.ok is False
    assert tracker.unavailable_reason == REASON_LOAD_FAILED
    assert store.last_error == "load:ConnectionError"  # class name only
    snap = tracker.observe(Decimal("1000"), now=T0, daily_loss_limit=LIMIT)
    assert snap.daily_loss is None
    assert snap.weekly_loss is None
    assert snap.monthly_loss is None
    assert snap.drawdown is None
    assert snap.cooldown_active is True
    assert tracker.initial_equity is None  # never silently started fresh
    writes = backend.writes
    assert store.save(broker.ledger, tracker) is False
    assert backend.writes == writes  # stored state not overwritten
    assert store.last_error == "save:refused_not_restored"

    # Retry once the DB is back: state restored and available again.
    backend.fail_read = False
    assert store.restore(broker.ledger, tracker).ok is True
    assert tracker.state_available is True


def test_ledger_rows_without_state_fail_closed() -> None:
    backend = FakeBackend()
    backend.ledger_snapshot = PaperLedger().to_snapshot()
    backend.state = None
    tracker = PaperEquityTracker()
    result = PaperStateStore(None, backend=backend).restore(PaperLedger(), tracker)
    assert result.ok is False
    assert tracker.unavailable_reason == REASON_STATE_MISSING
    assert tracker.observe(Decimal("1"), now=T0).daily_loss is None


def test_checksum_mismatch_fails_closed() -> None:
    backend = FakeBackend()
    store = PaperStateStore(None, backend=backend)
    broker, tracker = _fresh_runtime(store)
    _open_long(broker, T0)
    store.save(broker.ledger, tracker)
    assert backend.ledger_snapshot is not None
    backend.ledger_snapshot["balances"]["USDT"] = "999999"  # rows drifted from state
    tracker2 = PaperEquityTracker()
    ledger2 = PaperLedger()
    result = PaperStateStore(None, backend=backend).restore(ledger2, tracker2)
    assert result.ok is False
    assert tracker2.unavailable_reason == REASON_STATE_INVALID
    assert ledger2.positions == {}  # nothing adopted from inconsistent state


def test_malformed_tracker_state_fails_closed() -> None:
    backend = FakeBackend()
    store = PaperStateStore(None, backend=backend)
    broker, tracker = _fresh_runtime(store)
    tracker.observe(Decimal("1000"), now=T0)
    store.save(broker.ledger, tracker)
    assert backend.state is not None
    backend.state["tracker"]["peak_equity"] = "NaN"
    tracker2 = PaperEquityTracker()
    assert PaperStateStore(None, backend=backend).restore(PaperLedger(), tracker2).ok is False
    assert tracker2.state_lost is True


def test_persist_failure_blocks_until_next_good_save() -> None:
    backend = FakeBackend()
    store = PaperStateStore(None, backend=backend)
    broker, tracker = _fresh_runtime(store)
    tracker.observe(Decimal("1000"), now=T0)
    backend.fail_write = True
    assert store.save(broker.ledger, tracker) is False
    assert tracker.unavailable_reason == REASON_PERSIST_FAILED
    assert tracker.observe(Decimal("999"), now=T0 + timedelta(minutes=1)).daily_loss is None
    backend.fail_write = False
    assert store.save(broker.ledger, tracker) is True
    assert tracker.state_available is True
    snap = tracker.snapshot(now=T0 + timedelta(minutes=1))
    assert snap.daily_loss == Decimal("1")  # tracking continued while unavailable


def test_unchanged_state_skips_write() -> None:
    backend = FakeBackend()
    store = PaperStateStore(None, backend=backend)
    broker, tracker = _fresh_runtime(store)
    writes = backend.writes
    assert store.save(broker.ledger, tracker) is True
    assert backend.writes == writes
    assert store.save(broker.ledger, tracker, force=True) is True
    assert backend.writes == writes + 1


def test_payload_has_no_secret_like_fields() -> None:
    payload = build_state_payload(PaperLedger(), PaperEquityTracker())
    blob = str(payload).lower()
    for word in ("secret", "api_key", "password", "signature"):
        assert word not in blob


def test_store_does_not_retain_url() -> None:
    store = PaperStateStore("postgresql+psycopg://u:secretpw@127.0.0.1:1/x")
    assert "secretpw" not in repr(vars(store))


def test_unreachable_database_fails_closed() -> None:
    store = PaperStateStore("postgresql+psycopg://u:p@127.0.0.1:1/x")
    tracker = PaperEquityTracker()
    result = store.restore(PaperLedger(), tracker)
    assert result.ok is False
    assert tracker.state_available is False
    assert tracker.observe(Decimal("1"), now=T0).daily_loss is None
    assert store.last_error is not None and store.last_error.startswith("load:")
    assert "secretpw" not in store.last_error


# --- Optional Postgres integration (isolated temp schema, always rolled back) ----

_PG_URL = os.environ.get("AEGIS_TEST_DATABASE_URL")


@pytest.fixture()
def pg_session_factory() -> Iterator[sessionmaker[Session]]:
    if not _PG_URL:
        pytest.skip("AEGIS_TEST_DATABASE_URL not set")
    engine = create_engine(_PG_URL)
    conn = engine.connect()
    outer = conn.begin()
    try:
        schema = f"aegis_test_{uuid4().hex[:10]}"
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        Base.metadata.create_all(
            conn,
            tables=[
                OrderRow.__table__,
                FillRow.__table__,
                PositionRow.__table__,
                PaperBalanceRow.__table__,
                RiskPeriodStateRow.__table__,
            ],
        )
        yield sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
    finally:
        outer.rollback()  # drops the temp schema and every row written in it
        conn.close()
        engine.dispose()


def test_postgres_round_trip(pg_session_factory: sessionmaker[Session]) -> None:
    store = PaperStateStore(None, session_factory=pg_session_factory)
    broker, tracker = _fresh_runtime(store)
    tracker.observe(Decimal("1000"), now=T0)
    _open_long(broker, T0)
    tracker.observe(Decimal("995"), now=T0 + timedelta(minutes=1), daily_loss_limit=LIMIT)
    assert store.save(broker.ledger, tracker) is True
    apply_exits(broker, INST, [_bar(T0, "90")], now=T0 + timedelta(minutes=2))
    assert store.save(broker.ledger, tracker) is True

    store2 = PaperStateStore(None, session_factory=pg_session_factory)
    broker2, tracker2 = _fresh_runtime(store2)
    assert broker2.ledger.checksum() == broker.ledger.checksum()
    assert broker2.ledger.positions[KEY].quantity == Decimal("0")
    assert broker2.ledger.protections == {}
    assert len(broker2.ledger.fills) == 2
    assert tracker2.to_state() == tracker.to_state()
    assert tracker2.snapshot(now=T0 + timedelta(minutes=3)).period_lock_until is not None


def test_unparseable_legacy_order_side_fails_closed() -> None:
    """A legacy paper order row with an unparseable side makes restore refuse
    (state_lost) instead of crashing or silently starting fresh."""
    from aegis.paper.persist import _order_from_row

    bad = OrderRow(
        id=uuid4(),
        intent_id=uuid4(),
        client_order_id="paper-legacy",
        exchange_order_id=None,
        ledger_kind=LedgerKind.PAPER.value,
        exchange="toobit",
        market_type=MarketType.SPOT.value,
        symbol="BTCUSDT",
        status="filled",
        side="BUY_OPEN",
        order_type="LIMIT",
        quantity="1",
        filled_quantity="1",
        price="100",
        created_at=T0,
        updated_at=T0,
    )

    class LegacyRowBackend(FakeBackend):
        def read(self) -> StoredPaperState:
            _order_from_row(bad)  # what SqlPaperStateBackend.read does per row
            return super().read()

    tracker = PaperEquityTracker()
    ledger = PaperLedger()
    store = PaperStateStore(None, backend=LegacyRowBackend())
    result = store.restore(ledger, tracker)
    assert result.ok is False
    assert tracker.unavailable_reason == REASON_LOAD_FAILED
    assert tracker.state_lost is True
    assert store.last_error == "load:ValueError"
    assert ledger.orders == {}
