# Backtest pre-registration — rules-v1 on daily bars + gross-vs-net diagnostic (2026-10-15)

Written before these runs. Prior results: rules-v1 negative OOS on 5m/15m/1h; rules-v2
negative OOS on 15m/1h. Round-trip cost (2×10 bps fee + 5 bps spread + 2×5 bps slippage
≈ 35 bps) is a large fraction of a 1.5–2.5 ATR stop at intraday horizons.

## Test A — daily trend continuation (hypothesis from literature: crypto time-series
momentum is strongest at daily-to-multi-week horizons, where costs are small vs. moves)
* rules-v1 on **1d** bars, 5 symbols, Binance archive, 100-candle window.
* Grid (24 points): k_atr_stop {1.5,2.5,3.5}, r_target {2,3}, atr_pct_min {0.5,2},
  volume_ratio_min {0,1.0}; rsi 45–75; atr_pct_max 20.
* Walk-forward 2021-01-01..2025-09-01, train 24 months → test 6, step 6;
  min 20 pooled train trades (daily trades are sparse).
* Holdout 2025-09-01..2026-10-01 (viewed before only through intraday strategies; disclosed).
* Same costs/policy as previous runs; same four "OK" criteria (holdout needs ≥ 30 trades).

## Test B — diagnostic only (not a candidate, no selection impact)
* rules-v2 on 1h re-run with **zero costs** (fee 0, slippage 0, spread 0), same grid and
  split as `BACKTEST_PREREGISTRATION_V2_2026-10-15.md`, to separate gross signal from
  cost drag. Results are reported as diagnostic and must not be used to pick params.
