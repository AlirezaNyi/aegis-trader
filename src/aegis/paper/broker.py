"""Paper broker — simulates fills after Risk APPROVE; never calls exchange APIs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from aegis.config.settings import Settings, TradingMode
from aegis.guards.live import trading_ready
from aegis.paper.exceptions import PaperFillError, PaperLedgerKindError, PaperTradingBlocked
from aegis.paper.fills import compute_fee, compute_fill_price
from aegis.paper.ledger import PaperLedger, apply_cash_and_position
from aegis.risk.handoff import assert_may_submit_to_order_manager
from aegis.schemas.common import LedgerKind
from aegis.schemas.orders import Fill, Order, OrderIntent, OrderStatus
from aegis.schemas.risk import RiskDecision


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
        self._assert_decision_binds_intent(decision, intent)
        return self._apply_approved_intent(intent, mid_price=mid_price, now=clock)

    def _assert_decision_binds_intent(
        self, decision: RiskDecision, intent: OrderIntent
    ) -> None:
        if decision.proposal_id != intent.proposal_id:
            raise PaperFillError(
                "RiskDecision.proposal_id does not match OrderIntent.proposal_id"
            )
        if decision.correlation_id != intent.risk_decision_correlation_id:
            raise PaperFillError(
                "RiskDecision.correlation_id does not match "
                "OrderIntent.risk_decision_correlation_id"
            )

    def _apply_approved_intent(
        self,
        intent: OrderIntent,
        *,
        mid_price: Decimal | None = None,
        now: datetime | None = None,
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
        if intent.side.strip().upper() == "BUY":
            needed = intent.quantity * fill_price + fee
            if self.ledger.get_balance() < needed:
                raise PaperFillError(
                    f"insufficient paper {self.ledger.quote_asset} balance "
                    f"(need={needed}, have={self.ledger.get_balance()})"
                )
        elif intent.side.strip().upper() == "SELL":
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

        apply_cash_and_position(
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
        return order

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
