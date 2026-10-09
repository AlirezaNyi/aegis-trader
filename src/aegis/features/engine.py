"""Deterministic Feature Engine (features-v1)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.schemas.common import Timeframe
from aegis.schemas.features import FeatureSnapshot, FeatureValue, FeatureValueStatus
from aegis.schemas.market import Candle, InstrumentRef

FEATURES_SCHEMA_VERSION = "features-v1"

# Warm-up candle counts (inclusive) per feature name.
WARMUP: dict[str, int] = {
    "ret_1": 2,
    "sma_20": 20,
    "ema_20": 20,
    "rsi_14": 15,
    "atr_14": 15,
    "realized_vol_20": 21,
    "volume_sma_20": 20,
    "volume_ratio": 20,
}


def _null(status: FeatureValueStatus, reason: str) -> FeatureValue:
    return FeatureValue(value=None, status=status, reason=reason)


def _ok(value: Decimal) -> FeatureValue:
    return FeatureValue(value=value, status=FeatureValueStatus.OK)


def select_candles(
    candles: Sequence[Candle],
    *,
    as_of: datetime,
    include_intrabar: bool = False,
) -> list[Candle]:
    """Return candles eligible for feature computation at as_of."""
    selected: list[Candle] = []
    for candle in candles:
        if candle.close_time > as_of:
            continue
        if not candle.is_final and not include_intrabar:
            continue
        selected.append(candle)
    selected.sort(key=lambda c: c.open_time)
    return selected


def window_has_gap(candles: Sequence[Candle], interval: Timeframe) -> bool:
    if len(candles) < 2:
        return False
    step = interval_to_milliseconds(interval.value)
    for prev, curr in zip(candles, candles[1:], strict=False):
        prev_ms = int(prev.open_time.timestamp() * 1000)
        curr_ms = int(curr.open_time.timestamp() * 1000)
        if curr_ms - prev_ms > step:
            return True
    return False


def _sample_stdev(values: Sequence[Decimal]) -> Decimal | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values, Decimal("0")) / Decimal(n)
    variance = sum((v - mean) ** 2 for v in values) / Decimal(n - 1)
    # Decimal.sqrt available in Python 3.11+
    return variance.sqrt()


def _sma(closes: Sequence[Decimal]) -> Decimal:
    return sum(closes, Decimal("0")) / Decimal(len(closes))


def _ema_series(closes: Sequence[Decimal], span: int) -> list[Decimal]:
    """EMA with seed = SMA of first `span` closes; returns one EMA per close from index span-1."""
    if len(closes) < span:
        return []
    alpha = Decimal("2") / (Decimal(span) + Decimal("1"))
    ema = _sma(closes[:span])
    out = [ema]
    for price in closes[span:]:
        ema = alpha * price + (Decimal("1") - alpha) * ema
        out.append(ema)
    return out


def _wilder_rsi(closes: Sequence[Decimal], period: int = 14) -> Decimal | None:
    # Need period+1 closes.
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [c if c > 0 else Decimal("0") for c in changes]
    losses = [-c if c < 0 else Decimal("0") for c in changes]
    avg_gain = sum(gains[:period], Decimal("0")) / Decimal(period)
    avg_loss = sum(losses[:period], Decimal("0")) / Decimal(period)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * Decimal(period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + losses[i]) / Decimal(period)
    if avg_loss == 0:
        return Decimal("100") if avg_gain > 0 else Decimal("50")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))


def _true_range(high: Decimal, low: Decimal, prev_close: Decimal) -> Decimal:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _wilder_atr(candles: Sequence[Candle], period: int = 14) -> Decimal | None:
    if len(candles) < period + 1:
        return None
    trs: list[Decimal] = []
    for i in range(1, len(candles)):
        trs.append(
            _true_range(candles[i].high, candles[i].low, candles[i - 1].close)
        )
    atr = sum(trs[:period], Decimal("0")) / Decimal(period)
    for tr in trs[period:]:
        atr = (atr * Decimal(period - 1) + tr) / Decimal(period)
    return atr


def compute_features(
    candles: Sequence[Candle],
    *,
    instrument: InstrumentRef,
    timeframe: Timeframe,
    as_of: datetime,
    include_intrabar: bool = False,
    snapshot_id: UUID | None = None,
    created_at: datetime | None = None,
) -> FeatureSnapshot:
    """Compute features-v1 from candles. Look-ahead and open bars are excluded by default."""
    selected = select_candles(candles, as_of=as_of, include_intrabar=include_intrabar)
    gap = window_has_gap(selected, timeframe)
    features: dict[str, FeatureValue] = {}
    closes = [c.close for c in selected]
    volumes = [c.volume for c in selected]
    n = len(selected)

    def ready(name: str) -> bool:
        need = WARMUP[name]
        if n < need:
            features[name] = _null(
                FeatureValueStatus.INSUFFICIENT_SAMPLE,
                f"need {need} candles, have {n}",
            )
            return False
        window = selected[-need:]
        if window_has_gap(window, timeframe):
            features[name] = _null(
                FeatureValueStatus.UNAVAILABLE,
                "gap in warm-up window",
            )
            return False
        return True

    if ready("ret_1"):
        prev, last = closes[-2], closes[-1]
        if prev == 0:
            features["ret_1"] = _null(FeatureValueStatus.UNAVAILABLE, "zero previous close")
        else:
            features["ret_1"] = _ok((last - prev) / prev)

    if ready("sma_20"):
        features["sma_20"] = _ok(_sma(closes[-20:]))

    if ready("ema_20"):
        series = _ema_series(closes, 20)
        features["ema_20"] = _ok(series[-1])

    if ready("rsi_14"):
        rsi = _wilder_rsi(closes[-15:], 14)
        assert rsi is not None
        features["rsi_14"] = _ok(rsi)

    if ready("atr_14"):
        atr = _wilder_atr(selected[-15:], 14)
        assert atr is not None
        features["atr_14"] = _ok(atr)

    if ready("realized_vol_20"):
        rets: list[Decimal] = []
        window_closes = closes[-21:]
        for i in range(1, len(window_closes)):
            prev = window_closes[i - 1]
            if prev == 0:
                features["realized_vol_20"] = _null(
                    FeatureValueStatus.UNAVAILABLE, "zero close in return window"
                )
                break
            rets.append((window_closes[i] - prev) / prev)
        else:
            stdev = _sample_stdev(rets)
            assert stdev is not None
            features["realized_vol_20"] = _ok(stdev)

    if ready("volume_sma_20"):
        vol_sma = _sma(volumes[-20:])
        features["volume_sma_20"] = _ok(vol_sma)
        if vol_sma == 0:
            features["volume_ratio"] = _null(
                FeatureValueStatus.UNAVAILABLE, "zero volume sma"
            )
        else:
            features["volume_ratio"] = _ok(volumes[-1] / vol_sma)
    else:
        features["volume_ratio"] = features.get(
            "volume_sma_20",
            _null(FeatureValueStatus.INSUFFICIENT_SAMPLE, "volume warm-up incomplete"),
        )

    return FeatureSnapshot(
        schema_version=FEATURES_SCHEMA_VERSION,
        snapshot_id=snapshot_id or uuid4(),
        instrument=instrument,
        market_type=instrument.market_type,
        timeframe=timeframe,
        as_of=as_of,
        include_intrabar=include_intrabar,
        candle_count=n,
        gap_detected=gap,
        features=features,
        created_at=created_at or datetime.now(UTC),
    )
