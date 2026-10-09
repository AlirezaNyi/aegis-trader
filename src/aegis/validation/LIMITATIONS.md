# Validation limitations

## Implemented

* Batch validation of finalized candles for one instrument/timeframe.
* Detects empty windows, duplicates, out-of-order bars, gaps, and staleness.
* Fail-closed: `ok=False` and `market_integrity_ok=False` when issues block the cycle.
* Reuses issue kinds from `aegis.market_data.quality`.

## Deferred / unresolved

* Continuous stream tracker state is not persisted across process restarts.
* Stale threshold defaults to policy freshness (caller passes `stale_after_ms`);
  no separate owner-approved validation policy id yet.
* Does not backfill missing bars; gaps block new decisions.
* Intrabar (`is_final=False`) candles are excluded by design for trading decisions.
