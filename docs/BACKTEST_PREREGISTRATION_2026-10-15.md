# Backtest pre-registration — 15m and 1h rule strategy (2026-10-15)

Written **before** any 15m/1h result was computed. Changing any item below after
seeing results turns the holdout into in-sample data and must be reported as such.

## Hypothesis
The long-only trend rule (`aegis.strategy.rules`, `rules-v1`) loses to costs on 5m
(−0.18R OOS, `docs/WALK_FORWARD_REPORT_2026-10-15.md`). On 15m and 1h bars the move
per trade is larger relative to the fixed round-trip cost, so net expectancy may
turn positive.

## Data
* Binance public spot archive (research proxy for Toobit; see `docs/API_CONTRACTS.md` §3A),
  ADAUSDT, BTCUSDT, BNBUSDT, TRXUSDT, ETHUSDT.
* Range used: 2021-01-01 .. 2025-09-01 (exclusive).
* **Fresh holdout: 2024-09-01 .. 2025-09-01 (12 months), never viewed before.**
  (2025-09 onward was already viewed in the 5m run and is excluded from selection
  and from the holdout. It is reported separately as a *previously viewed* forward
  check only.)

## Walk-forward
* Train 12 months → test 3 months, step 3 months, selection on train only.
* Objective: pooled expectancy in R across symbols, min 30 R-trades per train window,
  tie-break lower worst-symbol drawdown, then param hash.
* Holdout evaluated once with the most recent fold's params.

## Costs and risk
Policy 1.0 snapshot (fixed 2 USDT, LIMIT/GTC, long-only, 5% max stop), fee 10 bps per
side, slippage 5 bps, assumed constant spread 5 bps, initial balance 100 USDT per
window and symbol, 100-candle window.

## Grids (declared here, not changed after results)
* 15m: `data/walk_forward/grid_15m_2026-10-15.json` — k_atr_stop {2,3,4,6}, r_target
  {1.5,2.5}, atr_pct_min {0.1,0.3}, volume_ratio_min {1.0,1.5}; rsi 45–70; atr_pct_max 5.
* 1h: `data/walk_forward/grid_1h_2026-10-15.json` — k_atr_stop {1.5,2,3,4}, r_target
  {1.5,2.5}, atr_pct_min {0.2,0.5}, volume_ratio_min {1.0,1.5}; rsi 45–70; atr_pct_max 8.
* 32 points each.

## What counts as "OK" (research bar, not owner promotion criteria)
All of:
1. Stitched OOS pooled expectancy_r > 0.
2. Probabilistic Sharpe vs 0 (PSR) ≥ 0.95 on stitched OOS per-trade R.
3. Deflated Sharpe (N = all trials) ≥ 0.95.
4. Holdout pooled expectancy_r > 0 with ≥ 30 trades.
Anything else is reported as "no demonstrated edge". Owner SE-* thresholds remain
UNAPPROVED and are separate.
