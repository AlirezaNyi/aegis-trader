"""Verified Toobit trade paths used by the Phase 7 adapter.

Submit paths are listed in docs/API_CONTRACTS.md. Query/cancel leaf paths are
taken from the official Spot Account/Trade and USDT-M API v2 pages that
API_CONTRACTS points Phase 7 to implement from. Withdraw is listed only to deny.
"""

from __future__ import annotations

REST_BASE_URL = "https://api.toobit.com"

# Explicitly listed in docs/API_CONTRACTS.md §3.3 / §3.4
VERIFIED_SUBMIT_PATHS: dict[str, str] = {
    "spot_order": "/api/v1/spot/order",
    "spot_order_test": "/api/v1/spot/orderTest",
    "futures_order": "/api/v2/futures/order",
}

# Official docs pages cited by API_CONTRACTS for Phase 7 query/cancel
# Spot: https://api-docs.toobit.com/api/spot-account-and-trading
# Futures: https://api-docs.toobit.com/api/usdt-m-api-v2
VERIFIED_QUERY_PATHS: dict[str, str] = {
    "spot_get_order": "/api/v1/spot/order",
    "spot_cancel_order": "/api/v1/spot/order",
    "futures_get_order": "/api/v2/futures/order",
    "futures_cancel_order": "/api/v2/futures/order",
}

WITHDRAW_PATH = "/api/v1/account/withdraw"
