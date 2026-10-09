FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AEGIS_TRADING_MODE=paper \
    AEGIS_LIVE_ARMED=false \
    AEGIS_KILL_SWITCH=false

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "aegis.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
