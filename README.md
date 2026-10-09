# Aegis

Personal autonomous trading system for Toobit Spot and Futures. **Paper trading is the default. Live trading is disabled until separately approved.**

Package and service identity: `aegis`.

Product and architecture docs live under [`docs/`](docs/). Phase guides live under [`Phases/`](Phases/).

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
- Readiness: `GET /ready`

## Configuration

See [`.env.example`](.env.example) and [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

| Variable | Default | Notes |
| --- | --- | --- |
| `AEGIS_TRADING_MODE` | `paper` | `development`, `paper`, or `live` |
| `AEGIS_LIVE_ARMED` | `false` | Must be true for live execution |
| `AEGIS_KILL_SWITCH` | `false` | When true, blocks new orders |
| `DATABASE_URL` | local compose URL | Required for DB-backed readiness |

Live order submission requires `trading_mode=live`, `live_armed=true`, and `kill_switch=false`. Phase 1 does not construct a live exchange submit client.

## Interfaces

Protocols under `aegis.interfaces` define ports for market data, execution, Jev, LLM, persistence, and clock. Phase 1 ships stubs only—no real Toobit, Jev, or LLM HTTP clients.

## Migrations

```bash
alembic upgrade head
```

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
- The deterministic Risk Engine (later phases) is the only authority for approving new trades.
