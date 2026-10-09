#!/usr/bin/env sh
# Restore a pg_dump custom-format file into Compose Postgres.
# WARNING: replaces database contents. Keep live disarmed. Test on non-prod first.
# Usage: ./scripts/restore_postgres.sh path/to/aegis_postgres_*.dump
set -eu

DUMP="${1:?usage: $0 path/to/dump}"
if [ ! -f "$DUMP" ]; then
  echo "dump not found: $DUMP" >&2
  exit 1
fi

echo "Restoring ${DUMP} into compose service postgres (database aegis)..."
# Drop/recreate public schema objects via --clean for custom format.
docker compose exec -T postgres pg_restore -U aegis -d aegis --clean --if-exists <"$DUMP" || {
  echo "pg_restore exited non-zero; inspect errors (some drop notices are expected)." >&2
  exit 1
}
echo "Restore finished. Next: docker compose restart aegis; verify /ready; run reconcile; keep live disarmed."
