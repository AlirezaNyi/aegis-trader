# Market data limitations (Phase 2)

Verified against official Toobit docs on 2026-10-09 (Phase 2 re-check):

* REST: `GET /api/v1/time`, `GET /api/v1/exchangeInfo`, `GET /quote/v1/klines`,
  `GET /quote/v1/depth` (shape re-checked against docs and a live public request 2026-10-13)
* WS: `wss://stream.toobit.com/quote/ws/v1`, topic `kline_$interval`, JSON ping/pong

## Documented behaviors encoded in code

* Without `startTime`/`endTime`, klines returns only the latest candle.
* Kline `limit` max is 1000.
* Supported Aegis intervals: `1m`, `5m`, `15m`.
* Spot symbols come from `exchangeInfo.symbols`; Futures from `exchangeInfo.contracts`.
* WS control message rate limit is 5 messages/second (client should not spam ping/sub).
* `MarketDataGateway.get_candles` returns the REST batch unchanged (no stream-duplicate
  filtering). Only the newest candle updates the freshness tracker. REST polls do not
  append to the optional event buffer; when a buffer is used it is capped (deque maxlen
  1024, engineering).
* REST I/O is async (`httpx.AsyncClient`): `ToobitRestMarketDataClient` methods,
  `MarketDataGateway.get_candles` / `get_order_book_top` / `refresh_metadata` /
  `get_metadata`, `MarketDataPort.get_candles`, `backfill_instrument`, and the Binance
  `ArchiveFetcher.get` / `import_symbol` are coroutines. Owners close clients with
  `aclose()` or `async with`. No automatic retries anywhere; HTTP errors propagate
  (`backfill_instrument` / `import_symbol` record them per window/file and continue).
  `CandleStore` stays synchronous (blocking DB calls inside the async scripts).
* WS reconnect/backoff triggers only on `OSError` (incl. timeouts/connection errors) and
  `websockets` exceptions; other errors propagate. A frame with unparseable `sendTime`
  is counted as malformed instead of failing the session.

## Order book (depth)

* `ToobitRestMarketDataClient.get_depth` / `MarketDataGateway.get_order_book_top` return
  `OrderBookTop` (best bid/ask + qty, exchange `t`, `received_at`). `spread_bps` =
  (ask - bid) / mid * 10000.
* Verified keys: `t` (ms), `b`, `a`; levels are `[price, qty]` decimal strings. Any other
  shape (e.g. `bids`/`asks`), an empty side (`EmptyOrderBookError`), non-positive prices or
  a crossed book raise `MalformedDepthError` — nothing is guessed.
* Level sort order is not documented; best bid/ask are computed as max/min.
* Request `limit` is capped at 100 by Aegis (engineering cap; docs give no maximum) and
  defaults to 5. Depth is REST snapshot only; the WS `depth` topic is not consumed.
* `get_order_book_top` is not on `MarketDataPort` (fixture/null ports unchanged).

## Candle persistence and backfill

* `CandleStore` writes only `is_final` candles to `market_candles` with
  `ON CONFLICT (exchange, market_type, symbol, interval, open_time) DO NOTHING`; stored
  bars are never revised. It is fail-open: disabled without a URL, and DB errors are
  logged (class name only) and return 0 / `[]`. The URL is not retained.
* `load_range` is `start <= open_time < end`, ordered ascending; returned candles have
  `is_final=True` and the original `received_at` (fetch time, not exchange close time).
* Postgres-specific insert; unit tests do not exercise a real database.
* `scripts/backfill_candles.py` paginates windows of <= 1000 bars oldest -> newest with a
  delay between requests. Observed live 2026-10-13 (undocumented): Toobit serves only the
  most recent ~3,500 bars per interval (1m ~2.4 days, 5m ~12 days, 15m ~36 days); older
  windows return empty lists and are reported as `empty`. Rate limits for klines are not
  documented here; the default 0.25 s delay is an engineering choice.
* Finality uses the parser rule (`close_time < now`); a bar stored as final is not
  re-checked against later exchange corrections.

## Binance archive (research-only history)

* `binance_archive.py` imports public Binance **spot** kline zips (data.binance.vision) into
  `market_candles` with `exchange="binance"`, `source="binance-archive"`. It is a research
  data source only — never a trading venue, never stored as `toobit`, no credentials.
  Facts and their verification status: `docs/API_CONTRACTS.md` §3A.
* **Basis risk:** Binance prices are not Toobit prices. Binance spot is far deeper with
  tighter spreads and more trades; Toobit bars may lag, deviate, or show different wicks
  and volume. Signals, fills, slippage and fee assumptions calibrated on Binance history
  do not transfer 1:1 to Toobit. Use `scripts/compare_binance_toobit.py` (close-diff bps
  and 5m return correlation over the stored overlap) before relying on archive data, and
  re-run it periodically — the overlap is only as long as stored Toobit history.
* Volume/quote volume/trade counts are Binance-venue numbers and are not comparable to
  Toobit volume. Taker-buy columns are not parsed or stored (no `Candle` field).
* Each zip is SHA-256 verified against its `.CHECKSUM` before parsing or caching; a mismatch
  skips the file and is reported. The cache (`data/binance_cache`, gitignored via `data/`)
  stores only verified zips; a cached zip that stops verifying is re-downloaded.
* Timestamp unit (ms before 2025-01-01, µs after) is detected per value by magnitude
  (>= 1e14 = µs). µs `close_time` (…59.999999) is stored as-is; Toobit rows use …59.999 ms.
  Join venues on `open_time`, never on `close_time`.
* `received_at` is set to the bar `close_time` (archive rows were not received live).
* A missing monthly zip (e.g. before the first-Monday publication) falls back to that
  month's daily zips; daily files appear the next day, so the window ends at today 00:00
  UTC. Rows with `close_time >= now` are skipped. Gaps are counted (internal holes and
  missing-vs-window) and reported, never filled.
* Malformed rows (non-numeric, misaligned open, close outside the bar, OHLC inconsistent,
  non-positive price, negative volume) are counted and skipped. Archive revisions after
  import are not picked up (`ON CONFLICT DO NOTHING`).
* `scripts/backtest_pipeline.py` loads `exchange="toobit"` only; replaying Binance history
  needs an explicit exchange option there (not implemented here).

## Unverified / deferred

* Exact WS kline “bar closed” flag is not documented; Aegis treats stream updates as `is_final=False` and finalizes the previous open when a newer `t` arrives.
* Futures-specific private streams are out of scope for Phase 2.
* No separate testnet was found; tests use mocks/fixtures only.
* Futures quantity unit vs `contractMultiplier` remains unresolved (metadata is stored, not interpreted for sizing).
