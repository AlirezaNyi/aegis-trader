"""Paper equity / period loss / drawdown / loss-limit lock tracking for RiskContext."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID

from aegis.paper.ledger import PaperLedger
from aegis.schemas.market import InstrumentRef

# Owner-approved RP-COOLDOWN-AFTER-LOSS value from risk/owner_v1.py — do not invent.
_APPROVED_COOLDOWN_AFTER_LOSS = timedelta(hours=24)


def instrument_mark_key(instrument: InstrumentRef) -> str:
    return (
        f"{instrument.exchange}:{instrument.market_type.value}:{instrument.symbol}"
    )


def compute_paper_equity(
    ledger: PaperLedger,
    *,
    marks: dict[str, Decimal] | None = None,
) -> Decimal:
    """Cash plus marked positions. Missing marks fall back to entry_price then 0."""
    equity = ledger.get_balance()
    mark_map = marks or {}
    for key, pos in ledger.positions.items():
        if pos.quantity == 0:
            continue
        mark = mark_map.get(key)
        if mark is None:
            mark = pos.entry_price if pos.entry_price is not None else Decimal("0")
        equity += pos.quantity * mark
    return equity


class LossPeriod(StrEnum):
    """Loss-accounting periods (UTC). Weeks start Monday 00:00 UTC."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


_PERIOD_ORDER: tuple[LossPeriod, ...] = (LossPeriod.DAILY, LossPeriod.WEEKLY, LossPeriod.MONTHLY)
TRACKER_STATE_VERSION = 1


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def period_start(period: LossPeriod, now: datetime) -> datetime:
    """Start of the UTC day / ISO week (Monday) / calendar month containing ``now``."""
    day = _utc(now).date()
    if period == LossPeriod.DAILY:
        start = day
    elif period == LossPeriod.WEEKLY:
        start = day - timedelta(days=day.weekday())
    else:
        start = day.replace(day=1)
    return datetime(start.year, start.month, start.day, tzinfo=UTC)


def next_period_start(period: LossPeriod, now: datetime) -> datetime:
    """Start of the period after the one containing ``now`` (lock expiry instant)."""
    start = period_start(period, now)
    if period == LossPeriod.DAILY:
        return start + timedelta(days=1)
    if period == LossPeriod.WEEKLY:
        return start + timedelta(days=7)
    if start.month == 12:
        return datetime(start.year + 1, 1, 1, tzinfo=UTC)
    return datetime(start.year, start.month + 1, 1, tzinfo=UTC)


def _loss(start_equity: Decimal | None, equity: Decimal) -> Decimal | None:
    if start_equity is None:
        return None
    loss = start_equity - equity
    return loss if loss > 0 else Decimal("0")


@dataclass
class PaperEquitySnapshot:
    """Equity-derived RiskContext inputs. ``None`` losses mean unknown ⇒ Risk REJECT."""

    equity: Decimal
    daily_loss: Decimal | None
    drawdown: Decimal | None
    cooldown_active: bool
    weekly_loss: Decimal | None = None
    monthly_loss: Decimal | None = None
    # Latest active loss-limit lock (max over periods); None ⇒ no lock in force.
    period_lock_until: datetime | None = None
    locked_periods: tuple[LossPeriod, ...] = ()
    state_available: bool = True
    unavailable_reason: str | None = None


@dataclass
class PaperEquityTracker:
    """Tracks peak equity and UTC day / ISO week / calendar month start equity.

    Losses are ``max(0, period_start_equity - equity)``; drawdown is
    ``max(0, peak - equity)``. A loss above an owner limit locks new trades until the
    start of the next period of that type; the lock survives equity recovery and
    restarts (when persisted). A daily breach additionally starts the approved 24h
    cooldown (RP-COOLDOWN-AFTER-LOSS).

    Unavailable state (``mark_unavailable``) reports ``None`` losses so Risk rejects:
    ``state_lost=True`` (persisted state could not be loaded) also freezes the tracker
    — it never silently starts fresh.
    """

    initial_equity: Decimal | None = None
    peak_equity: Decimal = Decimal("0")
    day_start_equity: Decimal | None = None
    day_utc: date | None = None
    cooldown_active: bool = False
    cooldown_until: datetime | None = None
    week_start_utc: date | None = None
    week_start_equity: Decimal | None = None
    month_start_utc: date | None = None
    month_start_equity: Decimal | None = None
    period_locks: dict[LossPeriod, datetime] = field(default_factory=dict)
    last_equity: Decimal | None = None
    last_observed_at: datetime | None = None
    unavailable_reason: str | None = None
    state_lost: bool = False

    # --- availability -------------------------------------------------------

    @property
    def state_available(self) -> bool:
        return self.unavailable_reason is None and not self.state_lost

    def mark_unavailable(self, reason: str, *, state_lost: bool = False) -> None:
        """Report None losses until cleared. ``state_lost`` also freezes updates."""
        self.unavailable_reason = reason
        if state_lost:
            self.state_lost = True

    def clear_unavailable(self) -> None:
        """Clear a transient (non state-lost) unavailability, e.g. after a good save."""
        if not self.state_lost:
            self.unavailable_reason = None

    def reset(self) -> None:
        """Return to a fresh tracker (only for an explicitly fresh persisted start)."""
        fresh = PaperEquityTracker()
        for name in fresh.__dataclass_fields__:
            setattr(self, name, getattr(fresh, name))

    # --- observation ---------------------------------------------------------

    def observe(
        self,
        equity: Decimal,
        *,
        now: datetime,
        daily_loss_limit: Decimal | None = None,
        weekly_loss_limit: Decimal | None = None,
        monthly_loss_limit: Decimal | None = None,
    ) -> PaperEquitySnapshot:
        """Record marked equity at ``now``, roll periods, apply limits, snapshot."""
        clock = _utc(now)
        value = Decimal(equity)
        if self.state_lost:
            return self._unavailable_snapshot(value)
        self._roll_periods(value, clock)
        self.last_equity = value
        self.last_observed_at = clock
        if value > self.peak_equity:
            self.peak_equity = value
        return self.evaluate_limits(
            now=clock,
            daily_loss_limit=daily_loss_limit,
            weekly_loss_limit=weekly_loss_limit,
            monthly_loss_limit=monthly_loss_limit,
        )

    def evaluate_limits(
        self,
        *,
        now: datetime,
        daily_loss_limit: Decimal | None = None,
        weekly_loss_limit: Decimal | None = None,
        monthly_loss_limit: Decimal | None = None,
    ) -> PaperEquitySnapshot:
        """Apply absolute USDT loss limits to the last observed equity.

        ``loss > limit`` locks that period until its next start. ``None`` limits are
        not enforced here (Risk rejects unapproved / missing params on its own).
        """
        clock = _utc(now)
        self._expire(clock)
        if self.state_lost or self.last_equity is None:
            return self.snapshot(now=clock)
        equity = self.last_equity
        for period, start_equity, limit in (
            (LossPeriod.DAILY, self.day_start_equity, daily_loss_limit),
            (LossPeriod.WEEKLY, self.week_start_equity, weekly_loss_limit),
            (LossPeriod.MONTHLY, self.month_start_equity, monthly_loss_limit),
        ):
            loss = _loss(start_equity, equity)
            if limit is None or loss is None or not loss > Decimal(limit):
                continue
            until = next_period_start(period, clock)
            current = self.period_locks.get(period)
            if current is None or until > current:
                self.period_locks[period] = until
            if period == LossPeriod.DAILY:
                self.cooldown_active = True
                cooldown_until = clock + _APPROVED_COOLDOWN_AFTER_LOSS
                if self.cooldown_until is None or cooldown_until > self.cooldown_until:
                    self.cooldown_until = cooldown_until
        return self.snapshot(now=clock)

    def snapshot(self, *, now: datetime) -> PaperEquitySnapshot:
        """Read-only view at ``now`` of the last observed equity (no mutation)."""
        clock = _utc(now)
        if self.state_lost:
            return self._unavailable_snapshot(self.last_equity or Decimal("0"))
        active = {p: u for p, u in self.period_locks.items() if clock < u}
        locked = tuple(p for p in _PERIOD_ORDER if p in active)
        lock_until = max(active.values()) if active else None
        cooldown = self.cooldown_active and (
            self.cooldown_until is None or clock < self.cooldown_until
        )
        equity = self.last_equity
        if equity is None or not self.state_available:
            return PaperEquitySnapshot(
                equity=equity if equity is not None else Decimal("0"),
                daily_loss=None,
                drawdown=None,
                cooldown_active=cooldown,
                weekly_loss=None,
                monthly_loss=None,
                period_lock_until=lock_until,
                locked_periods=locked,
                state_available=self.state_available,
                unavailable_reason=self.unavailable_reason,
            )
        drawdown = self.peak_equity - equity
        return PaperEquitySnapshot(
            equity=equity,
            daily_loss=_loss(self.day_start_equity, equity),
            drawdown=drawdown if drawdown > 0 else Decimal("0"),
            cooldown_active=cooldown,
            weekly_loss=_loss(self.week_start_equity, equity),
            monthly_loss=_loss(self.month_start_equity, equity),
            period_lock_until=lock_until,
            locked_periods=locked,
        )

    def _unavailable_snapshot(self, equity: Decimal) -> PaperEquitySnapshot:
        # State unknown: fail closed on every equity-derived input.
        return PaperEquitySnapshot(
            equity=equity,
            daily_loss=None,
            drawdown=None,
            cooldown_active=True,
            weekly_loss=None,
            monthly_loss=None,
            period_lock_until=None,
            locked_periods=(),
            state_available=False,
            unavailable_reason=self.unavailable_reason or "state_lost",
        )

    def _roll_periods(self, equity: Decimal, clock: datetime) -> None:
        today = clock.date()
        week = today - timedelta(days=today.weekday())
        month = today.replace(day=1)
        if self.initial_equity is None:
            self.initial_equity = equity
            self.peak_equity = equity
        # New period start equity = last mark observed before the boundary (best
        # estimate of boundary equity; losses during downtime or the first partial
        # bar are attributed to the new period rather than dropped). First ever
        # observation: the current equity.
        anchor = self.last_equity if self.last_equity is not None else equity
        # Forward-only rollover: a clock that moves backwards keeps the stored period.
        if self.day_utc is None or today > self.day_utc:
            self.day_start_equity = anchor if self.day_utc is not None else equity
            self.day_utc = today
        elif self.day_start_equity is None:
            self.day_start_equity = equity
        if self.week_start_utc is None or week > self.week_start_utc:
            self.week_start_equity = anchor if self.week_start_utc is not None else equity
            self.week_start_utc = week
        elif self.week_start_equity is None:
            self.week_start_equity = equity
        if self.month_start_utc is None or month > self.month_start_utc:
            self.month_start_equity = anchor if self.month_start_utc is not None else equity
            self.month_start_utc = month
        elif self.month_start_equity is None:
            self.month_start_equity = equity

    def _expire(self, clock: datetime) -> None:
        for period in [p for p, until in self.period_locks.items() if clock >= until]:
            del self.period_locks[period]
        if self.cooldown_until is not None and clock >= self.cooldown_until:
            self.cooldown_active = False
            self.cooldown_until = None

    # --- persistence ---------------------------------------------------------

    def to_state(self) -> dict[str, Any]:
        """JSON-safe state (Decimal as str, datetimes ISO-8601 UTC)."""
        if self.state_lost:
            raise ValueError("refusing to serialize a tracker whose state was lost")
        return {
            "version": TRACKER_STATE_VERSION,
            "initial_equity": _s(self.initial_equity),
            "peak_equity": str(self.peak_equity),
            "day_utc": None if self.day_utc is None else self.day_utc.isoformat(),
            "day_start_equity": _s(self.day_start_equity),
            "week_start_utc": (
                None if self.week_start_utc is None else self.week_start_utc.isoformat()
            ),
            "week_start_equity": _s(self.week_start_equity),
            "month_start_utc": (
                None if self.month_start_utc is None else self.month_start_utc.isoformat()
            ),
            "month_start_equity": _s(self.month_start_equity),
            "cooldown_active": self.cooldown_active,
            "cooldown_until": _iso(self.cooldown_until),
            "period_locks": {
                p.value: _utc(u).isoformat() for p, u in sorted(self.period_locks.items())
            },
            "last_equity": _s(self.last_equity),
            "last_observed_at": _iso(self.last_observed_at),
        }

    def restore_state(self, state: Mapping[str, Any]) -> None:
        """Adopt persisted state in place. Raises ValueError on any malformed field.

        Parsing completes before any attribute is assigned, so a bad payload never
        leaves a half-restored tracker.
        """
        if state.get("version") != TRACKER_STATE_VERSION:
            raise ValueError(f"unsupported tracker state version {state.get('version')!r}")
        cooldown_active = state.get("cooldown_active")
        if not isinstance(cooldown_active, bool):
            raise ValueError("cooldown_active must be a bool")
        locks_raw = state.get("period_locks") or {}
        if not isinstance(locks_raw, Mapping):
            raise ValueError("period_locks must be an object")
        locks: dict[LossPeriod, datetime] = {}
        for key, raw in locks_raw.items():
            until = _parse_dt(raw)
            if until is None:
                raise ValueError(f"period lock {key!r} has no expiry")
            locks[LossPeriod(str(key))] = until
        peak = _parse_dec(state.get("peak_equity"))
        parsed: dict[str, Any] = {
            "initial_equity": _parse_dec(state.get("initial_equity")),
            "peak_equity": peak if peak is not None else Decimal("0"),
            "day_utc": _parse_date(state.get("day_utc")),
            "day_start_equity": _parse_dec(state.get("day_start_equity")),
            "week_start_utc": _parse_date(state.get("week_start_utc")),
            "week_start_equity": _parse_dec(state.get("week_start_equity")),
            "month_start_utc": _parse_date(state.get("month_start_utc")),
            "month_start_equity": _parse_dec(state.get("month_start_equity")),
            "cooldown_active": cooldown_active,
            "cooldown_until": _parse_dt(state.get("cooldown_until")),
            "period_locks": locks,
            "last_equity": _parse_dec(state.get("last_equity")),
            "last_observed_at": _parse_dt(state.get("last_observed_at")),
        }
        for name, value in parsed.items():
            setattr(self, name, value)
        self.unavailable_reason = None
        self.state_lost = False

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> PaperEquityTracker:
        tracker = cls()
        tracker.restore_state(state)
        return tracker


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _utc(value).isoformat()


def _parse_dec(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError("decimal field must not be a bool")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal {raw!r}") from exc
    if not value.is_finite():
        raise ValueError(f"non-finite decimal {raw!r}")
    return value


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    return _utc(datetime.fromisoformat(str(raw)))


def _parse_date(raw: Any) -> date | None:
    if raw is None:
        return None
    return date.fromisoformat(str(raw))


@dataclass
class ProposalHistory:
    """Short in-memory proposal id window for duplicate detection."""

    max_len: int = 64
    _ids: deque[UUID] = field(default_factory=deque)

    @property
    def recent_ids(self) -> list[UUID]:
        return list(self._ids)

    def is_duplicate(self, proposal_id: UUID) -> bool:
        return proposal_id in self._ids

    def record(self, proposal_id: UUID) -> None:
        if proposal_id in self._ids:
            return
        self._ids.append(proposal_id)
        while len(self._ids) > self.max_len:
            self._ids.popleft()
