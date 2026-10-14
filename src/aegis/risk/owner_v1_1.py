"""Owner-set Risk Policy version 1.1 ("1.1-env").

Financial limits come from owner-set environment values, expressed as a percent
of the initial capital base ``AEGIS_PAPER_INITIAL_BALANCE``. Setting a value in
env is the owner's approval of that value. Engineering supplies **no** defaults:
a missing value leaves the derived parameter UNAPPROVED, so the Risk Engine
rejects every new trade (fail closed).

Everything else (allowlists, directions, leverage, order types, TIF, stop
distance, spread/fee/slippage caps, freshness, TTL, cooldown, ...) is inherited
unchanged from owner v1.0.

The effective ``policy_version`` is ``1.1-env+<sha256[:8]>`` over the canonical
parameter set, so every decision records exactly which limits were in force.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from aegis.config.settings import Settings
from aegis.risk.owner_v1 import owner_approved_policy_v1
from aegis.risk.policy import (
    SIZING_METHOD_PCT_CAPITAL,
    SIZING_METHOD_RISK_PCT_CAPITAL,
    TAKE_PROFIT_POLICY_REQUIRED,
    ParamStatus,
    PolicyParam,
    RiskPolicy,
)

OWNER_V1_1_VERSION = "1.1"
OWNER_V1_1_ENV_PREFIX = "1.1-env"
# Accepted ``AEGIS_RISK_POLICY_VERSION`` values (lower-cased) selecting this policy.
OWNER_V1_1_ALIASES: frozenset[str] = frozenset({OWNER_V1_1_VERSION, OWNER_V1_1_ENV_PREFIX})

_HUNDRED = Decimal("100")

# (env var name, Settings attribute) required by policy 1.1.
# AEGIS_RISK_PER_TRADE_PCT is optional: when unset, RP-MAX-RISK-PER-TRADE is absent
# and sizing is pct_capital. AEGIS_TRADING_FEE_BPS feeds RiskContext costs, not policy.
_REQUIRED_ENV: tuple[tuple[str, str], ...] = (
    ("AEGIS_PAPER_INITIAL_BALANCE", "paper_initial_balance"),
    ("AEGIS_RISK_POSITION_PCT", "risk_position_pct"),
    ("AEGIS_RISK_DAILY_LOSS_PCT", "risk_daily_loss_pct"),
    ("AEGIS_RISK_WEEKLY_LOSS_PCT", "risk_weekly_loss_pct"),
    ("AEGIS_RISK_MONTHLY_LOSS_PCT", "risk_monthly_loss_pct"),
    ("AEGIS_RISK_MAX_DRAWDOWN_PCT", "risk_max_drawdown_pct"),
    ("AEGIS_RISK_MIN_REWARD_RISK", "risk_min_reward_risk"),
)


def is_env_policy_selected(settings: Settings) -> bool:
    """True when ``AEGIS_RISK_POLICY_VERSION`` selects policy 1.1 (env-driven)."""
    return settings.risk_policy_version.strip().lower() in OWNER_V1_1_ALIASES


def missing_policy_env(settings: Settings) -> list[str]:
    """Env var names required by the selected env-driven policy that are unset.

    Returns ``[]`` when the selected policy version does not read limits from env
    (e.g. ``1.0`` or the draft). Names only — never values.
    """
    if not is_env_policy_selected(settings):
        return []
    return [name for name, attr in _REQUIRED_ENV if getattr(settings, attr) is None]


def _pct_of(capital: Decimal | None, pct: Decimal | None) -> Decimal | None:
    if capital is None or pct is None:
        return None
    return capital * pct / _HUNDRED


def _param(param_id: str, value: Any | None, unit: str) -> PolicyParam:
    """APPROVED when the owner supplied the inputs; UNAPPROVED (value None) otherwise."""
    if value is None:
        return PolicyParam(id=param_id, status=ParamStatus.UNAPPROVED, value=None, unit=unit)
    return PolicyParam(id=param_id, status=ParamStatus.APPROVED, value=value, unit=unit)


def owner_env_policy_v1_1(settings: Settings) -> RiskPolicy:
    """Build policy 1.1 from owner env values (percent of initial capital)."""
    base = owner_approved_policy_v1()
    capital = settings.paper_initial_balance
    position_cap = _pct_of(capital, settings.risk_position_pct)

    # No pyramiding: per-instrument cap equals per-order cap. Aggregate cap keeps
    # the owner-approved v1.0 open-position count, bounded structurally by the
    # capital base: min(position_cap * max_open_positions, C). 100% of C means
    # "no leverage on spot"; it is a structural bound, not a tunable number.
    aggregate: Decimal | None = None
    open_positions = base.get("RP-MAX-OPEN-POSITIONS")
    if (
        capital is not None
        and position_cap is not None
        and open_positions is not None
        and open_positions.status == ParamStatus.APPROVED
        and open_positions.value is not None
    ):
        aggregate = min(position_cap * Decimal(int(open_positions.value)), capital)

    per_trade_pct = settings.risk_per_trade_pct
    sizing_method = (
        SIZING_METHOD_RISK_PCT_CAPITAL if per_trade_pct is not None else SIZING_METHOD_PCT_CAPITAL
    )

    overlay: dict[str, PolicyParam] = {
        "RP-MAX-NOTIONAL-PER-ORDER": _param("RP-MAX-NOTIONAL-PER-ORDER", position_cap, "USDT"),
        "RP-MAX-NOTIONAL-PER-INSTRUMENT": _param(
            "RP-MAX-NOTIONAL-PER-INSTRUMENT", position_cap, "USDT"
        ),
        "RP-MAX-AGGREGATE-NOTIONAL": _param("RP-MAX-AGGREGATE-NOTIONAL", aggregate, "USDT"),
        "RP-DAILY-LOSS-LIMIT": _param(
            "RP-DAILY-LOSS-LIMIT", _pct_of(capital, settings.risk_daily_loss_pct), "USDT"
        ),
        "RP-WEEKLY-LOSS-LIMIT": _param(
            "RP-WEEKLY-LOSS-LIMIT", _pct_of(capital, settings.risk_weekly_loss_pct), "USDT"
        ),
        "RP-MONTHLY-LOSS-LIMIT": _param(
            "RP-MONTHLY-LOSS-LIMIT", _pct_of(capital, settings.risk_monthly_loss_pct), "USDT"
        ),
        "RP-DRAWDOWN-LIMIT": _param(
            "RP-DRAWDOWN-LIMIT", _pct_of(capital, settings.risk_max_drawdown_pct), "USDT"
        ),
        "RP-MIN-REWARD-RISK": _param(
            "RP-MIN-REWARD-RISK", settings.risk_min_reward_risk, "ratio"
        ),
        "RP-TAKE-PROFIT-POLICY": _param(
            "RP-TAKE-PROFIT-POLICY", TAKE_PROFIT_POLICY_REQUIRED, "policy_id"
        ),
        "RP-SIZING-METHOD": _param("RP-SIZING-METHOD", sizing_method, "enum"),
    }
    if per_trade_pct is not None:
        overlay["RP-MAX-RISK-PER-TRADE"] = _param(
            "RP-MAX-RISK-PER-TRADE", _pct_of(capital, per_trade_pct), "USDT"
        )

    params: dict[str, PolicyParam] = dict(base.params)
    params.update(overlay)
    version = f"{OWNER_V1_1_ENV_PREFIX}+{policy_fingerprint(params)}"
    return RiskPolicy(policy_version=version, params=params)


def policy_fingerprint(params: Mapping[str, PolicyParam]) -> str:
    """First 8 hex chars of sha256 over the canonical, sorted parameter set.

    Decimals are normalized (``10`` == ``10.0``) so formatting differences in env
    do not change the fingerprint; any value, unit, or status change does.
    """
    payload = {
        pid: {
            "status": param.status.value,
            "unit": param.unit,
            "value": _canonical(param.value),
        }
        for pid, param in sorted(params.items())
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Decimal):
        if value.is_finite():
            return "D:" + format(value.normalize(), "f")
        return "D:" + str(value)
    if isinstance(value, int):
        return "D:" + str(value)
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (set, frozenset)):
        items = [_canonical(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return str(value)
