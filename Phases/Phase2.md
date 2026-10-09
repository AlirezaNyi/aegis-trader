# Aegis Phase 2 — Toobit Market Data

Implement Toobit market-data ingestion for Spot and Futures.

Before coding, verify current official documentation for REST, WebSocket, authentication where required, instrument metadata, candle schemas, rate limits, timestamps, and supported market-data channels.

Implement:

* REST market-data adapter.
* WebSocket ingestion, heartbeat, reconnect, backoff, and resubscription.
* Symbol metadata and precision models.
* Normalized 1m, 5m, and 15m candles.
* Explicit handling of incomplete versus finalized candles.
* Timestamp normalization, duplicate detection, gap detection, and stale-data detection.
* Event provenance and exchange/local timestamps.
* Historical ingestion only where officially supported.
* Deterministic fixtures and mock adapters.
* Health metrics for data freshness, event lag, reconnects, and gaps.

Never invent endpoints or silently interpret missing data as valid. If a feature is unverified, leave it behind an interface and document the limitation.

Acceptance tests must cover malformed events, reconnects, duplicates, out-of-order events, gaps, stale data, and candle aggregation.

Do not implement live order execution. Run the relevant tests and report verified API behavior and unresolved uncertainties. Stop after this phase.
