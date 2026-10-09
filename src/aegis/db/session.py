"""SQLAlchemy engine helpers for readiness checks."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def create_engine_from_url(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def check_database(database_url: str, *, timeout_seconds: float = 2.0) -> tuple[bool, str]:
    """Return (ok, detail) for readiness probes."""
    try:
        engine = create_engine_from_url(database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception as exc:  # noqa: BLE001 — readiness must never crash the process
        return False, f"database unreachable: {exc.__class__.__name__}"
    _ = timeout_seconds
    return True, "database ok"
