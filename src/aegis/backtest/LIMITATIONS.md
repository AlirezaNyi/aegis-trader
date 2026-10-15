# Backtest limitations (Phase 6)

## Scope

* Deterministic candle replay with fixed seed, dataset/strategy versioning, and
  explicit train / validation / OOS time windows.
* Fills route through `PaperBroker` (`ledger_kind=paper`). No exchange order calls.
* `BacktestRunner` strategy hooks are test/simple callables — not the full pipeline.
* `replay_pipeline` (`pipeline_replay.py`, script `scripts/backtest_pipeline.py`) replays
  stored final candles through the full `run_paper_cycle` (features → analysts →
  evidence → Jev → Supervisor → Risk → PaperBroker). See "Full-pipeline replay" below.

## Cost model (simulation assumptions)

* Fees, slippage, and optional spread are engineering knobs for a run.
* They are **not** risk-policy values and are not claimed as live exchange fees.
* Latency, partial fills, and Futures funding are **not** applied in Phase 6.

## Look-ahead

* `CandleReplay` only exposes candles with `close_time <= as_of`.
* Accessing a future bar raises `LookAheadError`.

## Metrics honesty

* Reports include net return after costs, drawdown, profit factor, win rate, avg win/loss,
  trade count, exposure, turnover, and cost contribution, plus `r_trade_count`, `avg_r`,
  `expectancy_r` (R-defined trades only) and `sharpe_per_bar` / `sortino_per_bar`.
* Sharpe / Sortino use simple per-bar returns of the marked equity curve (starting from
  initial equity), risk-free rate 0, **not annualized**; Sharpe uses the sample stdev,
  Sortino the downside deviation with target 0. `None` when undefined (fewer than two
  bars or zero deviation).
* `BacktestRunner` (simple hooks) does not simulate protective exits.
* `disclaimer` and `uncertainty_notes` state that results are descriptive only.
* **Do not** claim profitability or edge from a single backtest.

## OOS separation

* Train, validation, and OOS use separate paper ledgers and metric accumulators.
* OOS metrics must not be mixed into train metrics.

## Live mode

* `BacktestRunner` refuses `trading_mode=live`.
* Live submit remains disabled until owner approval and Phase 7 adapter work.

## Full-pipeline replay (`replay_pipeline`)

* One `run_paper_cycle` per final bar, sorted by `open_time`; non-final bars are dropped;
  the trailing `window_candles` final bars with `close_time <= bar.close_time` are passed.
* `received_at` is rewritten to `close_time` and the cycle clock is `close_time + 1ms`,
  so freshness (RP-DATA-FRESHNESS-MS) passes for contiguous history. Gaps in stored
  candles still block validation (REJECT) — counted in `assumptions.counters`.
* **Spread:** historical order-book spread is not stored. `spread_bps` is a required
  argument; any value is a constant **assumption** recorded in `uncertainty_notes`.
  `None` = unobserved → Risk rejects every trade (RP-MAX-SPREAD / RP-MIN-LIQUIDITY).
* **LLM / Jev:** caller-provided ports. Default script uses `UnavailableLlmPort` and
  `UnavailableJevPort` → NO_TRADE every bar (zero trades). `--use-live-llm` uses the
  owner-configured provider wrapped in `CappedLlmPort` (whole-run cap, required
  `--max-llm-calls`). Jev is always Unavailable in the script (no historical Jev).
  The owner `DailyCallLimiter` resets per *historical* UTC day, hence the extra cap.
  Live LLM outputs are non-deterministic; results are not reproducible.
* **Fill model:** PaperBroker immediate full fill at the decision bar's close ±
  configured slippage; no queue position, latency, partial fills, or next-bar open.
* **Exits:** before each cycle, `paper.exits.apply_exits` simulates the Risk-approved
  stop-loss / take-profit on the visible final bars after entry (gap → open, stop first
  when both are in one bar, stop slippage only). It is idempotent, so a cycle that also
  calls it is a no-op. Supervisor/Risk SELLs are counted as `closed_signal`. Open
  positions at window end are marked at last close (not force-closed).
* **Trade PnL / R:** taken from the ledger closed-trade log, net of entry **and** exit
  fees (the simple `BacktestRunner` still uses gross-minus-exit-fee). R-multiple =
  `pnl / ((entry - stop) * qty)`; `avg_r` = mean R; `expectancy_r` =
  `sum(pnl) / sum(initial risk)`. Counters: `exit_fills`, `closed_trades`,
  `closed_stop_loss`, `closed_take_profit`, `closed_time_stop`, `closed_signal`.
* **Time stop (rules-v2):** `max_hold_bars` closes at the N-th post-entry bar close
  (bar resolution, after the level checks) with slippage and fee; see
  `paper/LIMITATIONS.md`.
* **Higher timeframe (rules-v2):** `htf_candles` (daily bars) are indexed once
  (`backtest/htf.py::HtfIndex`); each bar's cycle receives the trailing
  `HTF_CONTEXT_BARS` (210) daily bars with `close_time <= bar.close_time`. A daily bar
  is visible to an intraday bar whose close equals the daily close (both complete at the
  same instant). Archive daily bars are Binance's UTC day; Toobit's daily boundary is not
  verified to match.
* Per-window isolation: train / validation / OOS each get their own `PaperBroker`,
  `PaperLedger`, `PaperEquityTracker`, and `ProposalHistory`.
* Refuses `trading_mode=live` (and any non paper/development mode).
* `decision_source` / `rule_params` pass through to `PaperCycleDeps`; `rules` needs no LLM
  or Jev. `data_source` (e.g. `binance-archive`) is recorded in assumptions when candles
  come from a research archive; such candles keep their own exchange label on the
  instrument (Risk's allowlist is by symbol), so archive prices are **not** Toobit prices.
* `replay_windows` is the multi-window core (non-overlapping named windows, isolated
  ledgers per window); `replay_pipeline` is the train/validation/OOS wrapper around it.

## Shared-evidence multi-parameter replay (`multi_replay.py`)

* Rules mode only. Validation, features, analysts, evidence package and Jev are computed
  once per bar and shared by every grid point; the parameter-dependent tail (rule
  decision → Risk → PaperBroker) runs per point with its own ledger, equity tracker and
  proposal history.
* It mirrors `pipeline.cycle._run_paper_cycle_body` step by step (same functions, same
  order). Equivalence with `run_paper_cycle` is asserted by
  `tests/test_backtest_walk_forward.py::test_multi_replay_equals_run_paper_cycle_per_param`
  (and was checked on 3,000 real ADAUSDT bars × 4 points).
  rules-v2 with daily context and the time stop is covered by
  `tests/test_backtest_v2.py::test_multi_replay_v2_equals_run_paper_cycle_per_param`. **If the cycle body changes,
  update this module and re-run that test.**
* Jev is evaluated once per bar and shared: only valid for deterministic Jev ports
  (the tuner always uses `UnavailableJevPort`).
* Analyst timeouts: backtest paths (`replay_windows`, `replay_rules_multi`, hence the
  walk-forward) run analysts with `backtest_settings`, i.e. a per-call timeout of 3600 s
  (`BACKTEST_ANALYST_TIMEOUT_SECONDS`) instead of `analyst_timeout_seconds`. Reason:
  rules-v1 runs under 6-worker load logged a few `analyst_timeout` events, making
  evidence depend on CPU load. The paper soak / live-shaped cycle keeps the configured
  timeout. A genuinely hung analyst would now stall a backtest instead of degrading.
* Parameter sets may be rules-v1 or rules-v2 but must not be mixed in one call.

## Walk-forward tuning (`walk_forward.py`, script `scripts/walk_forward.py`)

* Rolling folds train N → test M, step S (months by default; `days` for smoke tests) plus
  a final holdout of the last H periods. Selection reads **train outcomes only**:
  pooled (across symbols) `expectancy_r` with `>= --min-trades` R-trades (required
  argument, no default), tie-break lower worst-symbol max drawdown, then param hash.
* Every grid point is also replayed on each fold's test window as an overfitting
  diagnostic (`test_rank_pct_among_grid`, a CSCV/PBO-style indicator, not full CSCV).
  These results never feed selection.
* Stitched OOS = each fold's test window with that fold's selected params; folds with no
  eligible point stay flat. The holdout is evaluated **once**, after all selections,
  with the most recent fold's params. Do not tune on holdout results.
* Every window starts from a fresh paper ledger; open positions at a window end are
  marked, not closed; trades never span windows. Up to `window_candles` bars before a
  window start are visible as past context (not look-ahead).
* Deflated Sharpe Ratio (Bailey & López de Prado 2014) uses per-trade R Sharpe of the
  pooled stitched OOS trades, `V` = variance of per-trade R Sharpe across train trials,
  `N` = grid size × folds (conservative; trials are correlated). A `N = grid size`
  variant is also reported. Statistics use `float` (not money).
* Normal-approximation CIs ignore autocorrelation and fat tails.
* Risk-policy period locks (e.g. owner v1.0 absolute drawdown / daily loss) stop new
  entries inside a window once hit, truncating samples; see `candidate_rejects`.
* Persistence: one `backtest_runs` row per (symbol, window, grid point) and one
  `strategy_experiments` row per run; fail-open (error class only). The JSON report is
  always written.
* `RiskPolicy` is not picklable, so process-pool workers rebuild it from `Settings`
  (`risk_policy_version`; policy 1.1 needs explicit CLI percentages).

* `BacktestRunner.run` (strategy-hook runner) does not catch blocked paper fills
  (`PaperFillError` / `PaperTradingBlocked`, e.g. futures or intent-binding mismatch);
  such a fill aborts the run (fail closed). The pipeline replay and walk-forward paths
  catch them and count `fill_blocked`.
