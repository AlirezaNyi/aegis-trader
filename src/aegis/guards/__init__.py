"""Safety guards for execution modes."""

from aegis.guards.live import LiveExecutionNotAllowed, assert_live_execution_allowed

__all__ = ["LiveExecutionNotAllowed", "assert_live_execution_allowed"]
