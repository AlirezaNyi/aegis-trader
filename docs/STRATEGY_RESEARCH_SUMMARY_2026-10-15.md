# Strategy research summary — 2026-10-15

All runs: Binance public archive (research proxy for Toobit), 5 symbols (ADA, BTC, BNB,
TRX, ETH USDT), long-only, policy 1.0 sizing, fee 10 bps/side, slippage 5 bps, assumed
spread 5 bps (~35 bps round trip), walk-forward with selection on train only, stitched
out-of-sample (OOS) reporting, one-shot holdout, Deflated Sharpe Ratio (DSR) over all
trials. Pre-registrations: `BACKTEST_PREREGISTRATION_2026-10-15.md`,
`BACKTEST_PREREGISTRATION_V2_2026-10-15.md`, `BACKTEST_PREREGISTRATION_D1_2026-10-15.md`.
Raw reports: `data/walk_forward/` (gitignored). No result below is a profitability claim.

## Results

| Strategy | TF | OOS trades | OOS expectancy (R) | PSR(>0) | DSR | Holdout (trades, R) | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rules-v1 trend | 5m | 327 | −0.18 | 0.04 | 0.00 | spent earlier | no edge |
| rules-v1 trend | 15m | 1,228 | −0.10 | 0.00 | 0.00 | 402, +0.09 | no edge |
| rules-v1 trend | 1h | 926 | −0.13 | 0.67 | 0.00 | 231, −0.15 | no edge |
| rules-v2 daily regime + pullback | 15m | 4,448 | −0.16 | 0.00 | 0.00 | 1,147, −0.32 | no edge |
| rules-v2 daily regime + pullback | 1h | 1,241 | −0.17 | 0.00 | 0.00 | 503, −0.21 | no edge |
| rules-v2 1h, **zero costs** (diagnostic) | 1h | 1,798 | −0.05 | 0.26 | 0.00 | 503, −0.05 | no gross edge |
| rules-v1 trend | **1d** | 41 | **+0.50** | **0.98** | 0.00 | 11, +0.34 | promising, not proven |

## What the evidence says
1. **Intraday (5m–1h) rule signals have no gross edge** on this data: even with zero
   costs v2 is ≈ −0.05R. Costs then add roughly −0.1R per trade. Further intraday
   parameter tuning on these features is unlikely to help and inflates multiple-testing
   risk (DSR already ≈ 0 because of the trial count).
2. **Daily-horizon trend is the only positive result**, consistent with the literature
   (crypto time-series momentum is strongest at daily-to-multi-week horizons; hourly
   returns tend to mean-revert). But 41 OOS trades and 11 holdout trades are far too few:
   it fails the pre-registered DSR and holdout-size criteria. Most of the gain came from
   TRX; ADA produced no trades.
3. **Costs dominate intraday**: ~35 bps round trip is ~0.3–0.6R at 15m/1h stop sizes.

## Recommended next steps (best practice)
1. **Stop intraday tuning; focus research on daily/multi-day trend** with *more data, not
   more parameters*: extend history (archive back to 2018–2019), widen the universe to
   ~20 liquid USDT spot pairs, and fix most parameters a priori (e.g. classic daily trend
   rules) so the trial count stays small. Pre-register before running.
2. **Reduce costs**: post-only/maker limit entries (Toobit maker < taker), check the real
   VIP fee at toobit.com/vip and set `AEGIS_TRADING_FEE_BPS`; measure live spread in soak.
3. **Forward validation**: a daily strategy needs a verified Toobit `1d` kline source
   before paper soak can run it (currently NO_TRADE by design). Then soak for months —
   daily strategies trade rarely, so the owner's SE-* thresholds must account for that.
4. **LLM / Jev**: they cannot be backtested honestly (training-data look-ahead). Evaluate
   them only forward, as a veto layer: run paper soak A/B with `AEGIS_DECISION_SOURCE=rules`
   vs `rules+llm` on the same candidate stream, pre-register the comparison metric, keep
   the LLM veto-only, and cap calls with `AEGIS_LLM_DAILY_CALL_CAP`.
5. **Overfitting control**: every trial is logged in `backtest_runs`/`strategy_experiments`;
   the holdouts used today are spent for these hypotheses. New ideas need new
   pre-registration and, ideally, untouched data.
