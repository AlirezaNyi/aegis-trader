"""Timestamp helpers — Toobit uses millisecond epoch times."""

from __future__ import annotations

from datetime import UTC, datetime


def ms_to_datetime(value: int | float | str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)


def datetime_to_ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def interval_to_milliseconds(interval: str) -> int:
    mapping = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
    }
    try:
        return mapping[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported interval for Aegis: {interval!r}") from exc
