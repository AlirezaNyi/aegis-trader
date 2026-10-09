# Aegis Phase 6 — Backtesting and Paper Trading

Implement historical backtesting and paper trading without live order submission.

Backtesting:

* Use reproducible historical data replay.
* Separate training, validation, and out-of-sample periods.
* Prevent look-ahead bias and leakage.
* Model fees, spread, slippage, latency, partial fills, and Futures funding where applicable.
* Version data sets, strategies, parameters, and experiment runs.
* Document assumptions and missing-data handling.

Report net return after modeled costs, maximum drawdown, profit factor, win rate, average win/loss, trade count, exposure, turnover, cost contribution, and results by instrument, timeframe, and market regime. Explain statistical uncertainty and sample-size limitations.

Paper Trading:

* Use the same validated analytical pipeline and Risk Engine as the intended live path.
* Simulate orders, fills, fees, positions, and balances.
* Keep simulated state separate from live account state.
* Persist all proposals, decisions, simulated fills, and position changes.
* Recover and reconcile simulated state after restart.
* Never send exchange orders.

Acceptance criteria:

* Replay is reproducible.
* Costs are applied consistently.
* Out-of-sample results remain separate.
* Paper positions and balances reconcile.
* Tests and static checks pass.
* Live mode remains disabled.

Do not claim profitability based on a single test. Produce an evaluation report and stop after this phase.
