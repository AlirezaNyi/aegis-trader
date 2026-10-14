"""Postgres integration: schema at head and candle store round-trip (rolled back)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from aegis.db.session import create_engine_from_url
from aegis.market_data.candle_store import build_upsert_statement, final_row_values
from aegis.schemas.common import MarketType, Timeframe
from aegis.schemas.market import Candle, InstrumentRef

pytestmark = pytest.mark.integration_db

_ROOT = Path(__file__).resolve().parents[2]


def test_database_is_at_alembic_head(test_database_url: str) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    engine = create_engine_from_url(test_database_url)
    with engine.connect() as conn:
        current = {row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))}
    assert current == heads
    tables = set(inspect(engine).get_table_names())
    for required in ("market_candles", "risk_period_state", "orders", "fills", "positions"):
        assert required in tables


def test_candle_upsert_roundtrip_is_rolled_back(test_database_url: str) -> None:
    engine = create_engine_from_url(test_database_url)
    instrument = InstrumentRef(
        exchange="integration-test", market_type=MarketType.SPOT, symbol="TESTUSDT"
    )
    start = datetime(2020, 1, 1, tzinfo=UTC)
    candles = [
        Candle(
            instrument=instrument,
            interval=Timeframe.M5,
            open_time=start + timedelta(minutes=5 * i),
            close_time=start + timedelta(minutes=5 * i + 5) - timedelta(milliseconds=1),
            open=Decimal("1"),
            high=Decimal("1.1"),
            low=Decimal("0.9"),
            close=Decimal("1.05"),
            volume=Decimal("10"),
            is_final=True,
            source="integration-test",
            received_at=start + timedelta(minutes=5 * i + 5),
        )
        for i in range(3)
    ]
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            rows = final_row_values(candles)
            first = conn.execute(build_upsert_statement(rows).returning(text("id"))).fetchall()
            again = conn.execute(build_upsert_statement(rows).returning(text("id"))).fetchall()
            assert len(first) == 3
            assert again == []  # ON CONFLICT DO NOTHING: history never overwritten
        finally:
            trans.rollback()
