"""Binance public kline archive import (research-only). No real network or database."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from aegis.market_data.binance_archive import (
    BINANCE_ARCHIVE_SOURCE,
    BINANCE_EXCHANGE,
    ArchiveFetcher,
    ArchiveFile,
    ArchiveFormatError,
    ChecksumFormatError,
    ChecksumMismatchError,
    archive_window,
    binance_instrument,
    count_gaps,
    daily_file,
    epoch_to_datetime,
    expected_bars,
    extract_csv,
    import_symbol,
    month_days,
    monthly_file,
    parse_checksum,
    parse_kline_csv,
    plan_archive_files,
    previous_months,
    validate_symbol,
    verify_checksum,
)
from aegis.market_data.candle_store import CandleStore
from aegis.market_data.cross_venue import compare_closes, percentile
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef

M5 = Timeframe.M5
STEP_MS = 300_000
STEP = timedelta(minutes=5)
SEP1 = datetime(2026, 9, 1, tzinfo=UTC)
LATER = datetime(2030, 1, 1, tzinfo=UTC)


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _ms_row(open_time: datetime, *, close: str = "1.05", high: str = "1.1") -> str:
    open_ms = _ms(open_time)
    return f"{open_ms},1.0,{high},0.9,{close},10,{open_ms + STEP_MS - 1},10.5,3,5,5.2,0"


def _us_row(open_time: datetime, *, close: str = "1.05") -> str:
    open_us = _ms(open_time) * 1000
    close_us = open_us + STEP_MS * 1000 - 1
    return f"{open_us},1.0,1.1,0.9,{close},10,{close_us},10.5,3,5,5.2,0"


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return buf.getvalue()


class FakeArchive:
    """httpx MockTransport handler serving zips + checksums; 404 otherwise."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []

    def add(self, file: ArchiveFile, csv_text: str, *, bad_checksum: bool = False) -> None:
        data = _zip(file.filename.removesuffix(".zip") + ".csv", csv_text)
        digest = "0" * 64 if bad_checksum else hashlib.sha256(data).hexdigest()
        self.files["/" + file.path] = data
        self.files["/" + file.checksum_path] = f"{digest}  {file.filename}\n".encode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        body = self.files.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    def fetcher(self, cache_dir: Path | None = None) -> ArchiveFetcher:
        client = httpx.AsyncClient(
            base_url="https://archive.test", transport=httpx.MockTransport(self.handler)
        )
        return ArchiveFetcher(cache_dir=cache_dir, client=client)


class FakeSink:
    def __init__(self) -> None:
        self.batches: list[list[Candle]] = []
        self.keys: set[tuple[str, str, datetime]] = set()

    def upsert_final(self, candles: Iterable[Candle]) -> int:
        batch = list(candles)
        self.batches.append(batch)
        inserted = 0
        for candle in batch:
            key = (candle.instrument.exchange, candle.instrument.symbol, candle.open_time)
            if key not in self.keys:
                self.keys.add(key)
                inserted += 1
        return inserted


# --- timestamps / parsing ------------------------------------------------------------


def test_epoch_units_detected_by_magnitude() -> None:
    t = datetime(2025, 1, 1, tzinfo=UTC)
    assert epoch_to_datetime(_ms(t)) == t
    assert epoch_to_datetime(_ms(t) * 1000) == t
    assert epoch_to_datetime(1735689899999999) == t + STEP - timedelta(microseconds=1)
    with pytest.raises(ValueError):
        epoch_to_datetime(-1)


def test_parse_millisecond_rows() -> None:
    t0 = datetime(2024, 12, 1, tzinfo=UTC)
    parsed = parse_kline_csv(
        "\n".join([_ms_row(t0), _ms_row(t0 + STEP)]) + "\n",
        symbol="ADAUSDT",
        interval=M5,
        now=LATER,
    )
    assert (parsed.rows, parsed.malformed, parsed.header_rows, parsed.not_final) == (2, 0, 0, 0)
    first = parsed.candles[0]
    assert first.open_time == t0
    assert first.close_time == t0 + STEP - timedelta(milliseconds=1)
    assert first.close == Decimal("1.05") and isinstance(first.close, Decimal)
    assert first.quote_volume == Decimal("10.5") and first.trade_count == 3


def test_parse_microsecond_rows_keep_precision() -> None:
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    parsed = parse_kline_csv(_us_row(t0), symbol="ADAUSDT", interval=M5, now=LATER)
    assert len(parsed.candles) == 1
    candle = parsed.candles[0]
    assert candle.open_time == t0
    assert candle.close_time == t0 + STEP - timedelta(microseconds=1)


def test_parse_detects_units_per_row_across_2025_switch() -> None:
    last_2024 = datetime(2024, 12, 31, 23, 55, tzinfo=UTC)
    text = "\n".join([_ms_row(last_2024), _us_row(last_2024 + STEP)])
    parsed = parse_kline_csv(text, symbol="TRXUSDT", interval=M5, now=LATER)
    assert [c.open_time for c in parsed.candles] == [last_2024, last_2024 + STEP]
    assert parsed.malformed == 0


def test_parsed_candles_are_binance_research_rows() -> None:
    parsed = parse_kline_csv(_us_row(SEP1), symbol="BTCUSDT", interval=M5, now=LATER)
    candle = parsed.candles[0]
    assert candle.instrument.exchange == BINANCE_EXCHANGE != "toobit"
    assert candle.instrument.market_type == MarketType.SPOT
    assert candle.source == BINANCE_ARCHIVE_SOURCE
    assert candle.is_final is True
    assert candle.received_at == candle.close_time


def test_parse_skips_header_and_counts_malformed() -> None:
    t0 = datetime(2024, 6, 1, tzinfo=UTC)
    open_ms = _ms(t0)
    rows = [
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,tbb,tbq,ignore",
        _ms_row(t0),
        _ms_row(t0 + timedelta(minutes=1)),  # open not aligned to 5m
        f"{open_ms},1,1.1,0.9,1.05,10,{open_ms + STEP_MS + 5},1,1,1,1,0",  # close past bar
        f"{open_ms},1,1.1,0.9,1.05,10,{open_ms - 1},1,1,1,1,0",  # close before open
        _ms_row(t0 + STEP, close="1.5"),  # close above high
        _ms_row(t0 + 2 * STEP, close="abc"),
        f"{open_ms},1,1.1,0.9",  # too few columns
        f"{open_ms},0,1.1,0,1.05,10,{open_ms + STEP_MS - 1},1,1,1,1,0",  # zero price
        "not,a,row",  # non-numeric after the first row is malformed, not a header
        "",
        _ms_row(t0 + 3 * STEP),
    ]
    parsed = parse_kline_csv("\n".join(rows), symbol="ADAUSDT", interval=M5, now=LATER)
    assert parsed.header_rows == 1
    assert parsed.malformed == 8
    assert [c.open_time for c in parsed.candles] == [t0, t0 + 3 * STEP]
    assert parsed.rows == 10


def test_parse_keeps_only_closed_bars() -> None:
    now = SEP1 + STEP + timedelta(minutes=1)  # second bar still open
    parsed = parse_kline_csv(
        "\n".join([_us_row(SEP1), _us_row(SEP1 + STEP)]), symbol="ADAUSDT", interval=M5, now=now
    )
    assert [c.open_time for c in parsed.candles] == [SEP1]
    assert parsed.not_final == 1


def test_symbol_validation_blocks_path_tricks() -> None:
    assert validate_symbol("ADAUSDT") == "ADAUSDT"
    for bad in ("../etc", "ada", "ADA/USDT", "", "A"):
        with pytest.raises(ValueError):
            validate_symbol(bad)
    assert binance_instrument("ETHUSDT").exchange == "binance"


# --- checksum / zip ---------------------------------------------------------------


def test_parse_checksum_formats() -> None:
    digest = "AB" * 32
    assert parse_checksum(f"{digest}  X.zip\n", expected_filename="X.zip") == digest.lower()
    assert parse_checksum(f"{digest} *X.zip", expected_filename="X.zip") == digest.lower()
    assert parse_checksum(digest) == digest.lower()
    with pytest.raises(ChecksumFormatError):
        parse_checksum("nothex  X.zip")
    with pytest.raises(ChecksumFormatError):
        parse_checksum("")
    with pytest.raises(ChecksumFormatError):
        parse_checksum(f"{digest}  Y.zip", expected_filename="X.zip")


def test_verify_checksum_mismatch_rejected() -> None:
    data = b"payload"
    verify_checksum(data, hashlib.sha256(data).hexdigest().upper())
    with pytest.raises(ChecksumMismatchError):
        verify_checksum(data + b"!", hashlib.sha256(data).hexdigest())


def test_extract_csv_guards() -> None:
    assert extract_csv(_zip("a.csv", "1,2\n")) == "1,2\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("a.csv", "x")
        archive.writestr("b.csv", "y")
    with pytest.raises(ArchiveFormatError):
        extract_csv(buf.getvalue())
    with pytest.raises(ArchiveFormatError):
        extract_csv(_zip("a.txt", "x"))
    with pytest.raises(ArchiveFormatError):
        extract_csv(b"not a zip")
    with pytest.raises(ArchiveFormatError):
        extract_csv(_zip("a.csv", "x" * 100), max_bytes=10)


# --- gaps / enumeration ---------------------------------------------------------------


def test_count_gaps() -> None:
    times = [SEP1 + i * STEP for i in (0, 1, 2, 5, 6, 10)] + [SEP1 + STEP]
    report = count_gaps(times, M5)
    assert report.bars == 6
    assert report.duplicates == 1
    assert report.missing_bars == 2 + 3
    assert report.gap_runs == 2
    assert report.largest_gap_bars == 3
    assert report.first_open == SEP1 and report.last_open == SEP1 + 10 * STEP

    contiguous = count_gaps([SEP1 + i * STEP for i in range(5)], M5)
    assert (contiguous.missing_bars, contiguous.gap_runs) == (0, 0)
    empty = count_gaps([], M5)
    assert empty.bars == 0 and empty.first_open is None


def test_expected_bars() -> None:
    assert expected_bars(SEP1, SEP1 + timedelta(days=1), M5) == 288
    assert expected_bars(SEP1, SEP1, M5) == 0


def test_previous_months_and_days() -> None:
    now = datetime(2026, 10, 14, 9, tzinfo=UTC)
    months = previous_months(now, 13)
    assert len(months) == 13
    assert months[0] == (2025, 9) and months[-1] == (2026, 9)
    assert previous_months(datetime(2026, 1, 5, tzinfo=UTC), 2) == [(2025, 11), (2025, 12)]
    assert previous_months(now, 0) == []
    assert len(month_days(2024, 2)) == 29
    assert month_days(2026, 10, before=date(2026, 10, 3)) == [date(2026, 10, 1), date(2026, 10, 2)]
    with pytest.raises(ValueError):
        previous_months(datetime(2026, 10, 14), 1)  # naive datetime


def test_plan_archive_files_and_paths() -> None:
    now = datetime(2026, 10, 14, 9, tzinfo=UTC)
    files = plan_archive_files("ADAUSDT", M5, now=now, months=2)
    assert [f.period for f in files[:2]] == ["2026-08", "2026-09"]
    assert [f.kind for f in files[2:]] == ["daily"] * 13
    assert files[-1].period == "2026-10-13"
    assert files[0].path == "data/spot/monthly/klines/ADAUSDT/5m/ADAUSDT-5m-2026-08.zip"
    assert files[-1].checksum_path == (
        "data/spot/daily/klines/ADAUSDT/5m/ADAUSDT-5m-2026-10-13.zip.CHECKSUM"
    )
    first_of_month = plan_archive_files(
        "ADAUSDT", M5, now=datetime(2026, 10, 1, 3, tzinfo=UTC), months=1
    )
    assert [f.kind for f in first_of_month] == ["monthly"]
    assert archive_window(now, 13) == (
        datetime(2025, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 14, tzinfo=UTC),
    )


# --- fetcher ------------------------------------------------------------------------


async def test_fetcher_verifies_and_caches(tmp_path: Path) -> None:
    archive = FakeArchive()
    file = monthly_file("ADAUSDT", M5, 2026, 9)
    archive.add(file, _us_row(SEP1))
    async with archive.fetcher(tmp_path) as fetcher:
        first = await fetcher.get(file)
        assert first is not None and first.from_cache is False
        assert len(archive.requests) == 2
        second = await fetcher.get(file)
    assert second is not None and second.from_cache is True
    assert second.data == first.data
    assert len(archive.requests) == 2  # served from cache, no network
    assert (tmp_path / file.path).is_file()


async def test_fetcher_rejects_checksum_mismatch_and_does_not_cache(tmp_path: Path) -> None:
    archive = FakeArchive()
    file = monthly_file("ADAUSDT", M5, 2026, 9)
    archive.add(file, _us_row(SEP1), bad_checksum=True)
    async with archive.fetcher(tmp_path) as fetcher:
        with pytest.raises(ChecksumMismatchError):
            await fetcher.get(file)
    assert not (tmp_path / file.path).exists()


async def test_fetcher_missing_file_returns_none() -> None:
    async with FakeArchive().fetcher() as fetcher:
        assert await fetcher.get(daily_file("ADAUSDT", M5, date(2026, 10, 14))) is None


async def test_fetcher_redownloads_tampered_cache(tmp_path: Path) -> None:
    archive = FakeArchive()
    file = monthly_file("ADAUSDT", M5, 2026, 9)
    archive.add(file, _us_row(SEP1))
    async with archive.fetcher(tmp_path) as fetcher:
        assert await fetcher.get(file) is not None
    (tmp_path / file.path).write_bytes(b"tampered")
    async with archive.fetcher(tmp_path) as fetcher:
        again = await fetcher.get(file)
    assert again is not None and again.from_cache is False
    assert len(archive.requests) == 4


async def test_fetcher_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(
        base_url="https://archive.test", transport=httpx.MockTransport(handler)
    )
    async with ArchiveFetcher(client=client) as fetcher:
        with pytest.raises(httpx.HTTPStatusError):
            await fetcher.get(monthly_file("ADAUSDT", M5, 2026, 9))


async def test_import_symbol_records_http_errors_and_continues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(
        base_url="https://archive.test", transport=httpx.MockTransport(handler)
    )
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    async with ArchiveFetcher(client=client) as fetcher:
        result = await import_symbol(fetcher, FakeSink(), "ADAUSDT", M5, now=now, months=1)
    assert result.files == 3
    assert result.errors == [
        "ADAUSDT-5m-2026-09.zip:HTTPStatusError",
        "ADAUSDT-5m-2026-10-01.zip:HTTPStatusError",
        "ADAUSDT-5m-2026-10-02.zip:HTTPStatusError",
    ]
    await client.aclose()


# --- import orchestration ------------------------------------------------------------


async def test_import_symbol_counts_checksum_failures_missing_and_gaps() -> None:
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    archive = FakeArchive()
    archive.add(
        monthly_file("ADAUSDT", M5, 2026, 9),
        "\n".join(_us_row(SEP1 + i * STEP) for i in (0, 1, 3)),
    )
    archive.add(
        daily_file("ADAUSDT", M5, date(2026, 10, 1)),
        _us_row(datetime(2026, 10, 1, tzinfo=UTC)),
        bad_checksum=True,
    )
    # 2026-10-02 daily not published -> 404
    sink = FakeSink()
    async with archive.fetcher() as fetcher:
        result = await import_symbol(
            fetcher, sink, "ADAUSDT", M5, now=now, months=1, batch_size=2
        )
    assert result.files == 3
    assert (result.checksum_ok, result.checksum_failed, result.missing) == (1, 1, 1)
    assert result.errors == ["ADAUSDT-5m-2026-10-01.zip:checksum_mismatch"]
    assert (result.rows, result.final, result.inserted) == (3, 3, 3)
    assert [len(b) for b in sink.batches] == [2, 1]
    assert all(c.instrument.exchange == "binance" for b in sink.batches for c in b)
    assert result.gaps is not None
    assert (result.gaps.missing_bars, result.gaps.gap_runs) == (1, 1)
    assert result.window_start == SEP1
    assert result.window_end == datetime(2026, 10, 3, tzinfo=UTC)
    assert result.missing_vs_window == 288 * 32 - 3


async def test_import_symbol_falls_back_to_daily_when_monthly_missing(tmp_path: Path) -> None:
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    archive = FakeArchive()
    for day in (1, 2):
        start = datetime(2026, 9, day, tzinfo=UTC)
        archive.add(
            daily_file("ADAUSDT", M5, start.date()),
            "\n".join(_us_row(start + i * STEP) for i in range(288)),
        )
    sink = FakeSink()
    async with archive.fetcher(tmp_path) as fetcher:
        result = await import_symbol(fetcher, sink, "ADAUSDT", M5, now=now, months=1)
    assert result.files == 1 + 30 + 2
    assert result.checksum_ok == 2
    assert result.missing == 1 + 28 + 2
    assert result.inserted == result.final == 576
    assert result.gaps is not None and result.gaps.missing_bars == 0

    # Re-run: idempotent sink, zips served from the verified cache.
    async with archive.fetcher(tmp_path) as fetcher:
        again = await import_symbol(fetcher, sink, "ADAUSDT", M5, now=now, months=1)
    assert again.inserted == 0 and again.final == 576
    assert again.from_cache == 2


async def test_import_symbol_rejects_bad_batch_size() -> None:
    async with FakeArchive().fetcher() as fetcher:
        with pytest.raises(ValueError):
            await import_symbol(
                fetcher, FakeSink(), "ADAUSDT", M5, now=LATER, months=1, batch_size=10_000
            )


# --- store bounds (fail-open) ----------------------------------------------------------


def test_time_bounds_disabled_and_unreachable() -> None:
    assert CandleStore(None).time_bounds(binance_instrument("ADAUSDT"), M5) is None
    store = CandleStore("postgresql+psycopg://u:p@127.0.0.1:1/x")
    assert store.time_bounds(binance_instrument("ADAUSDT"), M5) is None
    assert store.last_error is not None and store.last_error.startswith("bounds:")


# --- cross-venue comparison -------------------------------------------------------------


def _candle(exchange: str, open_time: datetime, close: str) -> Candle:
    return Candle(
        instrument=InstrumentRef(exchange=exchange, market_type=MarketType.SPOT, symbol="X"),
        interval=M5,
        open_time=open_time,
        close_time=open_time + STEP - timedelta(milliseconds=1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        is_final=True,
        source="test",
        received_at=open_time,
    )


def test_percentile_linear() -> None:
    values = [0.0, 10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 0.0) == 0.0
    assert percentile(values, 0.5) == 20.0
    assert percentile(values, 0.95) == pytest.approx(38.0)
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_compare_closes_bps_and_return_correlation() -> None:
    reference_closes = ["100", "101", "102", "101", "103"]
    primary_closes = ["100.1", "101.101", "102", "100.899", "103.103"]
    reference = [_candle("binance", SEP1 + i * STEP, c) for i, c in enumerate(reference_closes)]
    primary = [_candle("toobit", SEP1 + i * STEP, c) for i, c in enumerate(primary_closes)]
    primary.append(_candle("toobit", SEP1 + 10 * STEP, "1"))  # unmatched
    report = compare_closes(primary, reference, M5)
    assert report.primary_bars == 6 and report.reference_bars == 5
    assert report.matched == 5
    assert report.first_open == SEP1 and report.last_open == SEP1 + 4 * STEP
    assert report.max_abs_bps == pytest.approx(10.0)
    assert report.median_abs_bps == pytest.approx(10.0)
    assert report.mean_abs_bps == pytest.approx(8.0)
    assert report.mean_signed_bps == pytest.approx(4.0)
    assert report.return_pairs == 4
    assert report.return_correlation is not None and report.return_correlation > 0.99


def test_compare_closes_without_overlap() -> None:
    report = compare_closes(
        [_candle("toobit", SEP1, "1")], [_candle("binance", SEP1 + STEP, "1")], M5
    )
    assert report.matched == 0
    assert report.mean_abs_bps is None and report.return_correlation is None
    assert report.return_pairs == 0
