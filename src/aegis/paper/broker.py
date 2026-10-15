"""Paper broker — simulates fills after Risk APPROVE; never calls exchange APIs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from aegis.config.settings import Settings, TradingMode
from aegis.guards.live import trading_ready
from aegis.paper.exceptions import PaperFillError, PaperLedgerKindError, PaperTradingBlocked
from aegis.paper.fills import compute_fee, compute_fill_price
from aegis.paper.ledger import PaperLedger, apply_cash_and_position, instrument_key_for
from aegis.paper.protection import (
    ClosedTrade,
    ExitReason,
    ProtectiveLevels,
    parse_max_hold_bars,
    parse_protective_level,
)
from aegis.risk.handoff import (
    IntentBindingError,
    assert_intent_matches_decision,
    assert_may_submit_to_order_manager,
)
from aegis.schemas.common import LedgerKind
from aegis.schemas.market import InstrumentRef
from aegis.schemas.orders import Fill, Order, OrderIntent, OrderSide, OrderStatus
from aegis.schemas.risk import RiskDecision

_ZERO = Decimal("0")


class PaperBroker:
    """Simulated execution path for paper / development modes.

    Does not import or call Toobit private trade APIs. All state is ledger_kind=paper.
    Fee / slippage knobs are engineering simulation assumptions, not risk-policy values.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        ledger: PaperLedger | None = None,
        fee_bps: Decimal | None = None,
        slippage_bps: Decimal | None = None,
        initial_quote_balance: Decimal | None = None,
    ) -> None:
        self._settings = settings
        self.fee_bps = Decimal(fee_bps if fee_bps is not None else settings.paper_fee_bps)
        self.slippage_bps = Decimal(
            slippage_bps if slippage_bps is not None else settings.paper_slippage_bps
        )
        self.ledger = ledger or PaperLedger()
        if initial_quote_balance is not None:
            self.ledger.set_balance(Decimal(initial_quote_balance))

    def submit_from_risk_decision(
        self,
        decision: RiskDecision,
        intent: OrderIntent,
        *,
        mid_price: Decimal | None = None,
        now: datetime | None = None,
    ) -> Order:
        """Only APPROVE (non-expired) decisions bound to this intent may fill (INV-01)."""
        clock = now if now is not None else datetime.now(tz=UTC)
        assert_may_submit_to_order_manager(decision, now=clock)
        self._assert_paper_path_allowed(intent)
        self._assert_decision_binds_intent(decision, intent)
        stop_loss: Decimal | None = None
        take_profit: Decimal | None = None
        max_hold_bars: int | None = None
        if intent.side is OrderSide.BUY:
            # Protective levels come only from the APPROVED decision. Malformed
            # values fail closed (no fill) rather than open an unprotected position.
            params = decision.validated_order_params or {}
            stop_loss = parse_protective_level(params.get("stop_loss"), name="stop_loss")
            take_profit = parse_protective_level(params.get("take_profit"), name="take_profit")
            entry_conditions = params.get("entry_conditions") or {}
            if not isinstance(entry_conditions, Mapping):
                raise PaperFillError("validated entry_conditions must be an object")
            max_hold_bars = parse_max_hold_bars(entry_conditions.get("max_hold_bars"))
        return self._apply_approved_intent(
            intent,
            mid_price=mid_price,
            now=clock,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_hold_bars=max_hold_bars,
        )

    def _assert_decision_binds_intent(
        self, decision: RiskDecision, intent: OrderIntent
    ) -> None:
        try:
            assert_intent_matches_decision(decision, intent)
        except IntentBindingError as exc:
            raise PaperFillError(str(exc)) from exc

    def _apply_approved_intent(
        self,
        intent: OrderIntent,
        *,
        mid_price: Decimal | None = None,
        now: datetime | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        max_hold_bars: int | None = None,
    ) -> Order:
        """Internal fill path — only reachable after Risk APPROVE + identity bind."""
        clock = now if now is not None else datetime.now(tz=UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)

        self._assert_paper_path_allowed(intent)

        if intent.client_order_id in self.ledger.orders:
            return self.ledger.orders[intent.client_order_id]

        fill_price = compute_fill_price(
            intent,
            mid_price=mid_price,
            slippage_bps=self.slippage_bps,
        )
        fee = compute_fee(intent.quantity, fill_price, self.fee_bps)

        order_id = uuid4()
        order = Order(
            order_id=order_id,
            intent_id=intent.intent_id,
            client_order_id=intent.client_order_id,
            exchange_order_id=None,
            ledger_kind=LedgerKind.PAPER,
            instrument=intent.instrument,
            status=OrderStatus.FILLED,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            filled_quantity=intent.quantity,
            price=fill_price,
            created_at=clock,
            updated_at=clock,
        )
        fill = Fill(
            fill_id=uuid4(),
            order_id=order_id,
            ledger_kind=LedgerKind.PAPER,
            instrument=intent.instrument,
            quantity=intent.quantity,
            price=fill_price,
            fee=fee,
            fee_asset=self.ledger.quote_asset,
            exchanged_at=clock,
            received_at=clock,
        )

        # Spot-style cash check for BUY
        if intent.side is OrderSide.BUY:
            needed = intent.quantity * fill_price + fee
            if self.ledger.get_balance() < needed:
                raise PaperFillError(
                    f"insufficient paper {self.ledger.quote_asset} balance "
                    f"(need={needed}, have={self.ledger.get_balance()})"
                )
        elif intent.side is OrderSide.SELL:
            pos = self.ledger.position_for(
                intent.instrument.exchange,
                intent.instrument.market_type.value,
                intent.instrument.symbol,
            )
            held = pos.quantity if pos else Decimal("0")
            if held < intent.quantity:
                raise PaperFillError(
                    f"insufficient paper position for SELL "
                    f"(need={intent.quantity}, have={held})"
                )

        key = instrument_key_for(intent.instrument)
        prior = self.ledger.positions.get(key)
        prior_qty = prior.quantity if prior is not None else _ZERO
        prior_entry = prior.entry_price if prior is not None else None

        position = apply_cash_and_position(
            self.ledger,
            side=intent.side,
            quantity=intent.quantity,
            price=fill_price,
            fee=fee,
            instrument_exchange=intent.instrument.exchange,
            instrument_market_type=intent.instrument.market_type.value,
            instrument_symbol=intent.instrument.symbol,
            market_type_enum=intent.instrument.market_type,
            instrument_ref=intent.instrument,
            now=clock,
            position_id=uuid4(),
        )
        self.ledger.record_order(order)
        self.ledger.record_fill(fill)
        if intent.side is OrderSide.BUY:
            self._record_entry_protection(
                key,
                position_entry=position.entry_price,
                position_qty=position.quantity,
                prior_qty=prior_qty,
                fee=fee,
                stop_loss=stop_loss,
                take_profit=take_profit,
                max_hold_bars=max_hold_bars,
                client_order_id=intent.client_order_id,
                now=clock,
            )
        else:
            self._record_reduction(
                key,
                reason=ExitReason.SIGNAL,
                close_client_order_id=intent.client_order_id,
                quantity=intent.quantity,
                exit_price=fill_price,
                exit_fee=fee,
                prior_qty=prior_qty,
                prior_entry=prior_entry,
                now=clock,
            )
        return order

    def _record_entry_protection(
        self,
        key: str,
        *,
        position_entry: Decimal | None,
        position_qty: Decimal,
        prior_qty: Decimal,
        fee: Decimal,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
        max_hold_bars: int | None,
        client_order_id: str,
        now: datetime,
    ) -> None:
        """Record SL/TP + entry bookkeeping for the (long) position after a BUY fill.

        Adding to an existing position: newly approved non-None levels replace the
        prior ones; a None level keeps the prior level (a stop is never silently
        removed). ``opened_at`` moves to this fill so the entry bar is never scanned,
        and the time-stop bar count restarts at 0 from this fill.
        """
        if position_entry is None or position_qty <= 0:
            self.ledger.protections.pop(key, None)
            return
        prior = self.ledger.protections.get(key) if prior_qty > 0 else None
        self.ledger.protections[key] = ProtectiveLevels(
            stop_loss=stop_loss if stop_loss is not None else (
                prior.stop_loss if prior is not None else None
            ),
            take_profit=take_profit if take_profit is not None else (
                prior.take_profit if prior is not None else None
            ),
            entry_price=position_entry,
            quantity=position_qty,
            entry_fees=(prior.entry_fees if prior is not None else _ZERO) + fee,
            opened_at=now,
            entry_client_order_id=client_order_id,
            last_checked_open_time=None,
            max_hold_bars=max_hold_bars if max_hold_bars is not None else (
                prior.max_hold_bars if prior is not None else None
            ),
            bars_held=0,
        )

    def _record_reduction(
        self,
        key: str,
        *,
        reason: ExitReason,
        close_client_order_id: str,
        quantity: Decimal,
        exit_price: Decimal,
        exit_fee: Decimal,
        prior_qty: Decimal,
        prior_entry: Decimal | None,
        now: datetime,
    ) -> ClosedTrade | None:
        """Log a realized trade for a SELL that reduced a long; shrink/clear protection."""
        if prior_qty <= 0 or prior_entry is None:
            return None
        close_qty = min(quantity, prior_qty)
        protection = self.ledger.protections.get(key)
        entry_fees = _ZERO
        initial_risk: Decimal | None = None
        opened_at: datetime | None = None
        if protection is not None:
            if protection.quantity > 0:
                entry_fees = protection.entry_fees * close_qty / protection.quantity
            initial_risk = protection.initial_risk(close_qty)
            opened_at = protection.opened_at
        realized = (exit_price - prior_entry) * close_qty - entry_fees - exit_fee
        r_multiple = None if initial_risk is None else realized / initial_risk
        trade = ClosedTrade(
            instrument_key=key,
            reason=reason,
            close_client_order_id=close_client_order_id,
            quantity=close_qty,
            entry_price=prior_entry,
            exit_price=exit_price,
            entry_fees=entry_fees,
            exit_fee=exit_fee,
            realized_pnl=realized,
            initial_risk=initial_risk,
            r_multiple=r_multiple,
            opened_at=opened_at,
            closed_at=now,
        )
        self.ledger.closed_trades.append(trade)
        remaining = prior_qty - close_qty
        if protection is not None:
            if remaining <= 0:
                self.ledger.protections.pop(key, None)
            else:
                protection.entry_fees -= entry_fees
                protection.quantity = remaining
        return trade

    def assert_exit_path_allowed(self) -> None:
        """Paper protective exits never run as a side channel in live mode."""
        if self._settings.trading_mode == TradingMode.LIVE:
            raise PaperTradingBlocked("paper exits refused when trading_mode=live")

    def _fill_protective_exit(
        self,
        instrument: InstrumentRef,
        *,
        price: Decimal,
        reason: ExitReason,
        now: datetime,
        exchanged_at: datetime,
    ) -> tuple[Order, Fill, ClosedTrade]:
        """Reduce-only full close of a paper long at a simulated SL/TP/time-stop price.

        Only ``aegis.paper.exits.apply_exits`` calls this. It does not go through
        Risk (it can only shrink an existing paper position) and never touches an
        exchange. Refused in live mode; allowed under the kill switch because the
        simulated stop / target is modeled as already resting since entry.
        """
        self.assert_exit_path_allowed()
        if reason not in {ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT, ExitReason.TIME_STOP}:
            raise PaperFillError(f"unsupported protective exit reason={reason!r}")
        if price <= 0:
            raise PaperFillError(f"protective exit price must be positive ({price})")
        key = instrument_key_for(instrument)
        position = self.ledger.positions.get(key)
        if position is None or position.quantity <= 0:
            raise PaperFillError("no open paper long position to exit")
        quantity = position.quantity
        prior_entry = position.entry_price
        fee = compute_fee(quantity, price, self.fee_bps)
        client_order_id = f"paper-exit-{reason.value}-{uuid4().hex[:16]}"
        order_id = uuid4()
        order = Order(
            order_id=order_id,
            intent_id=uuid4(),
            client_order_id=client_order_id,
            exchange_order_id=None,
            ledger_kind=LedgerKind.PAPER,
            instrument=instrument,
            status=OrderStatus.FILLED,
            side=OrderSide.SELL,
            # Stop / time stop modeled as a market sell (slippage applied by caller);
            # target as a resting limit at the take-profit price.
            order_type="LIMIT" if reason == ExitReason.TAKE_PROFIT else "MARKET",
            quantity=quantity,
            filled_quantity=quantity,
            price=price,
            created_at=now,
            updated_at=now,
        )
        fill = Fill(
            fill_id=uuid4(),
            order_id=order_id,
            ledger_kind=LedgerKind.PAPER,
            instrument=instrument,
            quantity=quantity,
            price=price,
            fee=fee,
            fee_asset=self.ledger.quote_asset,
            exchanged_at=exchanged_at,
            received_at=now,
        )
        apply_cash_and_position(
            self.ledger,
            side=OrderSide.SELL,
            quantity=quantity,
            price=price,
            fee=fee,
            instrument_exchange=instrument.exchange,
            instrument_market_type=instrument.market_type.value,
            instrument_symbol=instrument.symbol,
            market_type_enum=instrument.market_type,
            instrument_ref=instrument,
            now=now,
            position_id=uuid4(),
        )
        self.ledger.record_order(order)
        self.ledger.record_fill(fill)
        trade = self._record_reduction(
            key,
            reason=reason,
            close_client_order_id=client_order_id,
            quantity=quantity,
            exit_price=price,
            exit_fee=fee,
            prior_qty=quantity,
            prior_entry=prior_entry,
            now=now,
        )
        if trade is None:  # pragma: no cover - guarded by the position check above
            raise PaperFillError("protective exit could not be recorded")
        return order, fill, trade

    def _assert_paper_path_allowed(self, intent: OrderIntent) -> None:
        if intent.ledger_kind != LedgerKind.PAPER:
            raise PaperLedgerKindError(
                f"PaperBroker refuses ledger_kind={intent.ledger_kind.value!r}"
            )
        if self._settings.kill_switch or not trading_ready(self._settings):
            raise PaperTradingBlocked("kill_switch or trading_ready blocks new paper orders")
        if self._settings.trading_mode == TradingMode.LIVE:
            # Paper broker must not run as a side-channel while live mode is configured.
            raise PaperTradingBlocked(
                "PaperBroker refuses submit when trading_mode=live "
                "(use paper/development for simulation)"
            )
        if intent.instrument.market_type.value == "futures":
            raise PaperTradingBlocked(
                "PaperBroker refuses futures until margin, funding, and liquidation "
                "semantics are implemented"
            )
