# Paper trading limitations (Phase 6)

## Simulation assumptions (not risk policy)

* `paper_fee_bps` and `paper_slippage_bps` are **engineering defaults for simulation only**.
* They are **not** owner-approved risk-policy values and must not be read as live fee schedules.
* Default fill model: immediate **full** fill at limit price (when set) or `mid ± slippage_bps`.
* Partial fills, latency, queue position, and Futures funding are **not** modeled in Phase 6.

## Ledger isolation

* PaperBroker writes only `ledger_kind=paper`.
* LIVE ledger rows are refused; Phase 6 never writes live account state.
* Paper balances/positions are independent of any exchange account.

## No exchange connectivity

* PaperBroker must **never** import or call Toobit private trade APIs or construct live submit clients.
* Live submit remains Phase 7+ and gated by `trading_mode` + `live_armed` + `kill_switch`.

## Risk handoff

* Production path: `submit_from_risk_decision` requires non-expired Risk **APPROVE** and matching `proposal_id` / correlation IDs (INV-01).
* Fill application is private (`_apply_approved_intent`); there is no public bypass around Risk.

## Restart recovery

* In-memory snapshot + checksum equality reconcile balances/positions (`to_snapshot` also
  carries protective levels).
* `PaperStateStore` (`state_store.py`) persists the paper ledger to `orders` / `fills` /
  `positions` / `paper_balances` (`ledger_kind='paper'`) **and** the equity tracker +
  protective levels + ledger checksum to `risk_period_state.state` (JSONB, migration
  `20261014_0008`) in **one transaction**.
* Protective levels live in that JSONB (not as columns on `positions`), so they are written
  atomically with the tracker and validated against open positions on load.
* Fail closed: if persistence is enabled and the load fails, the stored payload is
  malformed, the ledger-row checksum does not match the stored checksum, or paper ledger
  rows exist without a stored state, the tracker is marked `state_lost` → all
  equity-derived losses are `None` and `cooldown_active=True` → Risk rejects new trades.
  It never silently starts fresh and refuses to overwrite the stored state until a later
  `restore()` succeeds. Recovery from inconsistent stored state is a manual owner/operator
  action (inspect / repair or clear the paper rows); no automatic reset exists.
* A failed `save()` marks the tracker `state_persist_failed` (losses `None`) until the next
  successful save. Internal tracking continues meanwhile.
* `database_url=None` = in-memory mode (tests, backtests): restore/save are no-ops.
* The stored balance wins over `AEGIS_PAPER_INITIAL_BALANCE` once state exists.
* `save()` re-upserts every paper order/fill row (O(fills) queries per changed save;
  unchanged state is skipped by signature). Fine for paper soak volumes, not tuned for
  very large ledgers.
* Reconcile is a simple checksum / equality check — not exchange-authoritative live reconcile.
* `closed_trades` (realized PnL / R log) is in-process only and is **not** persisted;
  journal `ExitFill` results elsewhere if they must survive restarts.

## Equity tracking and loss-limit locks (paper soak)

* `PaperEquityTracker` derives daily / weekly (ISO week, Monday 00:00 UTC) / monthly
  (1st 00:00 UTC) loss as `max(0, period_start_equity - equity)` and drawdown as
  `max(0, peak - equity)` from marked equity.
* There is no exact boundary mark: a new period's start equity is the **last mark observed
  before the boundary** (first-ever observation: the current equity). Losses during
  downtime or before the first post-boundary mark are therefore attributed to the new
  period (conservative; may over-count after long downtime, never drops a loss). Rollover
  is forward-only (a clock moving backwards keeps the stored period).
* `loss > limit` (strict) sets a lock until the start of the next period of that type; the
  lock persists when equity recovers and across restarts (when persisted). Snapshot
  reports `period_lock_until` (latest active lock) and `locked_periods`. A daily breach
  also starts the owner-approved 24h `RP-COOLDOWN-AFTER-LOSS` cooldown.
* Limits are passed in by the caller (absolute USDT, from the approved policy). No limit
  values are defined here; `None` limits are not enforced by the tracker (Risk rejects
  unapproved params itself).
* Marks are cycle-provided closes; this is simulation MTM, not exchange mark price.

## Protective exits (`exits.py`)

* BUY fills from a Risk APPROVE record `stop_loss` / `take_profit` from
  `validated_order_params`, plus average entry, quantity, entry fees and fill time.
  Malformed (non-numeric / non-positive) levels fail closed: no fill.
* Adding to an open position: newly approved non-`None` levels replace the old ones; a
  `None` level keeps the prior one (a stop is never silently removed). R for pyramided
  positions uses the average entry and the latest stop and can be undefined (`None`) when
  that stop is not below the average entry.
* `apply_exits` scans final candles with `close_time > entry fill time` (the entry bar is
  never scanned; the next bar is, even when its `open_time` equals the replay fill clock),
  `close_time <= now`, and newer than the last checked bar. Rules per bar: open ≤ stop →
  fill at open (gap); low ≤ stop → fill at stop; high ≥ target → fill at target; both in
  one bar → stop first. Bar resolution only: intrabar path unknown. In paper soak the bar
  containing the fill may include a few seconds of pre-fill price action.
* Stop fills apply `slippage_bps` against the seller (rounded down); target fills get none
  and a target gapped through still fills at the target (conservative). No spread or
  liquidity model on exits; full quantity closed; long-only (spot).
* Exits are reduce-only simulation and do **not** go through Risk. They are refused in live
  mode and allowed under the kill switch (the simulated stop is modeled as resting since
  entry). Live protective exits are out of scope and need an owner ADR (ADR 0006).
* Time stop (rules-v2): a BUY whose approved `validated_order_params.entry_conditions`
  carries `max_hold_bars` records it with the levels (persisted in snapshots / state
  store; older payloads load without it). Each eligible final bar scanned increments
  `bars_held`; after the stop/target checks on a bar (they keep priority), if
  `bars_held >= max_hold_bars` the position closes at that bar's **close** minus
  `slippage_bps`, plus `fee_bps` (exit reason `time_stop`, MARKET). Malformed values
  (bool, float, ≤ 0, non-integer) fail closed: no fill. Adding to a position restarts the
  count at the new fill and keeps the prior `max_hold_bars` when the new approval has none.
  Simulation only; there is no live time-stop path.
* Exit orders are tagged by `client_order_id` prefix `paper-exit-stop_loss-` /
  `paper-exit-take_profit-` / `paper-exit-time_stop-` (the `Order` schema has no reason
  field).
* `realized_pnl` is net of entry and exit fees; `R = realized_pnl / ((entry - stop) * qty)`
  (price risk only; costs are in the numerator).

## Order side

* Paper broker, fills, ledger, exits and backtest compare `OrderSide` enum members
  (`schemas.orders.OrderSide`); unknown sides are refused at the `OrderIntent`
  schema boundary. Persisted rows keep the `"BUY"` / `"SELL"` strings.
