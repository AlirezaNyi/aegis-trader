"""Binance public spot kline archive (data.binance.vision) — RESEARCH-ONLY data source.

Binance is NOT a trading venue for Aegis. Rows produced here are always stored with
`exchange="binance"` and `source="binance-archive"` so they can never be mistaken for
Toobit candles (every Toobit consumer filters `market_candles` by exchange).

Facts checked 2026-10-14 against https://github.com/binance/binance-public-data and
live public downloads (no credentials):

* Monthly files: `data/spot/monthly/klines/<SYM>/<INT>/<SYM>-<INT>-YYYY-MM.zip`
  (published around the first Monday of the following month).
* Daily files: `data/spot/daily/klines/<SYM>/<INT>/<SYM>-<INT>-YYYY-MM-DD.zip`
  (published the next day). Unpublished files return HTTP 404.
* Each zip has a sibling `.CHECKSUM` file: `<sha256 hex>  <zip filename>`.
* Each zip holds one CSV without a header row (observed 2024-12 .. 2026-10); columns:
  open_time, open, high, low, close, volume, close_time, quote_volume, trades,
  taker_buy_base, taker_buy_quote, ignore.
* SPOT timestamps are microseconds from 2025-01-01 and milliseconds before; units are
  detected per row by magnitude.

Archive files "may be updated at a later date" per Binance; stored bars are never
revised (`ON CONFLICT DO NOTHING`).
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import zipfile
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Protocol

import httpx

from aegis.market_data.timeutil import interval_to_milliseconds
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef

BINANCE_ARCHIVE_BASE_URL = "https://data.binance.vision"
BINANCE_EXCHANGE = "binance"
BINANCE_ARCHIVE_SOURCE = "binance-archive"

# Engineering caps (not Binance limits): a 1m monthly CSV is a few MB.
MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_UPSERT_BATCH = 4000  # 16 columns per row; stays below Postgres' 65535 bind params

# Epoch values >= 1e14 cannot be milliseconds before year ~5138, and every
# microsecond value after 1973 exceeds it, so magnitude separates the two units.
_MICROSECOND_THRESHOLD = 10**14
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,30}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MIN_COLUMNS = 11

ArchiveKind = Literal["monthly", "daily"]


class BinanceArchiveError(Exception):
    """Base error for archive download/parse problems."""


class ChecksumMismatchError(BinanceArchiveError):
    """Downloaded zip SHA-256 does not match the published `.CHECKSUM`."""


class ChecksumFormatError(BinanceArchiveError):
    """`.CHECKSUM` content is not `<sha256>  <filename>` for the expected file."""


class ArchiveFormatError(BinanceArchiveError):
    """Zip container is not a single, size-bounded CSV."""


def validate_symbol(symbol: str) -> str:
    """Binance spot symbols are upper-case alphanumerics (also keeps cache paths safe)."""
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError(f"invalid archive symbol: {symbol!r}")
    return symbol


@dataclass(frozen=True)
class ArchiveFile:
    symbol: str
    interval: Timeframe
    kind: ArchiveKind
    period: str  # "YYYY-MM" (monthly) or "YYYY-MM-DD" (daily)

    @property
    def filename(self) -> str:
        return f"{self.symbol}-{self.interval.value}-{self.period}.zip"

    @property
    def path(self) -> str:
        return (
            f"data/spot/{self.kind}/klines/{self.symbol}/{self.interval.value}/{self.filename}"
        )

    @property
    def checksum_path(self) -> str:
        return f"{self.path}.CHECKSUM"


# --- window / file enumeration ----------------------------------------------


def previous_months(now: datetime, months: int) -> list[tuple[int, int]]:
    """The `months` complete UTC calendar months before `now`'s month, oldest first."""
    if months < 0:
        raise ValueError("months must be >= 0")
    now_utc = _as_utc(now)
    year, month = now_utc.year, now_utc.month
    out: list[tuple[int, int]] = []
    for _ in range(months):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append((year, month))
    out.reverse()
    return out


def month_days(year: int, month: int, *, before: date | None = None) -> list[date]:
    """Calendar days of a month, optionally only those strictly before `before`."""
    day = date(year, month, 1)
    out: list[date] = []
    while day.month == month:
        if before is not None and day >= before:
            break
        out.append(day)
        day += timedelta(days=1)
    return out


def monthly_file(symbol: str, interval: Timeframe, year: int, month: int) -> ArchiveFile:
    return ArchiveFile(validate_symbol(symbol), interval, "monthly", f"{year:04d}-{month:02d}")


def daily_file(symbol: str, interval: Timeframe, day: date) -> ArchiveFile:
    return ArchiveFile(validate_symbol(symbol), interval, "daily", day.isoformat())


def plan_archive_files(
    symbol: str, interval: Timeframe, *, now: datetime, months: int
) -> list[ArchiveFile]:
    """Monthly zips for the last `months` complete months plus daily zips for the
    current month up to yesterday (UTC). Today's file is never published yet."""
    now_utc = _as_utc(now)
    files = [monthly_file(symbol, interval, y, m) for y, m in previous_months(now_utc, months)]
    today = now_utc.date()
    files.extend(
        daily_file(symbol, interval, d) for d in month_days(today.year, today.month, before=today)
    )
    return files


def archive_window(now: datetime, months: int) -> tuple[datetime, datetime]:
    """[first planned month start, today 00:00 UTC) — the span the planned files cover."""
    now_utc = _as_utc(now)
    today_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=UTC)
    planned = previous_months(now_utc, months)
    if planned:
        year, month = planned[0]
        start = datetime(year, month, 1, tzinfo=UTC)
    else:
        start = datetime(now_utc.year, now_utc.month, 1, tzinfo=UTC)
    return start, today_start


# --- checksum / zip ------------------------------------------------------------


def parse_checksum(text: str, *, expected_filename: str | None = None) -> str:
    """Return the lower-case SHA-256 from `<hex>  <filename>` (filename optional)."""
    parts = text.strip().split()
    if not parts or not _SHA256_RE.fullmatch(parts[0]):
        raise ChecksumFormatError("checksum file does not start with a sha256 hex digest")
    if expected_filename is not None and len(parts) >= 2:
        name = parts[1].lstrip("*")  # sha256sum binary-mode marker
        if name != expected_filename:
            raise ChecksumFormatError(
                f"checksum names {name!r}, expected {expected_filename!r}"
            )
    return parts[0].lower()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_checksum(data: bytes, expected_sha256: str) -> None:
    if sha256_hex(data) != expected_sha256.lower():
        raise ChecksumMismatchError("sha256 mismatch")


def extract_csv(zip_bytes: bytes, *, max_bytes: int = MAX_CSV_BYTES) -> str:
    """Read the single CSV member in memory (never extracted to disk)."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            if len(members) != 1 or not members[0].filename.endswith(".csv"):
                raise ArchiveFormatError(f"expected one .csv member, found {len(members)}")
            member = members[0]
            if member.file_size > max_bytes:
                raise ArchiveFormatError("csv member exceeds size cap")
            with archive.open(member) as handle:
                raw = handle.read(max_bytes + 1)
    except zipfile.BadZipFile as exc:
        raise ArchiveFormatError("not a zip file") from exc
    if len(raw) > max_bytes:
        raise ArchiveFormatError("csv member exceeds size cap")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveFormatError("csv is not utf-8") from exc


# --- CSV parsing ------------------------------------------------------------------


def epoch_to_datetime(value: int) -> datetime:
    """Binance epoch integer to UTC datetime; microseconds if >= 1e14 else milliseconds."""
    if value < 0:
        raise ValueError("negative epoch")
    if value >= _MICROSECOND_THRESHOLD:
        return _EPOCH + timedelta(microseconds=value)
    return _EPOCH + timedelta(milliseconds=value)


@dataclass
class ParsedKlines:
    candles: list[Candle] = field(default_factory=list)
    rows: int = 0  # non-blank data rows seen (header excluded)
    header_rows: int = 0
    malformed: int = 0
    not_final: int = 0  # close_time >= now (bar still open)


def binance_instrument(symbol: str) -> InstrumentRef:
    return InstrumentRef(
        exchange=BINANCE_EXCHANGE, market_type=MarketType.SPOT, symbol=validate_symbol(symbol)
    )


def parse_kline_csv(
    text: str,
    *,
    symbol: str,
    interval: Timeframe,
    now: datetime,
) -> ParsedKlines:
    """Parse archive CSV rows into finalized `Candle`s (exchange="binance").

    Malformed rows (bad numbers, misaligned open time, close outside the bar,
    inconsistent OHLC, negative volume) are counted and skipped, never guessed.
    Only bars with `close_time < now` are returned. `received_at` = close_time.
    """
    instrument = binance_instrument(symbol)
    now_utc = _as_utc(now)
    step = timedelta(milliseconds=interval_to_milliseconds(interval.value))
    step_us = interval_to_milliseconds(interval.value) * 1000
    result = ParsedKlines()
    first_content_row = True
    for row in csv.reader(io.StringIO(text)):
        if not row or all(not cell.strip() for cell in row):
            continue
        first_cell = row[0].strip()
        if not first_cell.isdigit():
            if first_content_row:
                result.header_rows += 1
                first_content_row = False
                continue
            result.rows += 1
            result.malformed += 1
            continue
        first_content_row = False
        result.rows += 1
        candle = _parse_row(row, instrument, interval, step, step_us)
        if candle is None:
            result.malformed += 1
            continue
        if candle.close_time >= now_utc:
            result.not_final += 1
            continue
        result.candles.append(candle)
    return result


def _parse_row(
    row: Sequence[str],
    instrument: InstrumentRef,
    interval: Timeframe,
    step: timedelta,
    step_us: int,
) -> Candle | None:
    if len(row) < _MIN_COLUMNS:
        return None
    try:
        open_raw = int(row[0].strip())
        close_raw = int(row[6].strip())
        open_time = epoch_to_datetime(open_raw)
        close_time = epoch_to_datetime(close_raw)
        open_ = Decimal(row[1].strip())
        high = Decimal(row[2].strip())
        low = Decimal(row[3].strip())
        close = Decimal(row[4].strip())
        volume = Decimal(row[5].strip())
        quote_volume = Decimal(row[7].strip())
        trade_count = int(row[8].strip())
    except (ValueError, InvalidOperation, OverflowError):
        return None
    open_us = (open_time - _EPOCH) // timedelta(microseconds=1)
    if open_us % step_us != 0:
        return None
    # Each timestamp's unit is detected independently; the close must fall inside
    # the bar it belongs to (a mis-scaled value lands far outside and is rejected).
    if not (open_time < close_time < open_time + step):
        return None
    numbers = (open_, high, low, close, volume, quote_volume)
    if any(not value.is_finite() for value in numbers):
        return None
    if min(open_, high, low, close) <= 0 or volume < 0 or quote_volume < 0 or trade_count < 0:
        return None
    if high < max(open_, close, low) or low > min(open_, close):
        return None
    return Candle(
        instrument=instrument,
        interval=interval,
        open_time=open_time,
        close_time=close_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=quote_volume,
        trade_count=trade_count,
        is_final=True,
        source=BINANCE_ARCHIVE_SOURCE,
        received_at=close_time,
    )


# --- gap accounting --------------------------------------------------------------


@dataclass(frozen=True)
class GapReport:
    bars: int  # unique open times
    duplicates: int
    missing_bars: int  # internal holes between first and last bar
    gap_runs: int
    largest_gap_bars: int
    first_open: datetime | None
    last_open: datetime | None


def count_gaps(open_times: Iterable[datetime], interval: Timeframe) -> GapReport:
    """Missing bars between the first and last observed open time (edges excluded)."""
    raw = list(open_times)
    unique = sorted(set(raw))
    duplicates = len(raw) - len(unique)
    step = timedelta(milliseconds=interval_to_milliseconds(interval.value))
    missing = runs = largest = 0
    for previous, current in zip(unique, unique[1:], strict=False):
        hole = (current - previous) // step - 1
        if hole > 0:
            missing += hole
            runs += 1
            largest = max(largest, hole)
    return GapReport(
        bars=len(unique),
        duplicates=duplicates,
        missing_bars=missing,
        gap_runs=runs,
        largest_gap_bars=largest,
        first_open=unique[0] if unique else None,
        last_open=unique[-1] if unique else None,
    )


def expected_bars(start: datetime, end: datetime, interval: Timeframe) -> int:
    if end <= start:
        return 0
    step = timedelta(milliseconds=interval_to_milliseconds(interval.value))
    return (end - start) // step


# --- download + cache ---------------------------------------------------------------


@dataclass(frozen=True)
class FetchedArchive:
    data: bytes
    from_cache: bool


class ArchiveFetcher:
    """Downloads archive zips + checksums; verifies SHA-256 before use or caching.

    Public, unauthenticated GETs only (async, sequential, no retries). The cache
    mirrors archive paths under `cache_dir`; a zip is cached only after its
    checksum verifies, and a cached zip that no longer verifies is discarded and
    re-downloaded. Use as `async with ArchiveFetcher(...) as fetcher:`.
    """

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        base_url: str = BINANCE_ARCHIVE_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
        delay_seconds: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cache_dir = cache_dir
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"User-Agent": "aegis-research/0.1"},
        )
        self._delay = delay_seconds
        self._sleep = sleep
        self._requests = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ArchiveFetcher:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def get(self, file: ArchiveFile) -> FetchedArchive | None:
        """Verified zip bytes, or None when the file is not published (HTTP 404).

        Raises `ChecksumMismatchError` / `ChecksumFormatError` / `httpx.HTTPError`
        (and `OSError` if the verified zip cannot be written to the cache).
        """
        cached = self._read_cache(file)
        if cached is not None:
            return FetchedArchive(data=cached, from_cache=True)
        checksum_text = await self._download(file.checksum_path)
        if checksum_text is None:
            return None
        expected = parse_checksum(
            checksum_text.decode("utf-8", errors="replace"), expected_filename=file.filename
        )
        data = await self._download(file.path)
        if data is None:
            return None
        verify_checksum(data, expected)
        self._write_cache(file, data, checksum_text)
        return FetchedArchive(data=data, from_cache=False)

    async def _download(self, path: str) -> bytes | None:
        if self._requests > 0 and self._delay > 0:
            await self._sleep(self._delay)
        self._requests += 1
        response = await self._client.get(f"/{path}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def _cache_paths(self, file: ArchiveFile) -> tuple[Path, Path] | None:
        if self._cache_dir is None:
            return None
        zip_path = self._cache_dir / file.path
        return zip_path, zip_path.with_name(zip_path.name + ".CHECKSUM")

    def _read_cache(self, file: ArchiveFile) -> bytes | None:
        paths = self._cache_paths(file)
        if paths is None:
            return None
        zip_path, sum_path = paths
        if not (zip_path.is_file() and sum_path.is_file()):
            return None
        try:
            expected = parse_checksum(
                sum_path.read_text(encoding="utf-8", errors="replace"),
                expected_filename=file.filename,
            )
            data = zip_path.read_bytes()
            verify_checksum(data, expected)
        except (OSError, BinanceArchiveError):
            for path in (zip_path, sum_path):
                path.unlink(missing_ok=True)
            return None
        return data

    def _write_cache(self, file: ArchiveFile, data: bytes, checksum_text: bytes) -> None:
        paths = self._cache_paths(file)
        if paths is None:
            return
        zip_path, sum_path = paths
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        for path, payload in ((zip_path, data), (sum_path, checksum_text)):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(payload)
            tmp.replace(path)


# --- import orchestration ---------------------------------------------------------


class CandleSink(Protocol):
    def upsert_final(self, candles: Iterable[Candle]) -> int: ...


@dataclass
class ImportResult:
    symbol: str
    interval: str
    window_start: datetime
    window_end: datetime
    files: int = 0  # files attempted (after monthly->daily fallback expansion)
    checksum_ok: int = 0
    checksum_failed: int = 0
    missing: int = 0  # HTTP 404 (not published / not listed)
    from_cache: int = 0
    rows: int = 0
    header_rows: int = 0
    malformed: int = 0
    not_final: int = 0
    final: int = 0
    inserted: int = 0
    errors: list[str] = field(default_factory=list)
    gaps: GapReport | None = None
    missing_vs_window: int = 0  # expected bars in window - unique bars in window


async def import_symbol(
    fetcher: ArchiveFetcher,
    sink: CandleSink,
    symbol: str,
    interval: Timeframe,
    *,
    now: datetime,
    months: int,
    batch_size: int = 2000,
) -> ImportResult:
    """Download, verify, parse and upsert one symbol. Per-file errors are recorded,
    never raised. A missing monthly zip falls back to that month's daily zips."""
    if not 1 <= batch_size <= MAX_UPSERT_BATCH:
        raise ValueError(f"batch_size must be 1..{MAX_UPSERT_BATCH}")
    validate_symbol(symbol)
    now_utc = _as_utc(now)
    window_start, window_end = archive_window(now_utc, months)
    result = ImportResult(
        symbol=symbol,
        interval=interval.value,
        window_start=window_start,
        window_end=window_end,
    )
    open_times: list[datetime] = []
    queue = plan_archive_files(symbol, interval, now=now_utc, months=months)
    today = now_utc.date()
    while queue:
        file = queue.pop(0)
        result.files += 1
        try:
            fetched = await fetcher.get(file)
        except ChecksumMismatchError:
            result.checksum_failed += 1
            result.errors.append(f"{file.filename}:checksum_mismatch")
            continue
        except (BinanceArchiveError, httpx.HTTPError, OSError) as exc:
            # Keep going; report class name only.
            result.errors.append(f"{file.filename}:{type(exc).__name__}")
            continue
        if fetched is None:
            result.missing += 1
            if file.kind == "monthly":
                year, month = (int(part) for part in file.period.split("-"))
                fallback = [
                    daily_file(symbol, interval, d)
                    for d in month_days(year, month, before=today)
                ]
                queue[0:0] = fallback
            continue
        result.checksum_ok += 1
        if fetched.from_cache:
            result.from_cache += 1
        try:
            text = extract_csv(fetched.data)
        except ArchiveFormatError as exc:
            result.errors.append(f"{file.filename}:{type(exc).__name__}")
            continue
        parsed = parse_kline_csv(text, symbol=symbol, interval=interval, now=now_utc)
        result.rows += parsed.rows
        result.header_rows += parsed.header_rows
        result.malformed += parsed.malformed
        result.not_final += parsed.not_final
        result.final += len(parsed.candles)
        for candle in parsed.candles:
            # Defence in depth: archive rows must never be stored as Toobit data.
            if candle.instrument.exchange != BINANCE_EXCHANGE:
                raise BinanceArchiveError("refusing to store non-binance archive candle")
            open_times.append(candle.open_time)
        for start in range(0, len(parsed.candles), batch_size):
            result.inserted += sink.upsert_final(parsed.candles[start : start + batch_size])
    result.gaps = count_gaps(open_times, interval)
    in_window = {t for t in open_times if window_start <= t < window_end}
    result.missing_vs_window = expected_bars(window_start, window_end, interval) - len(in_window)
    return result


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
