# Aegis — Feature Engine (`features-v1`)

**Status:** Phase 3  
**Schema version:** `features-v1`  
**Related:** [DATA_MODEL.md](DATA_MODEL.md), [SRS.md](SRS.md) SRS-AN-001

## Input rules

* Candles are sorted by `open_time` ascending.
* Default: only candles with `is_final=True` and `close_time <= as_of`.
* Open (intrabar) candles are excluded unless `include_intrabar=True` is set explicitly; the flag is recorded on the snapshot.
* Candles with `close_time > as_of` are never used (look-ahead guard).
* Gaps inside a feature’s warm-up window are **not** interpolated. Affected features return `status=unavailable` with reason `gap in warm-up window`.
* Insufficient history returns `status=insufficient_sample` with `value=null` (never a fabricated zero).

All numeric values use `Decimal`.

## Feature catalog

| Name | Formula | Warm-up (candles) |
| --- | --- | --- |
| `ret_1` | `(close_t - close_{t-1}) / close_{t-1}` | 2 |
| `sma_20` | Arithmetic mean of last 20 closes | 20 |
| `ema_20` | EMA span 20; seed = SMA of first 20 closes in the series used; `α = 2/(span+1)` | 20 |
| `rsi_14` | Wilder RSI (period 14) | 15 |
| `atr_14` | Wilder ATR (period 14); TR = max(H−L, \|H−prevC\|, \|L−prevC\|) | 15 |
| `realized_vol_20` | Sample standard deviation of last 20 simple returns | 21 |
| `volume_sma_20` | Arithmetic mean of last 20 volumes | 20 |
| `volume_ratio` | `volume_t / volume_sma_20` | 20 |

If an internal helper cannot produce a value after warm-up has passed (defensive path; not reachable for well-formed input), the feature is `insufficient_sample` with a reason — never an exception or a silent `ok`. No `assert` statements are used, so behaviour is identical under `python -O`.

## Snapshot fields

`schema_version`, `snapshot_id`, `instrument`, `market_type`, `timeframe`, `as_of`, `include_intrabar`, `candle_count`, `gap_detected`, `features` (map of name → `{value, status, reason}`), `created_at`.

## Evidence `market_context` (optional)

`EvidencePackage.market_context` (built by `aegis.evidence.build_market_context`, or `build_evidence_package(..., candles=...)`) gives descriptive price levels:

* Uses only `is_final` candles matching the snapshot instrument/timeframe with `close_time <= snapshot.as_of` (no look-ahead, no intrabar). `None` when no such candle exists.
* `last_close`, `recent_high` (max high), `recent_low` (min low) over the last `lookback_candles` (default lookback 20; fewer if history is short).
* `atr_14` copied from the snapshot only when its status is `ok` (absolute price units); `atr_pct_of_close = atr_14 / last_close * 100`, quantized to 4 dp (percent, `0.42` = 0.42%).
* `spread_bps` is caller-supplied; `spread_status` is `ok` when provided, else `unavailable`. The builder never derives spread from candles.
* `as_of` is the `close_time` of the last eligible candle.

## Technical analyst raw values

Alongside its descriptive labels, the technical analyst payload carries the raw snapshot values as Decimal strings: `rsi_14`, `ema_20`, `sma_20` (always present when the evidence is `ok`) and `volume_ratio` (`null` when that feature is not `ok`). Labels and their keys are unchanged.

## Deterministic rule strategy (`aegis.strategy`, `rules-v1`)

`aegis.strategy.decide(package, params)` maps an `EvidencePackage` to a `SupervisorDecision` without an LLM, so backtests can replay the pipeline and parameters can be tuned (e.g. walk-forward). It is a pure function: no I/O, no clock, no randomness, `Decimal` only under a fixed local decimal context (prec 28, half-even). It reads only the package (no look-ahead beyond what the package builder already enforces).

**Not trade authority.** The output is a candidate decision; proposal assembly and the deterministic Risk Engine still decide APPROVE/REJECT and sizing. Nothing here is a profitability claim, and `RuleParams` values are caller-supplied research inputs, not owner-approved risk-policy values.

`RuleParams` (frozen, all `Decimal`, no defaults):

| Field | Meaning | Validation |
| --- | --- | --- |
| `k_atr_stop` | Stop distance in multiples of `atr_14` | `> 0` |
| `r_target` | Take-profit distance as a multiple of stop distance | `> 0` |
| `rsi_min`, `rsi_max` | Inclusive band on technical `rsi_14` | within `[0, 100]`, `min < max` |
| `atr_pct_min`, `atr_pct_max` | Inclusive band on `market_context.atr_pct_of_close` (**percent**, `0.42` = 0.42%) | `min >= 0`, `min < max` |
| `volume_ratio_min` | Minimum technical `volume_ratio` | `>= 0` |

Non-finite values and non-`Decimal` types are rejected. `to_dict()` returns canonical strings (`1.50` and `1.5` map to `"1.5"`); `from_dict()` accepts only `str`/`Decimal`. `param_hash()` is SHA-256 over `{"strategy_version": "rules-v1", "params": to_dict()}` (sorted keys, compact JSON), so equal params hash equally and a logic-version bump changes every hash.

Long-only (policy v1 directions are long). BUY / `long` only when every condition holds, checked in this order:

1. `market_context` present, `as_of <= package.as_of`, `last_close > 0`, `atr_14 > 0`, `atr_pct_of_close` present.
2. Exactly one technical evidence, status `ok`, `trend == "up"`.
3. Exactly one quantitative evidence, status `ok`, `mean_simple_return > 0`.
4. `rsi_min <= rsi_14 <= rsi_max`.
5. `atr_pct_min <= atr_pct_of_close <= atr_pct_max`.
6. `volume_ratio >= volume_ratio_min`.

Prices: `entry = last_close`; `stop = entry - k_atr_stop * atr_14`; `take_profit = entry + r_target * (entry - stop)`. A non-positive stop yields NO_TRADE. Prices are not rounded to exchange tick size here.

Otherwise the result is `NO_TRADE` / `flat` with `rationale = "no_trade:<reason>"`, naming the **first** failed condition (`NoTradeReason`, e.g. `market_context_missing`, `trend_not_up`, `mean_return_not_positive`, `rsi_out_of_range`, `atr_pct_out_of_range`, `volume_ratio_below_min`, `stop_not_positive`). Payload numbers must be `str`/`Decimal`/`int`; floats, booleans, and non-finite values count as unavailable. Other analysts (news, analytical risk, strategy researcher) are ignored by `rules-v1`.

## Higher-timeframe context (`EvidencePackage.htf_context`)

`aegis.evidence.build_htf_context(htf_candles, *, as_of, instrument=None)` returns an optional `HigherTimeframeContext` (for example daily regime levels for an intraday trading package). `build_evidence_package(..., htf_candles=...)` fills it with `as_of = snapshot.as_of` and the snapshot instrument. Without `htf_candles` the field is `null`.

* Only candles with `is_final` **and** `close_time <= as_of` are used. A daily bar still forming at the trading `as_of` is excluded even if its `is_final` flag is wrong, so the regime never sees today's close before the day ends. A bar whose `close_time` equals `as_of` exactly counts as completed.
* When `instrument` is given, other symbols and market types are ignored. Eligible candles must share one interval and have unique `open_time` values (`ValueError` otherwise).
* Fields: `timeframe`, `as_of` (`close_time` of the last completed bar used), `last_close`, `bars_available`, and `sma` mapping each fixed lookback `"20"`, `"50"`, `"100"`, `"200"` to the mean of the last N closes, or `null` when fewer than N bars exist. Computed in `Decimal` (prec 28, half-even).
* `null` when there is no completed bar. These values are descriptive only. Key names avoid the Jev redaction fragments, so the block passes through `redact_evidence_package` unchanged.

## Deterministic rule strategy `rules-v2` (daily regime + intraday pullback)

`aegis.strategy.decide_v2(package, params)` implements the strategy pre-registered in [`BACKTEST_PREREGISTRATION_V2_2026-10-15.md`](BACKTEST_PREREGISTRATION_V2_2026-10-15.md). Its purity, Decimal context, and authority limits match `rules-v1`: it is not trade authority, and its parameters are research inputs, not owner-approved risk-policy values. Nothing here claims the strategy is profitable.

`RuleParamsV2` (frozen, no defaults):

| Field | Meaning | Validation |
| --- | --- | --- |
| `regime_n` | Daily SMA lookback used for the regime | `int` in `{20, 50, 100, 200}` |
| `rsi_entry_max` | Inclusive ceiling on technical `rsi_14` | `Decimal` in `[0, 100]` |
| `k_atr_stop` | Stop distance in multiples of `atr_14` | `Decimal > 0` |
| `r_target` | Take-profit distance as a multiple of stop distance | `Decimal > 0` |
| `max_hold_bars` | Time stop in trading bars (applied by the caller's exit simulation, not by `decide_v2`) | `int >= 1` |

`to_dict()` returns canonical strings and includes `"strategy": "rules-v2"`. `from_dict()` accepts the dict with or without that key; ints may be `int` or integer strings, and floats and bools are rejected. `param_hash()` is SHA-256 over `{"strategy_version": "rules-v2", "params": to_dict()}`.

Long-only. The result is BUY / `long` only when these checks pass, in this order (the first failure is reported as `no_trade:<NoTradeReasonV2>`):

1. `market_context` is present, `as_of <= package.as_of`, `last_close > 0`, and `atr_14 > 0` (`market_context_missing`, `market_context_after_as_of`, `last_close_invalid`, `atr_unavailable`).
2. `htf_context` is present, `as_of <= package.as_of`, `sma[str(regime_n)]` is not null, and the HTF `last_close > sma` with strict inequality (`htf_context_missing`, `htf_context_after_as_of`, `htf_sma_unavailable`, `regime_not_up`).
3. Exactly one technical evidence exists, its status is `ok`, and `rsi_14 <= rsi_entry_max` (`technical_missing`, `technical_ambiguous`, `technical_not_ok`, `rsi_unavailable`, `rsi_above_entry_max`). This check is only the requirement to read RSI unambiguously. The technical analyst reports `ok` only when `sma_20`, `ema_20`, `rsi_14`, and `atr_14` are all available.
4. Levels: `entry = market_context.last_close`, `stop = entry - k_atr_stop * atr_14`, `take_profit = entry + r_target * (entry - stop)`. A non-positive stop returns `stop_not_positive`.

The intraday trend, quantitative mean return, `atr_pct_of_close` band, and volume ratio are **not** v2 conditions.

Dispatch helpers in `aegis.strategy` are `decide_any(package, params)` (selects by params type), `params_from_dict(data)` (selects by the `strategy` key, where a missing key means `rules-v1` for backward compatibility), `max_hold_bars_of(params)` (returns `None` for v1), and `strategy_version_of(params)`. `rules-v1` behaviour, its `to_dict` (no `strategy` key), and its param hashes are unchanged.

## Non-goals

* Features are descriptive inputs for analysts. They are not trade signals and do not size or approve orders.
* This document does not invent risk-policy numeric limits.
