# Backtest limitations (Phase 6)

## Scope

* Deterministic candle replay with fixed seed, dataset/strategy versioning, and
  explicit train / validation / OOS time windows.
* Fills route through `PaperBroker` (`ledger_kind=paper`). No exchange order calls.
* Strategy hooks are test/simple callables — not the full analyst → supervisor → risk pipeline.

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
