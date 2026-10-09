#!/usr/bin/env sh
# Backup Compose Postgres to a timestamped dump. Retention count is UNAPPROVED.
# Usage: ./scripts/backup_postgres.sh [output_dir]
set -eu

OUT_DIR="${1:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT_DIR"
FILE="${OUT_DIR}/aegis_postgres_${STAMP}.dump"

echo "Writing backup to ${FILE}"
docker compose exec -T postgres pg_dump -U aegis -d aegis -Fc >"$FILE"
echo "Backup complete: ${FILE}"
echo "After any restore: run reconciliation and keep AEGIS_LIVE_ARMED=false."
