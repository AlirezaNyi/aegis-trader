# Backtest pre-registration — rules-v2 "daily regime + intraday pullback" (2026-10-15)

Written **before** rules-v2 exists or any v2 result is computed.

## Why v2 (evidence that motivated it)
* rules-v1 (buy intraday trend continuation) failed out of sample on 5m (−0.18R,
  327 trades), 1h (−0.13R, 926 trades; holdout −0.15R) — see the walk-forward reports.
* Literature: crypto time-series momentum is strongest at **daily to multi-week**
  horizons; **hourly returns tend to mean-revert** (negative autocorrelation at 1–4h).
  v2 therefore takes the trend from the daily timeframe and enters on intraday
  pullbacks instead of intraday breakouts, and adds a time stop because mean-reversion
  edges decay quickly.

## Strategy (long-only, Risk Engine unchanged)
Enter BUY when all hold:
1. Daily regime up: last **completed** daily close > daily SMA(`regime_n`) (no look-ahead).
2. Intraday pullback: RSI(14) on the trading timeframe ≤ `rsi_entry_max`.
3. Market context present (ATR available).
Levels: stop = entry − `k_atr_stop`×ATR14; target = entry + `r_target`×(entry − stop).
Time stop: close at bar close after `max_hold_bars` bars if neither level was hit.

## Grid (5 params, 32 points per timeframe; not changed after results)
| Param | 15m | 1h |
| --- | --- | --- |
| regime_n (daily SMA) | {20, 50} | {20, 50} |
| rsi_entry_max | {30, 40} | {30, 40} |
| k_atr_stop | {1.5, 2.5} | {1.5, 2.5} |
| r_target | {1.5, 2.5} | {1.5, 2.5} |
| max_hold_bars | {16, 48} | {12, 36} |

## Data and split
* Binance archive research data (5 symbols), 15m and 1h trading bars, 1d regime bars.
* Walk-forward on 2021-01-01 .. 2025-09-01: train 12 months → test 3, step 3.
* **Holdout: 2025-09-01 .. 2026-10-13**, evaluated once. Disclosure: this period was
  previously viewed only through rules-v1 on 5m; v2 was not designed on it.
* Costs/risk identical to the v1 pre-registration (policy 1.0, fee 10 bps, slippage
  5 bps, assumed spread 5 bps, 100 USDT per window/symbol, min 30 train trades).

## "OK" bar (same as v1)
Stitched OOS pooled expectancy_r > 0, PSR(>0) ≥ 0.95, DSR (all trials) ≥ 0.95, and
holdout pooled expectancy_r > 0 with ≥ 30 trades.

## LLM / Jev
LLM and Jev cannot be validated on historical data without look-ahead bias (model
training data overlaps the test period). Their value is measured **forward only** in the
paper soak with `AEGIS_DECISION_SOURCE=rules+llm` (veto-only) versus `rules`, never by
backtest.
