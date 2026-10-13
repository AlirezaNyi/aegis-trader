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
  trade count, exposure, turnover, and cost contribution.
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
* **Exits:** whatever the Supervisor/Risk path produces; there is no stop/take-profit
  simulation inside bars. Open positions at window end are marked at last close.
* Per-window isolation: train / validation / OOS each get their own `PaperBroker`,
  `PaperLedger`, `PaperEquityTracker`, and `ProposalHistory`.
* Refuses `trading_mode=live` (and any non paper/development mode).
