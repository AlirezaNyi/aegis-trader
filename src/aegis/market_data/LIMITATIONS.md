# Market data limitations (Phase 2)

Verified against official Toobit docs on 2026-10-09 (Phase 2 re-check):

* REST: `GET /api/v1/time`, `GET /api/v1/exchangeInfo`, `GET /quote/v1/klines`
* WS: `wss://stream.toobit.com/quote/ws/v1`, topic `kline_$interval`, JSON ping/pong

## Documented behaviors encoded in code

* Without `startTime`/`endTime`, klines returns only the latest candle.
* Kline `limit` max is 1000.
* Supported Aegis intervals: `1m`, `5m`, `15m`.
* Spot symbols come from `exchangeInfo.symbols`; Futures from `exchangeInfo.contracts`.
* WS control message rate limit is 5 messages/second (client should not spam ping/sub).

## Unverified / deferred

* Exact WS kline “bar closed” flag is not documented; Aegis treats stream updates as `is_final=False` and finalizes the previous open when a newer `t` arrives.
* Futures-specific private streams are out of scope for Phase 2.
* No separate testnet was found; tests use mocks/fixtures only.
* Futures quantity unit vs `contractMultiplier` remains unresolved (metadata is stored, not interpreted for sizing).
