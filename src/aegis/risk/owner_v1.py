"""Owner-approved Risk Policy version 1.0.

Values come from the project owner's explicit approval (2026-10-09):
risk capital 10 USDT, daily loss 2.2 USDT, markets spot+futures, symbols
ADA/BTC/TRX/BNB/ETH as *USDT. Conservative defaults applied where the owner
said ``approve`` without answering follow-up options (leverage 1, LIMIT-only,
long-only, emergency automation off).

This module must not invent new financial numbers. Changes require a new
policy version and a fresh owner signature in docs/RISK_POLICY.md.
"""

from __future__ import annotations

from decimal import Decimal

from aegis.risk.policy import (
    ENGINEERING_DEFAULT_PARAMS,
    FINANCIAL_PARAM_IDS,
    ParamStatus,
    PolicyParam,
    RiskPolicy,
)

OWNER_V1_VERSION = "1.0"

# Symbols the owner listed (normalized to Toobit-style USDT pairs).
_OWNER_SYMBOLS: frozenset[str] = frozenset(
    {"ADAUSDT", "BTCUSDT", "TRXUSDT", "BNBUSDT", "ETHUSDT"}
)

# (value, unit) for every financial param — all APPROVED in v1.0.
_OWNER_V1_VALUES: dict[str, tuple[object, str | None]] = {
    "RP-INSTRUMENT-ALLOWLIST-SPOT": (_OWNER_SYMBOLS, "set"),
    "RP-INSTRUMENT-ALLOWLIST-FUTURES": (_OWNER_SYMBOLS, "set"),
    "RP-MARKETS-ALLOWED": (frozenset({"spot", "futures"}), "set"),
    "RP-DIRECTIONS-ALLOWED": (
        {"spot": ["long"], "futures": ["long"]},
        "map",
    ),
    "RP-MAX-LEVERAGE": (Decimal("1"), "ratio"),
    "RP-MAX-NOTIONAL-PER-ORDER": (Decimal("2"), "USDT"),
    "RP-MAX-NOTIONAL-PER-INSTRUMENT": (Decimal("4"), "USDT"),
    "RP-MAX-AGGREGATE-NOTIONAL": (Decimal("10"), "USDT"),
    "RP-MAX-OPEN-POSITIONS": (2, "count"),
    "RP-MAX-PENDING-ORDERS": (2, "count"),
    "RP-DAILY-LOSS-LIMIT": (Decimal("2.2"), "USDT"),
    "RP-DRAWDOWN-LIMIT": (Decimal("3.0"), "USDT"),
    "RP-SIZING-METHOD": ("fixed_notional", "enum"),
    "RP-STOP-LOSS-REQUIRED": (True, "bool"),
    "RP-STOP-LOSS-MAX-DISTANCE": (Decimal("5"), "%"),
    "RP-TAKE-PROFIT-POLICY": ("optional", "policy_id"),
    "RP-ORDER-TYPES-ALLOWED": (frozenset({"LIMIT"}), "set"),
    "RP-TIME-IN-FORCE-ALLOWED": (frozenset({"GTC"}), "set"),
    "RP-MAX-SPREAD": (Decimal("20"), "bps"),
    "RP-MIN-LIQUIDITY": ("depth_ok", "flag"),
    "RP-MAX-SLIPPAGE-MODEL": (Decimal("10"), "bps"),
    "RP-MAX-FEE-ESTIMATE": (Decimal("20"), "bps"),
    "RP-FUNDING-CONSTRAINT": ("ok_flag", "flag"),
    "RP-DATA-FRESHNESS-MS": (5000, "ms"),
    "RP-PROPOSAL-TTL-MS": (30_000, "ms"),
    "RP-COOLDOWN-AFTER-LOSS": ("24h", "duration"),
    "RP-STRATEGY-ALLOWLIST": (
        frozenset({"aegis-default", "aegis-default@0.1.0", "demo", "demo@0.0.1"}),
        "set",
    ),
    "RP-DUPLICATE-WINDOW": ("60s", "duration"),
    # Approved as disabled automation (ADR 0006: block-new only until owner enables).
    "RP-EMERGENCY-CANCEL": (False, "bool"),
    "RP-EMERGENCY-PROTECT": (False, "bool"),
}


def owner_approved_policy_v1() -> RiskPolicy:
    """Return the owner-signed policy snapshot ``1.0``."""
    missing = set(FINANCIAL_PARAM_IDS) - set(_OWNER_V1_VALUES)
    if missing:
        msg = f"owner v1.0 missing financial params: {sorted(missing)}"
        raise RuntimeError(msg)

    params: dict[str, PolicyParam] = dict(ENGINEERING_DEFAULT_PARAMS)
    for param_id, (value, unit) in _OWNER_V1_VALUES.items():
        params[param_id] = PolicyParam(
            id=param_id,
            status=ParamStatus.APPROVED,
            value=value,
            unit=unit,
        )
    return RiskPolicy(policy_version=OWNER_V1_VERSION, params=params)
