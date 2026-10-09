"""Live execution gates: trading_mode + live_armed + kill_switch.

Models must never write these flags. Phase 1 never constructs a live exchange submit client.
"""

from __future__ import annotations

from aegis.config.settings import Settings, TradingMode


class LiveExecutionNotAllowed(RuntimeError):
    """Raised when live exchange submission is blocked by policy gates."""


def assert_live_execution_allowed(settings: Settings) -> None:
    """Raise unless all three live gates permit submission."""
    if settings.trading_mode != TradingMode.LIVE:
        raise LiveExecutionNotAllowed(
            f"live execution blocked: trading_mode={settings.trading_mode.value!r} (need 'live')"
        )
    if not settings.live_armed:
        raise LiveExecutionNotAllowed("live execution blocked: live_armed is false")
    if settings.kill_switch:
        raise LiveExecutionNotAllowed("live execution blocked: kill_switch is true")


def live_execution_permitted(settings: Settings) -> bool:
    try:
        assert_live_execution_allowed(settings)
    except LiveExecutionNotAllowed:
        return False
    return True


def trading_ready(settings: Settings) -> bool:
    """Whether the system may accept new trading activity for the configured mode.

    Paper/development are ready from a mode perspective. Live requires all gates.
    Kill switch always blocks new trading readiness.
    """
    if settings.kill_switch:
        return False
    if settings.trading_mode == TradingMode.LIVE:
        return live_execution_permitted(settings)
    return True
