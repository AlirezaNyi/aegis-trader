"""Deterministic Risk Engine — sole APPROVE/REJECT authority for new trades."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation
from typing import Any

from aegis.config.settings import Settings, TradingMode
from aegis.guards.live import trading_ready
from aegis.risk.context import RiskContext
from aegis.risk.owner_v1_1 import OWNER_V1_1_ENV_PREFIX
from aegis.risk.policy import (
    NEW_TRADE_REQUIRED_PARAM_IDS,
    OPTIONAL_FINANCIAL_PARAM_IDS,
    TAKE_PROFIT_POLICY_REQUIRED,
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
# Structural (non-numeric) rules: always enforced, independent of policy approval.
_RULE_STOP_SIDE = "RP-STOP-LOSS-SIDE"
_RULE_TP_SIDE = "RP-TAKE-PROFIT-SIDE"
_RULE_ENTRY_VS_BOOK = "RP-ENTRY-VS-BOOK"
# Caller-set lock after a loss-limit breach (structural; no owner number).
_RULE_PERIOD_LOCK = "RP-PERIOD-LOCK"
# Policy 1.1+ params, enforced only when present in the active policy snapshot.
_RULE_WEEKLY_LOSS = "RP-WEEKLY-LOSS-LIMIT"
_RULE_MONTHLY_LOSS = "RP-MONTHLY-LOSS-LIMIT"
_RULE_MIN_REWARD_RISK = "RP-MIN-REWARD-RISK"
_RULE_MAX_RISK_PER_TRADE = "RP-MAX-RISK-PER-TRADE"
_BPS = Decimal("10000")
_RATIO_DISPLAY = Decimal("0.0001")
_USDT_DISPLAY = Decimal("0.00000001")
# Order types for which a declared entry_price is a resting limit price. A
# missing order_type is treated as LIMIT because intent building defaults to it.
_MARKET_ORDER_TYPES = frozenset({"MARKET"})


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

    # --- 0b. Env-derived policy 1.1 is paper-only (safety review H2) ---
    # Structural gate, no numbers: env values are accepted as owner approval for
    # paper only. Live needs a recorded owner live approval and a live capital
    # base, neither of which exists yet (docs/RISK_POLICY.md §9).
    _mark("RP-ENV-POLICY-LIVE")
    if live_mode and active.policy_version.strip().lower().startswith(OWNER_V1_1_ENV_PREFIX):
        _fail(
            "RP-ENV-POLICY-LIVE",
            "env-derived policy 1.1 is paper-only until owner records live approval "
            f"and a live capital base (policy_version={active.policy_version!r})",
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

    # --- Weekly / monthly loss (policy 1.1+; only when present in the policy) ---
    for period_rule, period_loss, label in (
        (_RULE_WEEKLY_LOSS, context.weekly_loss, "weekly_loss"),
        (_RULE_MONTHLY_LOSS, context.monthly_loss, "monthly_loss"),
    ):
        if active.get(period_rule) is None:
            continue
        period_limit = _need_approved(period_rule)
        if period_limit is None:
            continue
        observed = _try_decimal(period_loss)
        limit_dec = _try_decimal(period_limit)
        if observed is None:
            _fail(period_rule, f"{label} missing or non-numeric ({period_loss!r})")
        elif limit_dec is None:
            _fail(period_rule, f"approved limit is non-numeric ({period_limit!r})")
        elif observed > limit_dec:
            _fail(period_rule, f"{label}={observed} > limit={limit_dec}")

    # --- Period lock (structural; caller sets it after a loss-limit breach) ---
    _mark(_RULE_PERIOD_LOCK)
    if context.period_lock_until is not None:
        try:
            locked = context.now < context.period_lock_until
        except TypeError:
            # Naive vs aware datetimes cannot be ordered: fail closed.
            _fail(
                _RULE_PERIOD_LOCK,
                "period_lock_until is not comparable with now (timezone mismatch)",
            )
        else:
            if locked:
                _fail(
                    _RULE_PERIOD_LOCK,
                    "new trades locked until "
                    f"{context.period_lock_until.isoformat()} after a loss-limit breach",
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
        entry_dec = _try_decimal(entry)
        if entry is None:
            _fail(
                "RP-STOP-LOSS-MAX-DISTANCE",
                "cannot verify stop distance without entry_price in "
                "entry_conditions/sizing; stop fill not guaranteed",
            )
        elif entry_dec is None:
            _fail(
                "RP-STOP-LOSS-MAX-DISTANCE",
                f"cannot verify stop distance: non-numeric entry_price={entry!r}",
            )
        else:
            entry = entry_dec
            dist = abs(entry_dec - _to_decimal(proposal.stop_loss))
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
    if (
        tp_policy is not None
        and _take_profit_required(tp_policy)
        and proposal.take_profit is None
    ):
        _fail("RP-TAKE-PROFIT-POLICY", "take_profit required by approved policy")

    # --- Stop-loss / take-profit side vs entry (structural, non-numeric) ---
    # Evaluated regardless of policy approval status: a stop/target on the wrong
    # side of entry is a malformed proposal, not a tunable threshold.
    if proposal.stop_loss is not None:
        _mark(_RULE_STOP_SIDE)
        err = _level_side_error(proposal, proposal.stop_loss, protective=True)
        if err is not None:
            _fail(_RULE_STOP_SIDE, f"stop_loss {err}; {_STOP_NOT_GUARANTEED}")
    if proposal.take_profit is not None:
        _mark(_RULE_TP_SIDE)
        err = _level_side_error(proposal, proposal.take_profit, protective=False)
        if err is not None:
            _fail(_RULE_TP_SIDE, f"take_profit {err}")

    # --- Net reward:risk after round-trip costs (policy 1.1+; when present) ---
    reward_risk_net: Decimal | None = None
    if active.get(_RULE_MIN_REWARD_RISK) is not None:
        min_rr = _need_approved(_RULE_MIN_REWARD_RISK)
        if min_rr is not None:
            min_rr_dec = _try_decimal(min_rr)
            r_net, rr_err = _net_reward_risk(proposal, context)
            if min_rr_dec is None:
                _fail(_RULE_MIN_REWARD_RISK, f"approved minimum is non-numeric ({min_rr!r})")
            elif rr_err is not None or r_net is None:
                _fail(_RULE_MIN_REWARD_RISK, f"cannot compute net reward:risk: {rr_err}")
            else:
                reward_risk_net = r_net
                if r_net < min_rr_dec:
                    _fail(
                        _RULE_MIN_REWARD_RISK,
                        f"reward_risk_net={_fmt_down(r_net, _RATIO_DISPLAY)} < "
                        f"min={min_rr_dec} (after round-trip fee/spread/slippage costs)",
                    )

    # --- Max loss if the stop is hit, incl. costs (policy 1.1+; when present) ---
    if active.get(_RULE_MAX_RISK_PER_TRADE) is not None:
        risk_budget = _need_approved(_RULE_MAX_RISK_PER_TRADE)
        if risk_budget is not None:
            budget_dec = _try_decimal(risk_budget)
            implied_loss, loss_err = _implied_stop_loss(proposal, context)
            if budget_dec is None:
                _fail(
                    _RULE_MAX_RISK_PER_TRADE,
                    f"approved budget is non-numeric ({risk_budget!r})",
                )
            elif loss_err is not None or implied_loss is None:
                _fail(
                    _RULE_MAX_RISK_PER_TRADE,
                    f"cannot bound loss at stop: {loss_err}; {_STOP_NOT_GUARANTEED}",
                )
            elif implied_loss > budget_dec:
                _fail(
                    _RULE_MAX_RISK_PER_TRADE,
                    f"implied loss at stop={_fmt_up(implied_loss, _USDT_DISPLAY)} USDT "
                    f"> max={budget_dec} USDT; {_STOP_NOT_GUARANTEED}",
                )

    # --- Order types / TIF ---
    order_types = _need_approved("RP-ORDER-TYPES-ALLOWED")
    order_type = (
        proposal.entry_conditions.get("order_type")
        or proposal.sizing.get("order_type")
    )

    # --- Entry price vs observed book (structural, non-numeric) ---
    # Ties the model-declared entry_price to the market so the stop/TP side
    # rules above cannot be satisfied by a fictitious entry. No tolerance:
    # any tolerance would be an owner-set number.
    if order_type is None or str(order_type).strip().upper() not in _MARKET_ORDER_TYPES:
        _mark(_RULE_ENTRY_VS_BOOK)
        err = _entry_vs_book_error(proposal, context)
        if err is not None:
            _fail(_RULE_ENTRY_VS_BOOK, err)
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

    # Optional (1.1+) params: absent ⇒ not enforced; present but not APPROVED ⇒ REJECT.
    for param_id in OPTIONAL_FINANCIAL_PARAM_IDS:
        param = active.get(param_id)
        if param is None:
            continue
        _mark(param_id)
        if param.status != ParamStatus.APPROVED and not any(
            r["rule_id"] == param_id for r in reasons
        ):
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
    if reward_risk_net is not None:
        validated["reward_risk_net"] = _fmt_down(reward_risk_net, _RATIO_DISPLAY)
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


def _try_decimal(value: Any) -> Decimal | None:
    """Parse to a finite Decimal or return None (fail-closed helper)."""
    if value is None:
        return None
    try:
        dec = _to_decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return dec if dec.is_finite() else None


def _fmt_down(value: Decimal, places: Decimal) -> str:
    """Display helper: truncate (never round a failing ratio up to a passing one)."""
    try:
        return str(value.quantize(places, rounding=ROUND_DOWN))
    except InvalidOperation:
        return str(value)


def _fmt_up(value: Decimal, places: Decimal) -> str:
    """Display helper for losses: round away from zero (never understate a loss)."""
    try:
        return str(value.quantize(places, rounding=ROUND_UP))
    except InvalidOperation:
        return str(value)


def _take_profit_required(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == TAKE_PROFIT_POLICY_REQUIRED


def _round_trip_cost_per_unit(
    entry: Decimal, context: RiskContext
) -> tuple[Decimal | None, str | None]:
    """Cost per unit of base asset for entry + exit, in quote currency.

    ``Cu = entry * (2*fee_bps + spread_bps + 2*slippage_bps) / 10000``. All three
    inputs are required (fail closed); negative inputs are rejected as invalid.
    """
    fee = _try_decimal(context.fee_estimate)
    spread = _try_decimal(context.spread)
    slippage = _try_decimal(context.slippage_model_bps)
    if fee is None or spread is None or slippage is None:
        missing = [
            name
            for name, value in (
                ("fee_estimate", fee),
                ("spread", spread),
                ("slippage_model_bps", slippage),
            )
            if value is None
        ]
        return None, f"cost inputs missing or non-numeric: {', '.join(missing)}"
    if fee < 0 or spread < 0 or slippage < 0:
        return None, (
            f"cost inputs must be >= 0 (fee_estimate={fee}, spread={spread}, "
            f"slippage_model_bps={slippage})"
        )
    round_trip_bps = Decimal(2) * fee + spread + Decimal(2) * slippage
    return entry * round_trip_bps / _BPS, None


def _positive_entry(proposal: TradeProposal) -> tuple[Decimal | None, str | None]:
    raw_entry = _entry_price(proposal)
    if raw_entry is None:
        return None, "entry_price missing"
    entry = _try_decimal(raw_entry)
    if entry is None:
        return None, f"non-numeric entry_price={raw_entry!r}"
    if entry <= 0:
        return None, f"entry_price={entry} must be positive"
    return entry, None


def _net_reward_risk(
    proposal: TradeProposal, context: RiskContext
) -> tuple[Decimal | None, str | None]:
    """``R_net = (|TP - entry| - Cu) / (|entry - SL| + Cu)``; missing input ⇒ error."""
    entry, err = _positive_entry(proposal)
    if entry is None:
        return None, err
    stop = _try_decimal(proposal.stop_loss)
    if stop is None:
        return None, "stop_loss missing or non-numeric"
    target = _try_decimal(proposal.take_profit)
    if target is None:
        return None, "take_profit missing or non-numeric"
    cost, cost_err = _round_trip_cost_per_unit(entry, context)
    if cost is None:
        return None, cost_err
    risk = abs(entry - stop) + cost
    if risk <= 0:
        return None, "risk per unit is zero (stop_loss equals entry_price and no costs)"
    reward = abs(target - entry) - cost
    return reward / risk, None


def _implied_stop_loss(
    proposal: TradeProposal, context: RiskContext
) -> tuple[Decimal | None, str | None]:
    """Loss (quote) if the stop fills at its price: ``notional/entry * (|entry-SL| + Cu)``."""
    entry, err = _positive_entry(proposal)
    if entry is None:
        return None, err
    notional = _try_decimal(context.proposed_notional)
    if notional is None:
        return None, "proposed_notional missing or non-numeric"
    if notional < 0:
        return None, f"proposed_notional={notional} must be >= 0"
    stop = _try_decimal(proposal.stop_loss)
    if stop is None:
        return None, "stop_loss missing or non-numeric"
    cost, cost_err = _round_trip_cost_per_unit(entry, context)
    if cost is None:
        return None, cost_err
    return notional / entry * (abs(entry - stop) + cost), None


def _as_str_set(value: Any) -> set[str]:
    if isinstance(value, (set, frozenset, list, tuple)):
        return {str(item) for item in value}
    return {str(value)}


def _directions_for_market(value: Any, market: str) -> set[str]:
    if isinstance(value, dict):
        raw = value.get(market, value.get("all", []))
        return _as_str_set(raw)
    return _as_str_set(value)


def _level_side_error(
    proposal: TradeProposal, level: Any, *, protective: bool
) -> str | None:
    """Return a failure detail if ``level`` is on the wrong side of entry.

    ``protective=True`` means stop-loss (long ⇒ below entry, short ⇒ above);
    ``protective=False`` means take-profit (long ⇒ above, short ⇒ below).
    Equal to entry fails. Missing/unparseable entry or direction fails closed.
    """
    direction = proposal.direction
    if direction not in {TradeDirection.LONG, TradeDirection.SHORT}:
        dval = direction.value if direction is not None else None
        return f"side cannot be verified: direction={dval!r} (require long/short)"
    raw_entry = _entry_price(proposal)
    if raw_entry is None:
        return (
            "side cannot be verified without entry_price in "
            "entry_conditions/sizing"
        )
    try:
        entry = _to_decimal(raw_entry)
        lvl = _to_decimal(level)
    except (InvalidOperation, ValueError, TypeError):
        return f"side cannot be verified: non-numeric entry_price={raw_entry!r}"
    if not entry.is_finite() or not lvl.is_finite():
        return f"side cannot be verified: non-finite entry={entry} level={lvl}"
    is_long = direction == TradeDirection.LONG
    must_be_below = is_long if protective else not is_long
    if must_be_below and not lvl < entry:
        return (
            f"value={lvl} must be strictly below entry_price={entry} "
            f"for direction={direction.value}"
        )
    if not must_be_below and not lvl > entry:
        return (
            f"value={lvl} must be strictly above entry_price={entry} "
            f"for direction={direction.value}"
        )
    return None


def _entry_vs_book_error(proposal: TradeProposal, context: RiskContext) -> str | None:
    """Return a failure detail if a limit entry_price crosses the observed book.

    BUY requires entry_price <= best_ask; SELL requires entry_price >= best_bid.
    Missing book side, missing/non-numeric/non-positive entry fail closed.
    """
    raw_entry = _entry_price(proposal)
    if raw_entry is None:
        return "entry_price missing; cannot verify against order book"
    entry = _try_decimal(raw_entry)
    if entry is None:
        return f"non-numeric entry_price={raw_entry!r}"
    if entry <= 0:
        return f"entry_price={entry} must be positive"
    if proposal.action == ProposalAction.BUY:
        ask = _try_decimal(context.best_ask)
        if ask is None or ask <= 0:
            return f"book reference missing: best_ask={context.best_ask!r}"
        if entry > ask:
            return f"BUY entry_price={entry} > best_ask={ask}; would cross the book"
        return None
    if proposal.action == ProposalAction.SELL:
        bid = _try_decimal(context.best_bid)
        if bid is None or bid <= 0:
            return f"book reference missing: best_bid={context.best_bid!r}"
        if entry < bid:
            return f"SELL entry_price={entry} < best_bid={bid}; would cross the book"
        return None
    return f"action={proposal.action.value} has no book side"


def _entry_price(proposal: TradeProposal) -> Any | None:
    return (
        proposal.entry_conditions.get("entry_price")
        or proposal.entry_conditions.get("price")
        or proposal.sizing.get("entry_price")
        or proposal.sizing.get("price")
    )
