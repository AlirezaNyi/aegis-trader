"""Async execution path: concurrency safety, OrderSide boundary, reconcile sweeps.

Mocks / httpx.MockTransport only; no real exchange requests.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from aegis.config.settings import Settings, TradingMode, clear_settings_cache
from aegis.db.models import OrderRow
from aegis.exchange._http import order_from_exchange_payload
from aegis.exchange.errors import AmbiguousSubmitError, ExchangeError
from aegis.exchange.futures_client import ToobitFuturesExecutionClient
from aegis.exchange.router import ToobitExecutionRouter
from aegis.exchange.side_map import (
    OrderSideMappingError,
    futures_side_and_position,
    spot_side,
)
from aegis.exchange.spot_client import ToobitSpotExecutionClient
from aegis.guards.live import LiveExecutionNotAllowed
from aegis.ops.metrics import OpsMetrics
from aegis.ops.shutdown import ShutdownGate
from aegis.orders.manager import OrderManager, OrderSubmitBlocked
from aegis.orders.persist import LiveOrderLoadError, load_live_orders
from aegis.reconcile.restart import recover_open_intents
from aegis.reconcile.service import Reconciler, ReconcileSweeper
from aegis.schemas.common import LedgerKind, MarketType
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Order, OrderIntent, OrderSide, OrderStatus
from aegis.schemas.risk import RiskDecision, RiskDecisionType

API_KEY = "test-api-key-not-real"
SECRET = "test-secret-not-real"
NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _settings() -> Settings:
    clear_settings_cache()
    return Settings(
        trading_mode=TradingMode.LIVE,
        live_armed=True,
        kill_switch=False,
        require_database=False,
        toobit_api_key=API_KEY,
        toobit_api_secret=SECRET,
    )


def _intent(
    *,
    client_order_id: str | None = None,
    side: Any = "BUY",
    market_type: MarketType = MarketType.SPOT,
    symbol: str = "ETHUSDT",
    proposal_id: Any = None,
    extras: dict[str, Any] | None = None,
) -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        proposal_id=proposal_id or uuid4(),
        risk_decision_correlation_id="corr-async",
        client_order_id=client_order_id or f"c-{uuid4().hex[:8]}",
        ledger_kind=LedgerKind.LIVE,
        instrument=InstrumentRef(market_type=market_type, symbol=symbol),
        side=side,
        order_type="LIMIT",
        quantity=Decimal("1"),
        price=Decimal("100"),
        extras=extras or {},
        created_at=NOW,
    )


def _decision(proposal_id: Any) -> RiskDecision:
    return RiskDecision(
        decision=RiskDecisionType.APPROVE,
        policy_version="test",
        rules_evaluated=["TEST"],
        validated_order_params={
            "action": "BUY",
            "instrument": {
                "exchange": "toobit",
                "market_type": "spot",
                "symbol": "ETHUSDT",
            },
            "entry_conditions": {"entry_price": "100", "order_type": "LIMIT"},
            "sizing": {"entry_price": "100", "notional": "100"},
        },
        decided_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        correlation_id="corr-async",
        proposal_id=proposal_id,
    )


def _paired(client_order_id: str | None = None) -> tuple[RiskDecision, OrderIntent]:
    pid = uuid4()
    return _decision(pid), _intent(proposal_id=pid, client_order_id=client_order_id)


def _order(intent: OrderIntent, status: OrderStatus) -> Order:
    return Order(
        order_id=uuid4(),
        intent_id=intent.intent_id,
        client_order_id=intent.client_order_id,
        ledger_kind=LedgerKind.LIVE,
        instrument=intent.instrument,
        status=status,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        price=intent.price,
        created_at=NOW,
        updated_at=NOW,
    )


class GatedPort:
    """Submit blocks until ``release`` is set, so tests can interleave calls."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.submit_calls = 0
        self.submit_exc: BaseException | None = None
        self.get_calls = 0
        self.get_result: Order | None = None

    async def submit(self, intent: OrderIntent) -> Order:
        self.submit_calls += 1
        self.entered.set()
        await self.release.wait()
        if self.submit_exc is not None:
            raise self.submit_exc
        return _order(intent, OrderStatus.OPEN)

    async def cancel(self, client_order_id: str) -> Order:
        raise NotImplementedError

    async def get_order(self, client_order_id: str) -> Order | None:
        self.get_calls += 1
        return self.get_result


# ------------------------------------------------- concurrent submit safety


async def test_concurrent_same_client_order_id_submits_once() -> None:
    port = GatedPort()
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    decision, intent = _paired("c-dup")
    first = asyncio.create_task(mgr.submit(decision, intent, now=NOW))
    second = asyncio.create_task(mgr.submit(decision, intent.model_copy(), now=NOW))
    await port.entered.wait()
    await asyncio.sleep(0)
    assert port.submit_calls == 1
    port.release.set()
    a, b = await asyncio.gather(first, second)
    assert port.submit_calls == 1
    assert a.client_order_id == b.client_order_id == "c-dup"
    assert b.status == OrderStatus.OPEN


async def test_concurrent_submit_waits_and_is_blocked_by_prior_ambiguity() -> None:
    port = GatedPort()
    port.submit_exc = AmbiguousSubmitError("timeout", client_order_id="c-a")
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    d1, i1 = _paired("c-a")
    d2, i2 = _paired("c-b")
    first = asyncio.create_task(mgr.submit(d1, i1, now=NOW))
    await port.entered.wait()
    second = asyncio.create_task(mgr.submit(d2, i2, now=NOW))
    await asyncio.sleep(0)
    port.release.set()
    result = await first
    assert result.status in {OrderStatus.UNKNOWN, OrderStatus.MISSING}
    with pytest.raises(OrderSubmitBlocked):
        await second
    assert port.submit_calls == 1  # the waiting submit never reached the adapter


async def test_waiting_submit_rechecks_kill_switch_under_lock() -> None:
    settings = _settings()
    port = GatedPort()
    mgr = OrderManager(settings, port)
    d1, i1 = _paired()
    d2, i2 = _paired()
    first = asyncio.create_task(mgr.submit(d1, i1, now=NOW))
    await port.entered.wait()
    second = asyncio.create_task(mgr.submit(d2, i2, now=NOW))
    await asyncio.sleep(0)
    object.__setattr__(settings, "kill_switch", True)
    port.release.set()
    await first
    with pytest.raises((OrderSubmitBlocked, LiveExecutionNotAllowed)):
        await second
    assert port.submit_calls == 1


async def test_cancelled_submit_marks_unknown_and_blocks() -> None:
    port = GatedPort()
    mgr = OrderManager(_settings(), port)
    decision, intent = _paired("c-cancelled")
    task = asyncio.create_task(mgr.submit(decision, intent, now=NOW))
    await port.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr.orders["c-cancelled"].status == OrderStatus.UNKNOWN
    assert mgr.block_new_submits is True
    assert not mgr.lock.locked()


async def test_placeholder_written_before_network_call() -> None:
    port = GatedPort()
    mgr = OrderManager(_settings(), port)
    decision, intent = _paired("c-ph")
    task = asyncio.create_task(mgr.submit(decision, intent, now=NOW))
    await port.entered.wait()
    assert mgr.orders["c-ph"].status == OrderStatus.SUBMIT_ATTEMPTED
    port.release.set()
    await task


# ------------------------------------------------- OrderSide boundary


@pytest.mark.parametrize("raw", ["BUY", "buy", " Buy ", OrderSide.BUY])
def test_order_side_normalizes(raw: Any) -> None:
    intent = _intent(side=raw)
    assert intent.side is OrderSide.BUY
    assert intent.model_dump(mode="json")["side"] == "BUY"


@pytest.mark.parametrize("raw", ["LONG", "SHORT", "BUY_OPEN", "", "HOLD", None, 1])
def test_order_side_rejects_unknown(raw: Any) -> None:
    with pytest.raises(ValidationError):
        _intent(side=raw)


def test_order_side_round_trips_json() -> None:
    intent = _intent(side="sell")
    restored = OrderIntent.model_validate_json(intent.model_dump_json())
    assert restored.side is OrderSide.SELL
    assert '"side":"SELL"' in intent.model_dump_json()


def test_side_maps() -> None:
    assert spot_side(OrderSide.SELL) == "SELL"
    with pytest.raises(ValueError):
        spot_side("LONG")
    fut = _intent(market_type=MarketType.FUTURES, symbol="BTC-SWAP-USDT", side="SELL")
    assert futures_side_and_position(fut) == ("SELL", "SHORT")
    close_short = _intent(
        market_type=MarketType.FUTURES,
        symbol="BTC-SWAP-USDT",
        side="BUY",
        extras={"position_side": "SHORT"},
    )
    assert futures_side_and_position(close_short) == ("BUY", "SHORT")


def test_exchange_side_unknown_spelling_keeps_intent_side() -> None:
    intent = _intent(side="SELL")
    order = order_from_exchange_payload({"status": "NEW", "side": "SELL_CLOSE"}, intent)
    assert order.side is OrderSide.SELL


# ------------------------------------------------- async client lifecycle


async def test_injected_async_client_not_closed_by_aclose() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})),
        base_url="https://api.toobit.com",
    )
    async with ToobitSpotExecutionClient(api_key=API_KEY, api_secret=SECRET, client=http):
        pass
    assert not http.is_closed
    await http.aclose()


async def test_owned_clients_closed_by_router_aclose() -> None:
    spot = ToobitSpotExecutionClient(api_key=API_KEY, api_secret=SECRET)
    fut = ToobitFuturesExecutionClient(api_key=API_KEY, api_secret=SECRET)
    async with ToobitExecutionRouter(spot=spot, futures=fut):
        pass
    assert spot._client.is_closed and fut._client.is_closed


# ------------------------------------------------- reconcile batch / sweeper


class ScriptedQueryPort:
    def __init__(self, answers: dict[str, Any], *, delay: float = 0.0) -> None:
        self.answers = answers
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.get_calls: list[str] = []
        self.submit_calls = 0
        self.allow_submit = False

    async def submit(self, intent: OrderIntent) -> Order:
        self.submit_calls += 1
        if not self.allow_submit:
            raise AssertionError("reconcile must never submit")
        return _order(intent, OrderStatus.OPEN)

    async def cancel(self, client_order_id: str) -> Order:
        raise AssertionError("reconcile must never cancel")

    async def get_order(self, client_order_id: str) -> Order | None:
        self.get_calls.append(client_order_id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            answer = self.answers.get(client_order_id)
            if isinstance(answer, BaseException):
                raise answer
            return answer  # type: ignore[no-any-return]
        finally:
            self.in_flight -= 1


def _seed(mgr: OrderManager, statuses: dict[str, OrderStatus]) -> dict[str, OrderIntent]:
    intents = {coid: _intent(client_order_id=coid) for coid in statuses}
    mgr.load_intents(intents)
    mgr.load_orders({coid: _order(intents[coid], st) for coid, st in statuses.items()})
    return intents


async def test_reconcile_open_orders_bounded_and_isolated() -> None:
    statuses = {f"c-{i}": OrderStatus.OPEN for i in range(8)}
    statuses["c-unknown"] = OrderStatus.UNKNOWN
    statuses["c-done"] = OrderStatus.FILLED
    statuses["c-err"] = OrderStatus.OPEN
    port = ScriptedQueryPort({}, delay=0.01)
    mgr = OrderManager(_settings(), port)
    intents = _seed(mgr, statuses)
    for i in range(8):
        port.answers[f"c-{i}"] = _order(intents[f"c-{i}"], OrderStatus.PARTIALLY_FILLED)
    port.answers["c-unknown"] = _order(intents["c-unknown"], OrderStatus.FILLED)
    port.answers["c-err"] = ExchangeError("boom")

    reconciler = Reconciler(port, concurrency=3)
    batch = await reconciler.reconcile_open_orders(mgr, now=NOW)

    assert "c-done" not in port.get_calls  # terminal orders are not queried
    assert port.max_in_flight <= 3
    assert batch.errors == {"c-err": "ExchangeError"}
    assert mgr.orders["c-err"].status == OrderStatus.OPEN  # failure keeps prior state
    assert mgr.orders["c-unknown"].status == OrderStatus.FILLED
    assert all(mgr.orders[f"c-{i}"].status == OrderStatus.PARTIALLY_FILLED for i in range(8))
    # A failed query on an OPEN order leaves exchange state unverified: sticky block.
    assert batch.block_new_submits is True
    assert mgr.reconcile_pending_ids == frozenset({"c-err"})
    assert port.submit_calls == 0


async def test_reconcile_failure_keeps_unknown_blocking() -> None:
    port = ScriptedQueryPort({"c-u": httpx.ReadTimeout("t")})
    mgr = OrderManager(_settings(), port)
    _seed(mgr, {"c-u": OrderStatus.UNKNOWN})
    batch = await Reconciler(port).reconcile_open_orders(mgr, now=NOW)
    assert batch.errors == {"c-u": "ReadTimeout"}
    assert mgr.orders["c-u"].status == OrderStatus.UNKNOWN
    assert mgr.block_new_submits is True


async def test_reconcile_missing_is_reported_not_cleared() -> None:
    port = ScriptedQueryPort({"c-m": None})
    mgr = OrderManager(_settings(), port)
    _seed(mgr, {"c-m": OrderStatus.UNKNOWN})
    batch = await Reconciler(port).reconcile_open_orders(mgr, now=NOW)
    assert batch.missing == ["c-m"]
    assert mgr.orders["c-m"].status == OrderStatus.MISSING
    assert batch.block_new_submits is True
    assert mgr.block_new_submits is True
    # A second sweep does not re-query MISSING (terminal) and does not unblock.
    batch2 = await Reconciler(port).reconcile_open_orders(mgr, now=NOW)
    assert batch2.queried == 0
    assert mgr.block_new_submits is True


async def test_restart_recovery_query_error_keeps_block() -> None:
    port = ScriptedQueryPort({"c-r": ExchangeError("down")})
    mgr = OrderManager(_settings(), port)
    _seed(mgr, {"c-r": OrderStatus.UNKNOWN})
    result = await recover_open_intents(mgr, Reconciler(port), now=NOW)
    assert result.ok is False
    assert result.errors == {"c-r": "ExchangeError"}
    assert mgr.block_new_submits is True


async def test_sweeper_records_metrics_and_stops_on_shutdown() -> None:
    port = ScriptedQueryPort({})
    mgr = OrderManager(_settings(), port)
    intents = _seed(mgr, {"c-1": OrderStatus.OPEN, "c-2": OrderStatus.OPEN})
    port.answers["c-1"] = _order(intents["c-1"], OrderStatus.FILLED)
    port.answers["c-2"] = ExchangeError("x")
    gate = ShutdownGate()
    metrics = OpsMetrics()
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) >= 2:
            gate.begin_shutdown()

    sweeper = ReconcileSweeper(
        Reconciler(port),
        mgr,
        shutdown=gate,
        interval_seconds=10.0,
        jitter_seconds=2.0,
        metrics=metrics,
        rng=random.Random(7),
        sleep=fake_sleep,
    )
    await asyncio.wait_for(sweeper.run(), timeout=2)
    assert sweeper.sweeps == 2
    assert metrics.reconcile_runs == 2
    assert metrics.reconcile_failures == 2  # c-2 failed in each sweep
    assert all(10.0 <= d <= 12.0 for d in delays)
    assert port.submit_calls == 0
    assert await sweeper.run_once() is None  # shutting down: no further queries


async def test_sweeper_start_and_aclose_stop_promptly() -> None:
    port = ScriptedQueryPort({})
    mgr = OrderManager(_settings(), port)
    sweeper = ReconcileSweeper(
        Reconciler(port), mgr, shutdown=ShutdownGate(), interval_seconds=3600.0
    )
    task = sweeper.start()
    await asyncio.sleep(0.01)
    await asyncio.wait_for(sweeper.aclose(), timeout=1)
    assert task.done()
    assert sweeper.sweeps >= 1


async def test_sweeper_failed_sweep_is_counted_not_raised() -> None:
    class Broken(Reconciler):
        async def reconcile_open_orders(self, manager: OrderManager, **_: Any) -> Any:
            raise RuntimeError("boom")

    port = ScriptedQueryPort({})
    mgr = OrderManager(_settings(), port)
    metrics = OpsMetrics()
    sweeper = ReconcileSweeper(Broken(port), mgr, shutdown=ShutdownGate(), metrics=metrics)
    assert await sweeper.run_once() is None
    assert sweeper.failed_sweeps == 1
    assert metrics.reconcile_failures == 1


async def test_sweep_holds_lock_so_submit_waits() -> None:
    port = ScriptedQueryPort({}, delay=0.05)
    mgr = OrderManager(_settings(), port, reconciler=Reconciler(port))
    intents = _seed(mgr, {"c-u": OrderStatus.UNKNOWN})
    port.answers["c-u"] = _order(intents["c-u"], OrderStatus.FILLED)
    assert mgr.block_new_submits is True
    port.allow_submit = True
    sweep = asyncio.create_task(Reconciler(port).reconcile_open_orders(mgr, now=NOW))
    await asyncio.sleep(0.01)
    assert mgr.lock.locked()
    d, i = _paired()
    # Submit queues behind the sweep; its block check runs only after the sweep
    # resolved UNKNOWN → FILLED under the lock, so it proceeds exactly once.
    order = await mgr.submit(d, i, now=NOW)
    assert sweep.done()
    assert mgr.orders["c-u"].status == OrderStatus.FILLED
    assert order.status == OrderStatus.OPEN
    assert port.submit_calls == 1


# ------------------------------------------------- M1: extras never change side


def _fut(side: str, extras: dict[str, Any]) -> OrderIntent:
    return _intent(
        market_type=MarketType.FUTURES, symbol="BTC-SWAP-USDT", side=side, extras=extras
    )


@pytest.mark.parametrize(
    "extras",
    [
        {"side": "SELL"},
        {"side": "SELL", "position_side": "SHORT"},
        {"side": "sell", "positionSide": "LONG"},
        {"side": 1},
        {"side": None},
    ],
)
def test_futures_extras_side_conflict_rejected(extras: dict[str, Any]) -> None:
    with pytest.raises(OrderSideMappingError):
        futures_side_and_position(_fut("BUY", extras))


def test_futures_extras_side_equal_is_ignored() -> None:
    assert futures_side_and_position(_fut("BUY", {"side": " buy "})) == ("BUY", "LONG")
    assert futures_side_and_position(
        _fut("SELL", {"side": "SELL", "positionSide": "LONG"})
    ) == ("SELL", "LONG")


@pytest.mark.parametrize(
    "extras",
    [
        {"position_side": "BOTH"},
        {"positionSide": "NET"},
        {"position_side": 5},
        {"position_side": "LONG", "positionSide": "SHORT"},
    ],
)
def test_futures_unverified_or_conflicting_position_side_rejected(
    extras: dict[str, Any],
) -> None:
    with pytest.raises(OrderSideMappingError):
        futures_side_and_position(_fut("BUY", extras))


async def test_futures_side_conflict_never_reaches_network() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.toobit.com"
    )
    client = ToobitFuturesExecutionClient(api_key=API_KEY, api_secret=SECRET, client=http)
    with pytest.raises(OrderSideMappingError):
        await client.submit(_fut("BUY", {"side": "SELL"}))
    assert calls == []
    await http.aclose()


async def test_order_manager_marks_side_conflict_rejected_without_block() -> None:
    class SideMapPort:
        def __init__(self) -> None:
            self.submits = 0

        async def submit(self, intent: OrderIntent) -> Order:
            self.submits += 1
            futures_side_and_position(intent)  # raises before any network
            raise AssertionError("unreachable")

        async def cancel(self, client_order_id: str) -> Order:
            raise AssertionError

        async def get_order(self, client_order_id: str) -> Order | None:
            raise AssertionError("definitive local reject needs no reconcile")

    port = SideMapPort()
    mgr = OrderManager(_settings(), port)
    pid = uuid4()
    intent = _intent(
        proposal_id=pid,
        market_type=MarketType.FUTURES,
        symbol="BTC-SWAP-USDT",
        side="BUY",
        extras={"side": "SELL"},
    )
    futures_decision = _decision(pid).model_copy(
        update={
            "validated_order_params": {
                "action": "BUY",
                "instrument": {
                    "exchange": "toobit",
                    "market_type": "futures",
                    "symbol": "BTC-SWAP-USDT",
                },
                "entry_conditions": {"entry_price": "100", "order_type": "LIMIT"},
                "sizing": {"entry_price": "100", "notional": "100"},
            }
        }
    )
    order = await mgr.submit(futures_decision, intent, now=NOW)
    assert order.status == OrderStatus.REJECTED
    assert order.side is OrderSide.BUY
    assert mgr.block_new_submits is False
    assert port.submits == 1


# ------------------------------------------------- M2: sticky reconcile-incomplete


async def test_restart_query_failure_on_open_order_blocks() -> None:
    port = ScriptedQueryPort({"c-o": ExchangeError("down")})
    mgr = OrderManager(_settings(), port)
    _seed(mgr, {"c-o": OrderStatus.OPEN})
    assert mgr.block_new_submits is False
    result = await recover_open_intents(mgr, Reconciler(port), now=NOW)
    assert result.ok is False
    assert result.blocked is True
    assert mgr.reconcile_incomplete is True
    assert mgr.block_new_submits is True
    port.allow_submit = True
    d, i = _paired()
    with pytest.raises(OrderSubmitBlocked):
        await mgr.submit(d, i, now=NOW)
    assert port.submit_calls == 0


async def test_partially_filled_query_failure_blocks_and_resweep_clears() -> None:
    port = ScriptedQueryPort({"c-p": ExchangeError("down")})
    mgr = OrderManager(_settings(), port)
    intents = _seed(mgr, {"c-p": OrderStatus.PARTIALLY_FILLED})
    reconciler = Reconciler(port)
    batch = await reconciler.reconcile_open_orders(mgr, now=NOW)
    assert batch.errors and batch.block_new_submits is True
    assert mgr.block_new_submits is True

    port.answers["c-p"] = _order(intents["c-p"], OrderStatus.PARTIALLY_FILLED)
    batch2 = await reconciler.reconcile_open_orders(mgr, now=NOW)
    assert batch2.errors == {}
    assert mgr.reconcile_incomplete is False
    assert mgr.block_new_submits is False


async def test_resweep_with_other_error_does_not_clear() -> None:
    port = ScriptedQueryPort({"c-a": ExchangeError("down"), "c-b": ExchangeError("x")})
    mgr = OrderManager(_settings(), port)
    intents = _seed(mgr, {"c-a": OrderStatus.OPEN, "c-b": OrderStatus.OPEN})
    reconciler = Reconciler(port)
    await reconciler.reconcile_open_orders(mgr, now=NOW)
    port.answers["c-a"] = _order(intents["c-a"], OrderStatus.FILLED)
    await reconciler.reconcile_open_orders(mgr, now=NOW)
    assert mgr.block_new_submits is True  # c-b still failing
    port.answers["c-b"] = _order(intents["c-b"], OrderStatus.CANCELED)
    await reconciler.reconcile_open_orders(mgr, now=NOW)
    assert mgr.block_new_submits is False


async def test_pending_id_turned_terminal_is_still_requeried() -> None:
    """A pending id that later becomes terminal locally is still re-queried by the
    sweep (pending ids are always included) before the block clears."""
    port = ScriptedQueryPort({"c-t": ExchangeError("down")})
    mgr = OrderManager(_settings(), port)
    intents = _seed(mgr, {"c-t": OrderStatus.OPEN})
    reconciler = Reconciler(port)
    await reconciler.reconcile_open_orders(mgr, now=NOW)
    mgr.apply_exchange_order("c-t", _order(intents["c-t"], OrderStatus.FILLED), now=NOW)
    assert mgr.block_new_submits is True
    port.answers["c-t"] = _order(intents["c-t"], OrderStatus.FILLED)
    port.get_calls.clear()
    await reconciler.reconcile_open_orders(mgr, now=NOW)
    assert port.get_calls == ["c-t"]
    assert mgr.block_new_submits is False


async def test_cancel_and_apply_exchange_order_do_not_clear_sticky_block() -> None:
    class CancelPort(ScriptedQueryPort):
        async def cancel(self, client_order_id: str) -> Order:
            return _order(self.intents[client_order_id], OrderStatus.CANCELED)

    port = CancelPort({"c-x": ExchangeError("down")})
    mgr = OrderManager(_settings(), port)
    intents = _seed(mgr, {"c-x": OrderStatus.OPEN, "c-y": OrderStatus.OPEN})
    port.intents = intents  # type: ignore[attr-defined]
    port.answers["c-y"] = _order(intents["c-y"], OrderStatus.OPEN)
    await recover_open_intents(mgr, Reconciler(port), now=NOW)
    assert mgr.block_new_submits is True

    await mgr.cancel("c-x", now=NOW)
    assert mgr.block_new_submits is True
    mgr.apply_exchange_order("c-y", _order(intents["c-y"], OrderStatus.FILLED), now=NOW)
    assert mgr.block_new_submits is True
    mgr.allow_new_submits()
    assert mgr.block_new_submits is True
    # Direct single-id query (as after an ambiguous submit) does not clear either.
    port.answers["c-x"] = _order(intents["c-x"], OrderStatus.CANCELED)
    await Reconciler(port).query_and_update(mgr, "c-x", now=NOW)
    assert mgr.block_new_submits is True
    # Only a full successful recovery / sweep clears it.
    result = await recover_open_intents(mgr, Reconciler(port), now=NOW)
    assert result.ok is True
    assert mgr.block_new_submits is False


async def test_intent_only_failed_query_stays_pending_until_resolved() -> None:
    port = ScriptedQueryPort({"c-i": ExchangeError("down")})
    mgr = OrderManager(_settings(), port)
    intent = _intent(client_order_id="c-i")
    result = await recover_open_intents(mgr, Reconciler(port), intents={"c-i": intent}, now=NOW)
    assert result.ok is False
    assert "c-i" not in mgr.orders
    assert mgr.block_new_submits is True
    # Sweep re-queries the intent-only id even though no local order exists.
    port.answers["c-i"] = _order(intent, OrderStatus.FILLED)
    await Reconciler(port).reconcile_open_orders(mgr, now=NOW)
    assert mgr.orders["c-i"].status == OrderStatus.FILLED
    assert mgr.block_new_submits is False


# ------------------------------------------------- L1: unparseable persisted side


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self, _stmt: Any) -> _Rows:
        return _Rows(self._rows)


def _order_row(client_order_id: str, side: str) -> OrderRow:
    return OrderRow(
        id=uuid4(),
        intent_id=uuid4(),
        client_order_id=client_order_id,
        exchange_order_id=None,
        ledger_kind=LedgerKind.LIVE.value,
        exchange="toobit",
        market_type=MarketType.SPOT.value,
        symbol="ETHUSDT",
        status=OrderStatus.OPEN.value,
        side=side,
        order_type="LIMIT",
        quantity="1",
        filled_quantity="0",
        price="100",
        created_at=NOW,
        updated_at=NOW,
    )


def test_load_live_orders_unparseable_side_raises_typed_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = _FakeSession([_order_row("c-good", "buy"), _order_row("c-bad", "BUY_OPEN")])
    with caplog.at_level("ERROR"), pytest.raises(LiveOrderLoadError) as info:
        load_live_orders(session)  # type: ignore[arg-type]
    assert info.value.failed_client_order_ids == ["c-bad"]
    assert set(info.value.loaded) == {"c-good"}
    assert info.value.loaded["c-good"].side is OrderSide.BUY
    assert any("live_order_row_unparseable" in r.getMessage() for r in caplog.records)


def test_load_live_orders_clean_rows_ok() -> None:
    session = _FakeSession([_order_row("c-1", "SELL")])
    out = load_live_orders(session)  # type: ignore[arg-type]
    assert out["c-1"].side is OrderSide.SELL


async def test_recovery_with_load_error_blocks_submits() -> None:
    session = _FakeSession([_order_row("c-bad", "LONG")])
    try:
        load_live_orders(session)  # type: ignore[arg-type]
    except LiveOrderLoadError as exc:
        load_error = exc
    else:  # pragma: no cover
        raise AssertionError("expected LiveOrderLoadError")
    port = ScriptedQueryPort({})
    port.allow_submit = True
    mgr = OrderManager(_settings(), port)
    mgr.load_orders(load_error.loaded)
    result = await recover_open_intents(mgr, Reconciler(port), load_error=load_error, now=NOW)
    assert result.ok is False
    assert result.blocked is True
    assert mgr.state_load_failed is True
    # Successful sweeps never clear a state-load failure.
    await Reconciler(port).reconcile_open_orders(mgr, now=NOW)
    mgr.allow_new_submits()
    assert mgr.block_new_submits is True
    d, i = _paired()
    with pytest.raises(OrderSubmitBlocked):
        await mgr.submit(d, i, now=NOW)
    assert port.submit_calls == 0
