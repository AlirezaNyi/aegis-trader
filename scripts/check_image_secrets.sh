#!/usr/bin/env sh
# Smoke: ensure tracked deploy files do not embed non-empty secret assignments.
# Handles KEY=value and YAML KEY: value. Placeholders and empty values are OK.
# Does not read .env (local secrets).
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FAIL=0

SECRET_KEYS='TOOBIT_API_KEY|TOOBIT_API_SECRET|TYPESAFE_API_KEY|AEGIS_LLM_API_KEY'

# Print non-placeholder assignments; return 0 if any found.
find_bad_assignments() {
  file="$1"
  # shellcheck disable=SC2016
  grep -nE "(${SECRET_KEYS})[[:space:]]*[:=][[:space:]]*" "$file" 2>/dev/null | while IFS= read -r line; do
    # Strip KEY= / KEY:
    value=$(printf '%s\n' "$line" | sed -E "s/^[0-9]+:[[:space:]]*(${SECRET_KEYS})[[:space:]]*[:=][[:space:]]*//")
    value=$(printf '%s\n' "$value" | sed -E 's/^["'\'']//; s/["'\'']$//; s/[[:space:]]*$//')
    case "$value" in
      '' | '""' | "''") continue ;;
    esac
    lower=$(printf '%s\n' "$value" | tr '[:upper:]' '[:lower:]')
    case "$lower" in
      *changeme* | *placeholder* | *your_* | *example* | *xxxxx* | *dummy*) continue ;;
    esac
    printf '%s\n' "$line"
  done
}

check_assignments() {
  file="$1"
  hits=$(find_bad_assignments "$file" || true)
  if [ -n "$hits" ]; then
    printf '%s\n' "$hits" >&2
    echo "FAIL: possible secret material in $file" >&2
    FAIL=1
  else
    echo "OK: $file"
  fi
}

check_assignments docker-compose.yml
check_assignments .env.example
check_assignments Dockerfile

if [ -f .env ]; then
  echo "NOTE: .env is local-only; never commit; Dockerfile does not COPY .env."
fi

if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
echo "OK: secret absence smoke passed"
