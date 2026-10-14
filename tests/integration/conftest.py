"""Opt-in integration tests. Every test here is skipped unless explicitly enabled.

* ``AEGIS_TEST_DATABASE_URL`` — real Postgres at alembic head (never the prod DB).
* ``AEGIS_TEST_PUBLIC_NETWORK=1`` — public Toobit market-data endpoints.
* ``AEGIS_TEST_TOOBIT_ORDER_TEST=1`` — signed spot ``orderTest`` only (Toobit has no
  verified testnet; orderTest validates an order without matching or moving funds).
"""

from __future__ import annotations

import os

import pytest


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _ = config
    db_url = os.environ.get("AEGIS_TEST_DATABASE_URL", "").strip()
    gates = {
        "integration_db": (bool(db_url), "set AEGIS_TEST_DATABASE_URL to run"),
        "integration_public": (
            _flag("AEGIS_TEST_PUBLIC_NETWORK"),
            "set AEGIS_TEST_PUBLIC_NETWORK=1 to run",
        ),
        "integration_order_test": (
            _flag("AEGIS_TEST_TOOBIT_ORDER_TEST"),
            "set AEGIS_TEST_TOOBIT_ORDER_TEST=1 to run",
        ),
    }
    for item in items:
        for marker, (enabled, reason) in gates.items():
            if marker in item.keywords and not enabled:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture
def test_database_url() -> str:
    return os.environ["AEGIS_TEST_DATABASE_URL"]
