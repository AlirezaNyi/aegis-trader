"""Toobit exchange adapter — credential plane and live submit path (Phase 7).

Live submit code path exists (LIVE_SUBMIT_SUPPORTED=True). create_app must not
construct the live client by default; use ``build_execution_port`` which returns
NullExecutionPort unless live gates + credentials are present.
"""

from __future__ import annotations

from aegis.exchange.errors import AmbiguousSubmitError, ExchangeError, RateLimitError
from aegis.exchange.factory import build_execution_port
from aegis.exchange.futures_client import ToobitFuturesExecutionClient
from aegis.exchange.paths import VERIFIED_QUERY_PATHS, VERIFIED_SUBMIT_PATHS
from aegis.exchange.router import ToobitExecutionRouter
from aegis.exchange.spot_client import ToobitSpotExecutionClient
from aegis.exchange.withdraw import WithdrawForbiddenError, assert_withdraw_forbidden

PHASE = 7
LIVE_SUBMIT_SUPPORTED = True

__all__ = [
    "PHASE",
    "LIVE_SUBMIT_SUPPORTED",
    "VERIFIED_QUERY_PATHS",
    "VERIFIED_SUBMIT_PATHS",
    "AmbiguousSubmitError",
    "ExchangeError",
    "RateLimitError",
    "WithdrawForbiddenError",
    "assert_withdraw_forbidden",
    "build_execution_port",
    "ToobitSpotExecutionClient",
    "ToobitFuturesExecutionClient",
    "ToobitExecutionRouter",
]
