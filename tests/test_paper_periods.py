"""Paper equity tracker: UTC day / ISO week / month periods and loss-limit locks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aegis.paper.equity import (
    LossPeriod,
    PaperEquityTracker,
    next_period_start,
    period_start,
)

# 2026-10-18 is a Sunday; 2026-10-19 is a Monday.
SUNDAY_2359 = datetime(2026, 10, 18, 23, 59, tzinfo=UTC)
MONDAY_0000 = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
LIMIT = Decimal("2")  # arbitrary test limit (not a policy value)


def test_period_start_boundaries() -> None:
    assert period_start(LossPeriod.WEEKLY, SUNDAY_2359) == datetime(2026, 10, 12, tzinfo=UTC)
    assert period_start(LossPeriod.WEEKLY, MONDAY_0000) == MONDAY_0000
    assert next_period_start(LossPeriod.WEEKLY, SUNDAY_2359) == MONDAY_0000
    assert next_period_start(LossPeriod.DAILY, SUNDAY_2359) == MONDAY_0000
    eom = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)
    assert period_start(LossPeriod.MONTHLY, eom) == datetime(2026, 10, 1, tzinfo=UTC)
    assert next_period_start(LossPeriod.MONTHLY, eom) == datetime(2026, 11, 1, tzinfo=UTC)
    dec = datetime(2026, 12, 15, tzinfo=UTC)
    assert next_period_start(LossPeriod.MONTHLY, dec) == datetime(2027, 1, 1, tzinfo=UTC)
    # Non-UTC offsets are converted to UTC before bucketing.
    plus_two = timezone(timedelta(hours=2))
    assert period_start(LossPeriod.DAILY, datetime(2026, 10, 19, 2, 0, tzinfo=plus_two)) == (
        MONDAY_0000
    )
    assert period_start(
        LossPeriod.WEEKLY, datetime(2026, 10, 19, 1, 0, tzinfo=plus_two)
    ) == datetime(2026, 10, 12, tzinfo=UTC)


def test_week_rolls_at_monday_midnight_not_before() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=SUNDAY_2359 - timedelta(days=3))  # Thursday
    sunday = tracker.observe(Decimal("97"), now=SUNDAY_2359)
    assert sunday.weekly_loss == Decimal("3")
    # No mark between Thursday and Sunday: the unseen drop is attributed to Sunday
    # (start = last mark before the boundary), never silently dropped.
    assert sunday.daily_loss == Decimal("3")
    monday = tracker.observe(Decimal("97"), now=MONDAY_0000)
    assert monday.weekly_loss == Decimal("0")
    assert tracker.week_start_utc == MONDAY_0000.date()
    assert tracker.week_start_equity == Decimal("97")
    # Month did not roll: loss vs the October start equity remains.
    assert monday.monthly_loss == Decimal("3")


def test_month_rolls_on_first_of_month() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=datetime(2026, 10, 2, tzinfo=UTC))
    end = tracker.observe(Decimal("90"), now=datetime(2026, 10, 31, 23, 59, tzinfo=UTC))
    assert end.monthly_loss == Decimal("10")
    first = tracker.observe(Decimal("90"), now=datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    assert first.monthly_loss == Decimal("0")
    assert first.drawdown == Decimal("10")  # peak-based, never resets


def test_weekly_lock_until_next_monday_and_survives_recovery() -> None:
    tracker = PaperEquityTracker()
    wed = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    tracker.observe(Decimal("100"), now=wed - timedelta(days=2), weekly_loss_limit=LIMIT)
    # Thursday-ish daily start differs, but weekly start is Monday's 100.
    snap = tracker.observe(Decimal("97"), now=wed, weekly_loss_limit=LIMIT)
    assert snap.weekly_loss == Decimal("3")
    assert snap.period_lock_until == MONDAY_0000
    assert snap.locked_periods == (LossPeriod.WEEKLY,)

    recovered = tracker.observe(
        Decimal("105"), now=wed + timedelta(days=1), weekly_loss_limit=LIMIT
    )
    assert recovered.weekly_loss == Decimal("0")
    assert recovered.period_lock_until == MONDAY_0000  # lock persists after recovery

    after = tracker.observe(Decimal("105"), now=MONDAY_0000, weekly_loss_limit=LIMIT)
    assert after.period_lock_until is None
    assert after.locked_periods == ()


def test_monthly_lock_until_first_of_next_month() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=datetime(2026, 10, 1, tzinfo=UTC))
    snap = tracker.observe(
        Decimal("95"),
        now=datetime(2026, 10, 20, tzinfo=UTC),
        monthly_loss_limit=Decimal("4"),
    )
    assert snap.period_lock_until == datetime(2026, 11, 1, tzinfo=UTC)
    assert snap.locked_periods == (LossPeriod.MONTHLY,)


def test_loss_equal_to_limit_does_not_lock() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=SUNDAY_2359 - timedelta(hours=1))
    snap = tracker.observe(Decimal("98"), now=SUNDAY_2359, daily_loss_limit=LIMIT)
    assert snap.daily_loss == LIMIT
    assert snap.period_lock_until is None
    assert snap.cooldown_active is False


def test_daily_breach_locks_to_midnight_and_keeps_24h_cooldown() -> None:
    tracker = PaperEquityTracker()
    t0 = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    tracker.observe(Decimal("100"), now=t0)
    snap = tracker.observe(Decimal("97"), now=t0 + timedelta(hours=1), daily_loss_limit=LIMIT)
    assert snap.period_lock_until == datetime(2026, 10, 15, tzinfo=UTC)
    assert snap.locked_periods == (LossPeriod.DAILY,)
    assert snap.cooldown_active is True
    assert tracker.cooldown_until == t0 + timedelta(hours=25)
    next_day = tracker.observe(Decimal("97"), now=datetime(2026, 10, 15, 1, tzinfo=UTC))
    assert next_day.period_lock_until is None
    assert next_day.cooldown_active is True  # approved 24h cooldown outlives the day lock


def test_multiple_locks_report_latest_expiry() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=datetime(2026, 10, 14, 1, tzinfo=UTC))
    snap = tracker.observe(
        Decimal("90"),
        now=datetime(2026, 10, 14, 2, tzinfo=UTC),
        daily_loss_limit=LIMIT,
        weekly_loss_limit=LIMIT,
        monthly_loss_limit=LIMIT,
    )
    assert snap.locked_periods == (LossPeriod.DAILY, LossPeriod.WEEKLY, LossPeriod.MONTHLY)
    assert snap.period_lock_until == datetime(2026, 11, 1, tzinfo=UTC)


def test_state_round_trip_preserves_periods_and_locks() -> None:
    tracker = PaperEquityTracker()
    wed = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    tracker.observe(Decimal("100"), now=wed - timedelta(days=2))
    tracker.observe(Decimal("96"), now=wed, weekly_loss_limit=LIMIT, daily_loss_limit=LIMIT)
    state = tracker.to_state()
    restored = PaperEquityTracker.from_state(state)
    assert restored.to_state() == state
    later = wed + timedelta(days=2)
    snap = restored.observe(Decimal("110"), now=later, weekly_loss_limit=LIMIT)
    assert snap.period_lock_until == MONDAY_0000  # lock survived the "restart"
    assert snap.drawdown == Decimal("0")
    assert restored.initial_equity == Decimal("100")


def test_restore_state_rejects_malformed_without_partial_mutation() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=MONDAY_0000)
    before = tracker.to_state()
    bad = dict(before)
    bad["week_start_equity"] = "not-a-number"
    with pytest.raises(ValueError):
        tracker.restore_state(bad)
    assert tracker.to_state() == before
    with pytest.raises(ValueError):
        tracker.restore_state({**before, "version": 99})
    with pytest.raises(ValueError):
        tracker.restore_state({**before, "period_locks": {"yearly": MONDAY_0000.isoformat()}})


def test_state_lost_reports_none_and_never_starts_fresh() -> None:
    tracker = PaperEquityTracker()
    tracker.mark_unavailable("state_load_failed", state_lost=True)
    snap = tracker.observe(Decimal("100"), now=MONDAY_0000, daily_loss_limit=LIMIT)
    assert snap.state_available is False
    assert snap.daily_loss is None
    assert snap.weekly_loss is None
    assert snap.monthly_loss is None
    assert snap.drawdown is None
    assert snap.cooldown_active is True
    assert tracker.initial_equity is None  # did not silently initialize
    tracker.clear_unavailable()  # cannot clear a lost state
    assert tracker.state_available is False
    with pytest.raises(ValueError):
        tracker.to_state()


def test_transient_unavailability_keeps_tracking_but_reports_none() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=MONDAY_0000)
    tracker.mark_unavailable("state_persist_failed")
    snap = tracker.observe(Decimal("97"), now=MONDAY_0000 + timedelta(hours=1))
    assert snap.daily_loss is None and snap.drawdown is None
    assert snap.state_available is False
    tracker.clear_unavailable()
    snap = tracker.snapshot(now=MONDAY_0000 + timedelta(hours=1))
    assert snap.daily_loss == Decimal("3")
    assert snap.drawdown == Decimal("3")


def test_rollover_start_equity_is_last_mark_before_boundary() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=SUNDAY_2359 - timedelta(minutes=5))
    tracker.observe(Decimal("99"), now=SUNDAY_2359)
    # First Monday mark arrives late and already lower: loss since the boundary counts.
    snap = tracker.observe(Decimal("96"), now=MONDAY_0000 + timedelta(minutes=7))
    assert tracker.day_start_equity == Decimal("99")
    assert tracker.week_start_equity == Decimal("99")
    assert snap.daily_loss == Decimal("3")
    assert snap.weekly_loss == Decimal("3")


def test_clock_moving_backwards_keeps_current_period() -> None:
    tracker = PaperEquityTracker()
    tracker.observe(Decimal("100"), now=MONDAY_0000 + timedelta(days=1))
    snap = tracker.observe(Decimal("99"), now=MONDAY_0000)
    assert tracker.day_utc == (MONDAY_0000 + timedelta(days=1)).date()
    assert snap.daily_loss == Decimal("1")
