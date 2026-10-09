"""Deterministic Risk Engine — sole APPROVE/REJECT authority for new trades."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aegis.config.settings import Settings, TradingMode
from aegis.guards.live import trading_ready
from aegis.risk.context import RiskContext
from aegis.risk.policy import (
    NEW_TRADE_REQUIRED_PARAM_IDS,
    ParamStatus,
    RiskPolicy,
    default_draft_policy,
    require_approved,
)
from aegis.schemas.common import MarketType
from aegis.schemas.proposal import ProposalAction, TradeDirection, TradeProposal
from aegis.schemas.risk import RiskDecision, RiskDecisionType

# Side-mapping note for validated params (API_CONTRACTS.md).
_SIDE_MAPPING_NOTE = (
    "Internal BUY/SELL are proposal intents; exchange side mapping is performed "
    "only inside Order Manager / adapter after Risk APPROVE."
)
_STOP_NOT_GUARANTEED = (
    "Stop-loss instructions are not a guarantee of fill price or fill occurrence."
)


def evaluate_risk(
    proposal: TradeProposal,
    context: RiskContext,
    settings: Settings,
    policy: RiskPolicy | None = None,
) -> RiskDecision:
    """Evaluate a proposal. Same inputs + policy_version ⇒ same decision."""
    active = policy if policy is not None else default_draft_policy()
    rules_evaluated: list[str] = []
    reasons: list[dict[str, Any]] = []
    live_mode = settings.trading_mode == TradingMode.LIVE

    def _fail(rule_id: str, detail: str) -> None:
        if rule_id not in rules_evaluated:
            rules_evaluated.append(rule_id)
        reasons.append({"rule_id": rule_id, "detail": detail})

    def _mark(rule_id: str) -> None:
        if rule_id not in rules_evaluated:
            rules_evaluated.append(rule_id)

    def _unapproved(param_id: str, *, extra: str = "") -> None:
        detail = f"{param_id} is UNAPPROVED; numeric/membership checks cannot run"
        if live_mode:
            detail = f"{detail}; live path blocked until owner-approved policy"
        if extra:
            detail = f"{detail}; {extra}"
        _fail(param_id, detail)

    def _need_approved(param_id: str) -> Any | None:
        value, err = require_approved(active, param_id)
        _mark(param_id)
        if err is not None:
            _unapproved(param_id) if "UNAPPROVED" in err else _fail(param_id, err)
            return None
        return value

    # supervisor_model_meta / uncertainty must never flip the decision.
    _ = proposal.supervisor_model_meta.get("force_approve")
    _ = proposal.uncertainty

    # --- 0. Live must not treat draft policy as an owner-approved version ---
    _mark("RP-POLICY-VERSION")
    if live_mode and active.is_draft:
        _fail(
            "RP-POLICY-VERSION",
            "trading_mode=live cannot use a draft policy version "
            f"({active.policy_version!r}); owner-approved policy required",
        )

    # --- 1. Kill switch ---
    _mark("RP-KILL-SWITCH")
    if settings.kill_switch:
        _fail("RP-KILL-SWITCH", "kill_switch is true; new orders blocked")

    # --- 2–3. Mode / live armed (reuse guards.live via trading_ready) ---
    _mark("RP-MODE")
    _mark("RP-LIVE-ARMED")
    if settings.trading_mode == TradingMode.LIVE and not settings.live_armed:
        _fail("RP-LIVE-ARMED", "trading_mode=live requires live_armed=true")
    if not trading_ready(settings):
        if settings.kill_switch:
            pass  # already recorded
        elif settings.trading_mode == TradingMode.LIVE:
            _fail(
                "RP-MODE",
                "live trading not ready (live_armed/kill_switch gates via trading_ready)",
            )
        else:
            _fail("RP-MODE", "trading_ready is false for configured mode")

    # --- 4. Action / direction ---
    _mark("RP-ACTION")
    if proposal.action in {ProposalAction.NO_TRADE, ProposalAction.HOLD}:
        _fail(
            "RP-ACTION",
            f"action={proposal.action.value} is not a new-trade approve candidate",
        )
    if proposal.direction == TradeDirection.FLAT:
        _fail("RP-ACTION", "direction=flat implies no new order to approve")
    if proposal.action not in {ProposalAction.BUY, ProposalAction.SELL}:
        # Defensive: any other action is reject
        if proposal.action not in {ProposalAction.NO_TRADE, ProposalAction.HOLD}:
            _fail("RP-ACTION", f"unsupported action={proposal.action.value}")

    is_new_trade = (
        proposal.action in {ProposalAction.BUY, ProposalAction.SELL}
        and proposal.direction != TradeDirection.FLAT
    )

    # --- 5. Proposal TTL / expiry ---
    _mark("RP-PROPOSAL-TTL")
    if proposal.expires_at <= context.now:
        _fail("RP-PROPOSAL-TTL", "proposal.expires_at <= now; proposal expired")
    ttl_ms, ttl_err = require_approved(active, "RP-PROPOSAL-TTL-MS")
    _mark("RP-PROPOSAL-TTL-MS")
    if ttl_err is not None:
        if live_mode or is_new_trade:
            _unapproved("RP-PROPOSAL-TTL-MS")
    else:
        age_ms = int((context.now - proposal.created_at).total_seconds() * 1000)
        max_age = int(ttl_ms)  # type: ignore[arg-type]
        if age_ms > max_age:
            _fail(
                "RP-PROPOSAL-TTL-MS",
                f"proposal age_ms={age_ms} exceeds RP-PROPOSAL-TTL-MS={max_age}",
            )

    if not is_new_trade:
        return _build_decision(
            RiskDecisionType.REJECT,
            proposal,
            context,
            active,
            rules_evaluated,
            reasons,
            validated=None,
        )

    # --- Allowlists / strategy / markets / directions ---
    market = proposal.instrument.market_type
    allowlist_id = (
        "RP-INSTRUMENT-ALLOWLIST-SPOT"
        if market == MarketType.SPOT
        else "RP-INSTRUMENT-ALLOWLIST-FUTURES"
    )
    allowlist = _need_approved(allowlist_id)
    if allowlist is not None:
        symbols = _as_str_set(allowlist)
        if proposal.instrument.symbol not in symbols:
            _fail(
                allowlist_id,
                f"symbol {proposal.instrument.symbol!r} not in allowlist",
            )

    markets = _need_approved("RP-MARKETS-ALLOWED")
    if markets is not None:
        if market.value not in _as_str_set(markets):
            _fail(
                "RP-MARKETS-ALLOWED",
                f"market_type={market.value!r} not in allowed markets",
            )

    directions = _need_approved("RP-DIRECTIONS-ALLOWED")
    if directions is not None:
        allowed = _directions_for_market(directions, market.value)
        direction_value = (
            proposal.direction.value if proposal.direction is not None else None
        )
        if direction_value is None or direction_value not in allowed:
            _fail(
                "RP-DIRECTIONS-ALLOWED",
                f"direction={direction_value!r} not permitted for {market.value}",
            )

    strategy_list = _need_approved("RP-STRATEGY-ALLOWLIST")
    if strategy_list is not None:
        keys = _as_str_set(strategy_list)
        strategy_key = f"{proposal.strategy_id}@{proposal.strategy_version}"
        if (
            strategy_key not in keys
            and proposal.strategy_id not in keys
        ):
            _fail(
                "RP-STRATEGY-ALLOWLIST",
                f"strategy {strategy_key!r} not on allowlist",
            )

    # --- Data freshness / integrity ---
    freshness = _need_approved("RP-DATA-FRESHNESS-MS")
    if freshness is not None:
        if context.market_data_age_ms is None:
            _fail("RP-DATA-FRESHNESS-MS", "market_data_age_ms missing")
        elif context.market_data_age_ms > int(freshness):
            _fail(
                "RP-DATA-FRESHNESS-MS",
                f"market_data_age_ms={context.market_data_age_ms} > max={int(freshness)}",
            )

    _mark("RP-DATA-INTEGRITY")
    if context.market_integrity_ok is not True:
        _fail(
            "RP-DATA-INTEGRITY",
            f"market_integrity_ok={context.market_integrity_ok!r}; require True",
        )

    # --- Account / reconcile ---
    _mark("RP-ACCOUNT-STATE")
    if context.account_available is not True:
        _fail(
            "RP-ACCOUNT-STATE",
            f"account_available={context.account_available!r}; require True",
        )
    if context.balance_reconciled is not True:
        _fail(
            "RP-ACCOUNT-STATE",
            f"balance_reconciled={context.balance_reconciled!r}; require True",
        )

    _mark("RP-RECONCILE")
    if context.reconciliation_ok is not True:
        _fail(
            "RP-RECONCILE",
            f"reconciliation_ok={context.reconciliation_ok!r}; require True",
        )

    # --- Sizing / notional / leverage / counts ---
    max_order = _need_approved("RP-MAX-NOTIONAL-PER-ORDER")
    if max_order is not None:
        if context.proposed_notional is None:
            _fail("RP-MAX-NOTIONAL-PER-ORDER", "proposed_notional missing")
        elif _to_decimal(context.proposed_notional) > _to_decimal(max_order):
            _fail(
                "RP-MAX-NOTIONAL-PER-ORDER",
                f"proposed_notional={context.proposed_notional} > max={max_order}",
            )

    max_instr = _need_approved("RP-MAX-NOTIONAL-PER-INSTRUMENT")
    if max_instr is not None:
        if context.instrument_notional is None:
            _fail("RP-MAX-NOTIONAL-PER-INSTRUMENT", "instrument_notional missing")
        else:
            projected = _to_decimal(context.instrument_notional)
            if context.proposed_notional is not None:
                projected = projected + _to_decimal(context.proposed_notional)
            if projected > _to_decimal(max_instr):
                _fail(
                    "RP-MAX-NOTIONAL-PER-INSTRUMENT",
                    f"projected instrument notional={projected} > max={max_instr}",
                )

    max_agg = _need_approved("RP-MAX-AGGREGATE-NOTIONAL")
    if max_agg is not None:
        if context.aggregate_notional is None:
            _fail("RP-MAX-AGGREGATE-NOTIONAL", "aggregate_notional missing")
        else:
            projected = _to_decimal(context.aggregate_notional)
            if context.proposed_notional is not None:
                projected = projected + _to_decimal(context.proposed_notional)
            if projected > _to_decimal(max_agg):
                _fail(
                    "RP-MAX-AGGREGATE-NOTIONAL",
                    f"projected aggregate notional={projected} > max={max_agg}",
                )

    max_open = _need_approved("RP-MAX-OPEN-POSITIONS")
    if max_open is not None:
        if context.open_positions_count is None:
            _fail("RP-MAX-OPEN-POSITIONS", "open_positions_count missing")
        elif context.open_positions_count >= int(max_open):
            # At capacity: a new trade would exceed the approved max.
            _fail(
                "RP-MAX-OPEN-POSITIONS",
                f"open_positions_count={context.open_positions_count} >= max={int(max_open)}",
            )

    max_pending = _need_approved("RP-MAX-PENDING-ORDERS")
    if max_pending is not None:
        if context.pending_orders_count is None:
            _fail("RP-MAX-PENDING-ORDERS", "pending_orders_count missing")
        elif context.pending_orders_count >= int(max_pending):
            _fail(
                "RP-MAX-PENDING-ORDERS",
                f"pending_orders_count={context.pending_orders_count} >= max={int(max_pending)}",
            )

    if market == MarketType.FUTURES:
        max_lev = _need_approved("RP-MAX-LEVERAGE")
        if max_lev is not None:
            if proposal.leverage is None:
                _fail("RP-MAX-LEVERAGE", "leverage missing on futures proposal")
            elif _to_decimal(proposal.leverage) > _to_decimal(max_lev):
                _fail(
                    "RP-MAX-LEVERAGE",
                    f"leverage={proposal.leverage} > max={max_lev}",
                )
    else:
        _mark("RP-MAX-LEVERAGE")

    # --- Daily loss / drawdown ---
    daily = _need_approved("RP-DAILY-LOSS-LIMIT")
    if daily is not None:
        if context.daily_loss is None:
            _fail("RP-DAILY-LOSS-LIMIT", "daily_loss missing")
        elif _to_decimal(context.daily_loss) > _to_decimal(daily):
            _fail(
                "RP-DAILY-LOSS-LIMIT",
                f"daily_loss={context.daily_loss} > limit={daily}",
            )

    dd = _need_approved("RP-DRAWDOWN-LIMIT")
    if dd is not None:
        if context.drawdown is None:
            _fail("RP-DRAWDOWN-LIMIT", "drawdown missing")
        elif _to_decimal(context.drawdown) > _to_decimal(dd):
            _fail(
                "RP-DRAWDOWN-LIMIT",
                f"drawdown={context.drawdown} > limit={dd}",
            )

    sizing_method = _need_approved("RP-SIZING-METHOD")
    if sizing_method is not None:
        method = proposal.sizing.get("method") or proposal.sizing.get("sizing_method")
        if method != sizing_method:
            _fail(
                "RP-SIZING-METHOD",
                f"sizing method={method!r} does not match approved {sizing_method!r}",
            )

    # --- Spread / liquidity / fee / funding / slippage ---
    max_spread = _need_approved("RP-MAX-SPREAD")
    if max_spread is not None:
        if context.spread is None:
            _fail("RP-MAX-SPREAD", "spread missing")
        elif _to_decimal(context.spread) > _to_decimal(max_spread):
            _fail(
                "RP-MAX-SPREAD",
                f"spread={context.spread} > max={max_spread}",
            )

    min_liq = _need_approved("RP-MIN-LIQUIDITY")
    if min_liq is not None:
        if context.liquidity_ok is not True:
            _fail(
                "RP-MIN-LIQUIDITY",
                f"liquidity_ok={context.liquidity_ok!r}; require True "
                f"(approved threshold={min_liq!r})",
            )

    max_slip = _need_approved("RP-MAX-SLIPPAGE-MODEL")
    if max_slip is not None:
        if context.slippage_model_bps is None:
            _fail("RP-MAX-SLIPPAGE-MODEL", "slippage_model_bps missing")
        elif _to_decimal(context.slippage_model_bps) > _to_decimal(max_slip):
            _fail(
                "RP-MAX-SLIPPAGE-MODEL",
                f"slippage_model_bps={context.slippage_model_bps} > max={max_slip}",
            )

    max_fee = _need_approved("RP-MAX-FEE-ESTIMATE")
    if max_fee is not None:
        if context.fee_estimate is None:
            _fail("RP-MAX-FEE-ESTIMATE", "fee_estimate missing")
        elif _to_decimal(context.fee_estimate) > _to_decimal(max_fee):
            _fail(
                "RP-MAX-FEE-ESTIMATE",
                f"fee_estimate={context.fee_estimate} > max={max_fee}",
            )

    if market == MarketType.FUTURES:
        funding = _need_approved("RP-FUNDING-CONSTRAINT")
        if funding is not None and context.funding_ok is not True:
            _fail(
                "RP-FUNDING-CONSTRAINT",
                f"funding_ok={context.funding_ok!r}; require True "
                f"(constraint={funding!r})",
            )
    else:
        _mark("RP-FUNDING-CONSTRAINT")

    # --- Stop-loss / take-profit ---
    stop_required = _need_approved("RP-STOP-LOSS-REQUIRED")
    if stop_required is not None and bool(stop_required) and proposal.stop_loss is None:
        _fail(
            "RP-STOP-LOSS-REQUIRED",
            f"stop_loss required but missing; {_STOP_NOT_GUARANTEED}",
        )

    max_stop_dist = _need_approved("RP-STOP-LOSS-MAX-DISTANCE")
    if max_stop_dist is not None and proposal.stop_loss is not None:
        entry = _entry_price(proposal)
        if entry is None:
            _fail(
                "RP-STOP-LOSS-MAX-DISTANCE",
                "cannot verify stop distance without entry_price in "
                "entry_conditions/sizing; stop fill not guaranteed",
            )
        else:
            dist = abs(_to_decimal(entry) - _to_decimal(proposal.stop_loss))
            # Interpret approved value as absolute price distance unless unit implies %.
            param = active.get("RP-STOP-LOSS-MAX-DISTANCE")
            if param is not None and param.unit in {"%", "percent", "pct"}:
                pct = (
                    dist / _to_decimal(entry) * Decimal("100")
                    if entry != 0
                    else Decimal("Infinity")
                )
                if pct > _to_decimal(max_stop_dist):
                    _fail(
                        "RP-STOP-LOSS-MAX-DISTANCE",
                        f"stop distance pct={pct} > max={max_stop_dist}; "
                        f"{_STOP_NOT_GUARANTEED}",
                    )
            elif dist > _to_decimal(max_stop_dist):
                _fail(
                    "RP-STOP-LOSS-MAX-DISTANCE",
                    f"stop distance={dist} > max={max_stop_dist}; "
                    f"{_STOP_NOT_GUARANTEED}",
                )

    tp_policy = _need_approved("RP-TAKE-PROFIT-POLICY")
    if tp_policy is not None and tp_policy is True and proposal.take_profit is None:
        _fail("RP-TAKE-PROFIT-POLICY", "take_profit required by approved policy")

    # --- Order types / TIF ---
    order_types = _need_approved("RP-ORDER-TYPES-ALLOWED")
    order_type = (
        proposal.entry_conditions.get("order_type")
        or proposal.sizing.get("order_type")
    )
    if order_types is not None:
        if order_type is None:
            _fail("RP-ORDER-TYPES-ALLOWED", "order_type missing on proposal")
        elif str(order_type) not in _as_str_set(order_types):
            _fail(
                "RP-ORDER-TYPES-ALLOWED",
                f"order_type={order_type!r} not in allowed set",
            )

    tifs = _need_approved("RP-TIME-IN-FORCE-ALLOWED")
    tif = (
        proposal.entry_conditions.get("time_in_force")
        or proposal.entry_conditions.get("tif")
        or proposal.sizing.get("time_in_force")
    )
    if tifs is not None:
        if tif is None:
            _fail("RP-TIME-IN-FORCE-ALLOWED", "time_in_force missing on proposal")
        elif str(tif) not in _as_str_set(tifs):
            _fail(
                "RP-TIME-IN-FORCE-ALLOWED",
                f"time_in_force={tif!r} not in allowed set",
            )

    # --- Exchange metadata flags (not Aegis policy numbers) ---
    _mark("RP-SYMBOL-PRECISION")
    if context.symbol_precision_ok is not True:
        _fail(
            "RP-SYMBOL-PRECISION",
            f"symbol_precision_ok={context.symbol_precision_ok!r}; require True",
        )
    _mark("RP-MIN-SIZE")
    if context.min_size_ok is not True:
        _fail(
            "RP-MIN-SIZE",
            f"min_size_ok={context.min_size_ok!r}; require True",
        )

    # --- Duplicate window ---
    dup_window = _need_approved("RP-DUPLICATE-WINDOW")
    if dup_window is not None:
        if context.duplicate_detected is True:
            _fail(
                "RP-DUPLICATE-WINDOW",
                f"duplicate_detected=True within window={dup_window!r}",
            )
        elif context.duplicate_detected is None and context.recent_proposal_ids is None:
            _fail(
                "RP-DUPLICATE-WINDOW",
                "duplicate detector inputs missing "
                "(duplicate_detected and recent_proposal_ids)",
            )
        elif (
            context.recent_proposal_ids is not None
            and proposal.proposal_id in context.recent_proposal_ids
        ):
            _fail(
                "RP-DUPLICATE-WINDOW",
                "proposal_id appears in recent_proposal_ids",
            )
        elif context.duplicate_detected is None:
            _fail(
                "RP-DUPLICATE-WINDOW",
                "duplicate_detected is required when RP-DUPLICATE-WINDOW is APPROVED",
            )

    # --- Cooldown ---
    cooldown = _need_approved("RP-COOLDOWN-AFTER-LOSS")
    if cooldown is not None:
        if context.cooldown_active is None:
            _fail("RP-COOLDOWN-AFTER-LOSS", "cooldown_active missing")
        elif context.cooldown_active is True:
            _fail(
                "RP-COOLDOWN-AFTER-LOSS",
                f"cooldown_active=True (window={cooldown!r})",
            )

    # Ensure every required new-trade financial id was considered
    for param_id in NEW_TRADE_REQUIRED_PARAM_IDS:
        if param_id in {
            "RP-MAX-LEVERAGE",
            "RP-FUNDING-CONSTRAINT",
        } and market != MarketType.FUTURES:
            continue
        if param_id == allowlist_id:
            continue
        if param_id in {
            "RP-INSTRUMENT-ALLOWLIST-SPOT",
            "RP-INSTRUMENT-ALLOWLIST-FUTURES",
        }:
            # Only the market-relevant allowlist is required.
            if param_id != allowlist_id:
                continue
        _mark(param_id)
        param = active.get(param_id)
        if param is None or param.status != ParamStatus.APPROVED:
            if not any(r["rule_id"] == param_id for r in reasons):
                _unapproved(param_id)

    if reasons:
        return _build_decision(
            RiskDecisionType.REJECT,
            proposal,
            context,
            active,
            rules_evaluated,
            reasons,
            validated=None,
        )

    if not trading_ready(settings):
        _fail("RP-MODE", "trading_ready became false before APPROVE")
        return _build_decision(
            RiskDecisionType.REJECT,
            proposal,
            context,
            active,
            rules_evaluated,
            reasons,
            validated=None,
        )

    validated = _validated_params(proposal)
    return _build_decision(
        RiskDecisionType.APPROVE,
        proposal,
        context,
        active,
        rules_evaluated,
        reasons,
        validated=validated,
    )


def _build_decision(
    decision: RiskDecisionType,
    proposal: TradeProposal,
    context: RiskContext,
    policy: RiskPolicy,
    rules_evaluated: list[str],
    reasons: list[dict[str, Any]],
    *,
    validated: dict[str, Any] | None,
) -> RiskDecision:
    return RiskDecision(
        decision=decision,
        policy_version=policy.policy_version,
        rules_evaluated=list(rules_evaluated),
        rejection_reasons=list(reasons),
        validated_order_params=validated,
        account_state_refs=list(context.account_state_refs),
        market_state_refs=list(context.market_state_refs),
        decided_at=context.now,
        expires_at=proposal.expires_at,
        correlation_id=proposal.correlation_id,
        proposal_id=proposal.proposal_id,
    )


def _validated_params(proposal: TradeProposal) -> dict[str, Any]:
    return {
        "instrument": proposal.instrument.model_dump(mode="json"),
        "action": proposal.action.value,
        "direction": proposal.direction.value if proposal.direction else None,
        "side_mapping_note": _SIDE_MAPPING_NOTE,
        "sizing": dict(proposal.sizing),
        "leverage": str(proposal.leverage) if proposal.leverage is not None else None,
        "stop_loss": str(proposal.stop_loss) if proposal.stop_loss is not None else None,
        "take_profit": (
            str(proposal.take_profit) if proposal.take_profit is not None else None
        ),
        "stop_loss_fill_not_guaranteed": True,
        "stop_loss_note": _STOP_NOT_GUARANTEED,
        "entry_conditions": dict(proposal.entry_conditions),
        "strategy_id": proposal.strategy_id,
        "strategy_version": proposal.strategy_version,
    }


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _as_str_set(value: Any) -> set[str]:
    if isinstance(value, (set, frozenset, list, tuple)):
        return {str(item) for item in value}
    return {str(value)}


def _directions_for_market(value: Any, market: str) -> set[str]:
    if isinstance(value, dict):
        raw = value.get(market, value.get("all", []))
        return _as_str_set(raw)
    return _as_str_set(value)


def _entry_price(proposal: TradeProposal) -> Any | None:
    return (
        proposal.entry_conditions.get("entry_price")
        or proposal.entry_conditions.get("price")
        or proposal.sizing.get("entry_price")
        or proposal.sizing.get("price")
    )
