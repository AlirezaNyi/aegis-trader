# Walk-forward report: deterministic rule strategy (2026-10-15)

**Bottom line: no edge was found.** The stitched out-of-sample (OOS) expectancy is
negative: **-0.18 R per trade over 327 trades** (95% normal CI of mean R
[-0.31, +0.01]). The Deflated Sharpe Ratio is about 0 (4e-21) with N = 192 trials and
also about 0 with N = 24. The holdout looks positive (+0.33 R over 45 trades), but it
has too few trades to mean anything (CI [-0.37, +0.86]). It also contradicts the
stitched OOS result, and BTC contributes only 2 trades. None of this is evidence of
profitability. Do not use these params for live trading. Do not re-tune on the holdout.

## What was run

- Harness: `src/aegis/backtest/walk_forward.py` (protocol, statistics),
  `src/aegis/backtest/multi_replay.py` (shared-evidence replay of a parameter grid),
  and the CLI `scripts/walk_forward.py`.
- Command (JSON report and best-params file are in `data/walk_forward/`, which is
  gitignored):

```bash
.venv/bin/python scripts/walk_forward.py --symbol ADAUSDT --symbol BTCUSDT \
  --exchange binance --data-source binance-archive \
  --grid data/walk_forward/grid_2026-10-15.json --spread-bps 5 --initial-balance 100 \
  --min-trades 30 --train-months 3 --test-months 1 --step-months 1 --holdout-months 2 \
  --workers 6 --out data/walk_forward/report_2026-10-15.json \
  --md-out data/walk_forward/report_2026-10-15.md
```

- Run id `e188188e-f1d3-468d-8c6d-db334bb31e61`. Runtime: 877 s with 6 worker processes. 771 rows were persisted: 768 trial rows in
  `backtest_runs` (24 points x 8 folds x 2 symbols x {train, test}), 2 holdout rows,
  and 1 `strategy_experiments` row.
- Risk policy: owner **1.0** (fixed 2 USDT notional, LIMIT/GTC, long-only, 5% max
  stop, absolute daily-loss and drawdown limits). Fee is 10 bps and slippage is 5 bps,
  both from the current Settings. Spread is an **assumed** constant 5 bps.
- Decision source is `rules`. Jev is Unavailable and no LLM is used.

### Grid (24 points, fixed before any fold result was seen)

```json
{
  "k_atr_stop": ["2", "4", "6"],
  "r_target": ["1.5", "3"],
  "rsi_min": "45",
  "rsi_max": "70",
  "atr_pct_min": ["0.05", "0.2"],
  "atr_pct_max": "3",
  "volume_ratio_min": ["1.0", "1.5"]
}
```

Why these values: a round trip costs about 25-30 bps (2 x 10 bps fee + stop
slippage). A 5m ATR is about 0.1-0.5% of price. So the grid tries wider stops
(`k_atr_stop`) and a volatility floor (`atr_pct_min`) to reduce the cost per unit of
risk. The only data looked at before the run was a quick timing benchmark on
ADAUSDT, September 2025 (inside fold 0 train).

## Interpretation and caveats

- **Selection sits on the edge of the grid.** Every fold picked `k_atr_stop=6` (the
  widest stop) and `atr_pct_min=0.2` (the higher floor). This suggests costs dominate
  at 5m: the wider the stop, the smaller the fee drag in R. The true optimum may lie
  outside the grid. If so, it is a property of the cost structure, not a signal edge.
- **Train expectancy was negative for 6 of 8 folds, even for the selected point.** The
  selector picks the least bad point. With these rules and costs, no point is
  reliably positive in-sample.
- **Stability:** 3 distinct points were selected, and the modal point was chosen 4/8
  times. The median OOS rank of the selected point among the grid is 0.80, and 1/8
  folds fell below the median. Ranking the grid this way only shows that cost-light
  configs lose less. It does not show positive expectancy.
- **Policy effects:** most rejected rule candidates were blocked by
  RP-MAX-NOTIONAL-PER-INSTRUMENT (already 2 x 2 USDT in the symbol). ADA fold 2 train
  also hit RP-DRAWDOWN-LIMIT (137 rejects), which truncated that sample. One closed
  trade can cover two stacked entries, because exits close the full position.
- **Data:** the candles are Binance spot archive data (`exchange=binance`), not
  Toobit. No validation gaps were blocked (`validation_blocked=0`). Spread is assumed,
  and fills happen at bar close. Exits are resolved at bar resolution, with the stop
  taken first when a bar touches both stop and target.
- **Statistics:** the normal CIs ignore autocorrelation and fat tails. The DSR
  assumptions are in `report.dsr.assumptions`. In short: SR is the per-trade R
  Sharpe; V is computed across train trials; N = grid x folds is conservative.
- The holdout is now **spent**. Any future grid or objective change needs a fresh
  holdout, either new data after 2026-10-13 or a re-declared split.

## Benchmarks (single process, ADAUSDT 5m, owner policy 1.0, spread 5 bps)

| Path | Bars | Param sets | Seconds | Throughput |
| --- | --- | --- | --- | --- |
| `replay_windows` -> `run_paper_cycle` (rules) | 10,000 | 1 | 12.7 | 786 bars/s |
| `replay_rules_multi` (shared evidence) | 10,000 | 24 | 45.2 | 5,307 bar-param evals/s (~6.8x) |

`replay_rules_multi` produced identical metrics and counters to the `run_paper_cycle`
path on 3,000 real ADAUSDT bars x 4 points. A unit test asserts the same thing on a
synthetic fixture.

## Generated tables

| Setting | Value |
| --- | --- |
| unit | months |
| train | 3 |
| test | 1 |
| step | 1 |
| holdout | 2 |
| min_trades | 30 |
| spread_bps | 5 |
| initial_balance | 100 |
| fee_bps | 10 |
| slippage_bps | 5 |
| risk_policy_version | 1.0 |
| timeframe | 5m |
| window_candles | 100 |
| grid size | 24 |
| total selection trials (grid x folds) | 192 |

### Data

| Symbol | Source | Bars | First open | Last close | Data hash (16) |
| --- | --- | --- | --- | --- | --- |
| ADAUSDT | binance-archive (binance) | 117504 | 2025-09-01T00:00:00+00:00 | 2026-10-13T23:59:59.999999+00:00 | `d4e6d325f0d84305` |
| BTCUSDT | binance-archive (binance) | 117504 | 2025-09-01T00:00:00+00:00 | 2026-10-13T23:59:59.999999+00:00 | `2694eeff01a99cdd` |

### Folds (selection on train only)

| Fold | Train | Test | Eligible | Selected hash | Train expR (n) | Test expR (n) | Test rank pct |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2025-09-01..2025-11-30 | 2025-12-01..2025-12-31 | 24 | `623a00f77e` | -0.2941 (121) | -0.2188 (48) | 1.00 |
| 1 | 2025-10-01..2025-12-31 | 2026-01-01..2026-01-31 | 24 | `623a00f77e` | -0.3057 (138) | -0.3789 (43) | 0.70 |
| 2 | 2025-11-01..2026-01-31 | 2026-02-01..2026-02-28 | 24 | `cf1c7196a4` | -0.3193 (138) | -0.1175 (47) | 0.91 |
| 3 | 2025-12-01..2026-02-28 | 2026-03-01..2026-03-31 | 24 | `cf1c7196a4` | -0.1996 (141) | -0.1205 (60) | 0.70 |
| 4 | 2026-01-01..2026-03-31 | 2026-04-01..2026-04-30 | 24 | `623a00f77e` | -0.1035 (132) | 0.0008 (44) | 0.61 |
| 5 | 2026-02-01..2026-04-30 | 2026-05-01..2026-05-31 | 24 | `623a00f77e` | 0.0122 (133) | -0.1415 (19) | 0.91 |
| 6 | 2026-03-01..2026-05-31 | 2026-06-01..2026-06-30 | 24 | `656cdd7cdb` | 0.0550 (74) | -0.6937 (36) | 0.35 |
| 7 | 2026-04-01..2026-06-30 | 2026-07-01..2026-07-31 | 24 | `656cdd7cdb` | -0.1552 (80) | 0.2602 (30) | 1.00 |

### Selected params per fold

| Fold | k_atr_stop | r_target | rsi_min | rsi_max | atr_pct_min | atr_pct_max | volume_ratio_min |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 6 | 1.5 | 45 | 70 | 0.2 | 3 | 1.5 |
| 1 | 6 | 1.5 | 45 | 70 | 0.2 | 3 | 1.5 |
| 2 | 6 | 1.5 | 45 | 70 | 0.2 | 3 | 1 |
| 3 | 6 | 1.5 | 45 | 70 | 0.2 | 3 | 1 |
| 4 | 6 | 1.5 | 45 | 70 | 0.2 | 3 | 1.5 |
| 5 | 6 | 1.5 | 45 | 70 | 0.2 | 3 | 1.5 |
| 6 | 6 | 3 | 45 | 70 | 0.2 | 3 | 1.5 |
| 7 | 6 | 3 | 45 | 70 | 0.2 | 3 | 1.5 |

### Stitched OOS (fold test windows, selected params)

| Scope | R-trades | expectancy_r | avg_r 95% CI (normal) | win rate | net PnL | compounded return | max DD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ADAUSDT | 211 | -0.2247 | [-0.374, 0.009] | 0.332 | -3.4091 | -0.0321 | 0.0383 |
| BTCUSDT | 116 | -0.0844 | [-0.381, 0.183] | 0.362 | -0.5801 | -0.0056 | 0.0118 |
| pooled | 327 | -0.1809 | [-0.312, 0.006] | - | -3.9892 | - | - |

### Deflated Sharpe Ratio

| Item | Value |
| --- | --- |
| sr_oos_per_trade | -0.1043 |
| t_obs | 327.0000 |
| skew | 1.1179 |
| kurtosis | 3.4291 |
| sr_variance_across_trials | 0.0261 |
| n_trials_total | 192.0000 |
| sr0_expected_max | 0.4448 |
| dsr | 0.0000 |
| n_trials_grid | 24.0000 |
| sr0_expected_max_n_grid | 0.3200 |
| dsr_n_grid | 0.0000 |
| psr_vs_zero | 0.0378 |

### Stability

- folds with a selection: 8/8; distinct selected points: 3; modal point chosen 4 times.
- test rank pct median: 0.80; share of folds below test median: 0.12.

### Holdout (evaluated once)

Window 2026-08-14T00:00:00+00:00 .. 2026-10-13T23:59:59.999999+00:00; params from fold 7.

| Symbol | R-trades | expectancy_r | win rate | net return | max DD |
| --- | --- | --- | --- | --- | --- |
| ADAUSDT | 43 | 0.2185 | 0.279 | 0.0072 | 0.0102 |
| BTCUSDT | 2 | 2.9634 | 1.000 | 0.0068 | 0.0036 |
| pooled | 45 | 0.3265 (avg R CI [-0.365, 0.864]) | - | - | - |

> HOLDOUT evaluated once with the most recent fold's params. Do NOT use these holdout results to change the grid, the objective or the params; doing so turns the holdout into in-sample data.
