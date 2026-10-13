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

## Snapshot fields

`schema_version`, `snapshot_id`, `instrument`, `market_type`, `timeframe`, `as_of`, `include_intrabar`, `candle_count`, `gap_detected`, `features` (map of name → `{value, status, reason}`), `created_at`.

## Evidence `market_context` (optional)

`EvidencePackage.market_context` (built by `aegis.evidence.build_market_context`, or `build_evidence_package(..., candles=...)`) gives descriptive price levels:

* Uses only `is_final` candles matching the snapshot instrument/timeframe with `close_time <= snapshot.as_of` (no look-ahead, no intrabar). `None` when no such candle exists.
* `last_close`, `recent_high` (max high), `recent_low` (min low) over the last `lookback_candles` (default lookback 20; fewer if history is short).
* `atr_14` copied from the snapshot only when its status is `ok` (absolute price units); `atr_pct_of_close = atr_14 / last_close * 100`, quantized to 4 dp (percent, `0.42` = 0.42%).
* `spread_bps` is caller-supplied; `spread_status` is `ok` when provided, else `unavailable`. The builder never derives spread from candles.
* `as_of` is the `close_time` of the last eligible candle.

## Non-goals

* Features are descriptive inputs for analysts. They are not trade signals and do not size or approve orders.
* This document does not invent risk-policy numeric limits.
