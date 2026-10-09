# Aegis

Personal autonomous trading system for Toobit Spot and Futures. **Paper trading is the default. Live trading is disabled until separately approved.**

Package and service identity: `aegis`.

Repository: [AlirezaNyi/aegis-trader](https://github.com/AlirezaNyi/aegis-trader)

Product and architecture docs live under [`docs/`](docs/). Phase guides live under [`Phases/`](Phases/).

## Current phase

Phase 3 — deterministic Feature Engine (`features-v1`), five specialized analysts, and Evidence Package builder. Jev, LLM Supervisor, Risk Engine, and live order submission remain disabled / stubbed.

## Requirements

- Python 3.12+
- Docker (optional, for Compose + Postgres)

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

If `files.pythonhosted.org` does not resolve on your network, install via a mirror, for example:

```bash
pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
```

Run the API (without Postgres, readiness reports DB as not connected):

```bash
uvicorn aegis.main:app --reload --app-dir src
```

Or with Compose (app + Postgres):

```bash
docker compose up --build
```

- Liveness: `GET /health`
- Readiness: `GET /ready` (reports `"phase": 3`)

## Configuration

See [`.env.example`](.env.example) and [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

| Variable | Default | Notes |
| --- | --- | --- |
| `AEGIS_TRADING_MODE` | `paper` | `development`, `paper`, or `live` |
| `AEGIS_LIVE_ARMED` | `false` | Must be true for live execution |
| `AEGIS_KILL_SWITCH` | `false` | When true, blocks new orders |
| `AEGIS_ANALYST_TIMEOUT_SECONDS` | `2` | Per-analyst timeout |
| `AEGIS_ANALYST_CONCURRENCY` | `5` | Max concurrent analysts (1–5) |
| `DATABASE_URL` | local compose URL | Required for DB-backed readiness |

Live order submission requires `trading_mode=live`, `live_armed=true`, and `kill_switch=false`. No live exchange submit client is constructed.

## Implemented modules

- **Market data (Phase 2):** Toobit public REST/WS adapters, quality checks, fixtures — see [`src/aegis/market_data/LIMITATIONS.md`](src/aegis/market_data/LIMITATIONS.md).
- **Features & analysts (Phase 3):** [`docs/FEATURES.md`](docs/FEATURES.md), [`src/aegis/analysts/LIMITATIONS.md`](src/aegis/analysts/LIMITATIONS.md).
- **Interfaces:** Protocols under `aegis.interfaces` for market data, execution, Jev, LLM, persistence, and clock. Execution, Jev, and LLM remain stubs until later phases.

## Migrations

```bash
alembic upgrade head
```

Phase 3 adds `feature_snapshots`, `analyst_evidence`, `evidence_packages`, and `strategy_experiments` (alongside Phase 1 `audit_events`).

## Tests and static checks

```bash
pytest
ruff check src tests
mypy src
```

## Safety

- No exchange withdrawal support.
- Secrets must not be committed.
- LLMs and Jev never receive exchange credentials.
- Analysts never submit orders or approve trades.
- The deterministic Risk Engine (later phases) is the only authority for approving new trades.
