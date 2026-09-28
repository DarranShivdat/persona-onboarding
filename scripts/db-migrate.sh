#!/usr/bin/env bash
# Apply infra/supabase/migrations/*.sql in filename order, idempotently.
#
#   scripts/db-migrate.sh "$PERSONA_DATABASE_URL"            # apply pending
#   scripts/db-migrate.sh "$PERSONA_DATABASE_URL" --status   # list applied/pending, change nothing
#   scripts/db-migrate.sh "$PERSONA_DATABASE_URL" --baseline # record all as applied WITHOUT running
#                                                            # (only for a DB already built by the
#                                                            #  test helper apply_migrations())
#
# Tracking table: public.persona_schema_migrations(version, checksum, applied_at). Each file runs
# in ONE transaction together with its tracking row (psql --single-transaction), so a failed
# migration leaves nothing half-applied. Re-running is a no-op. A changed checksum for an
# already-applied file is reported (never re-applied).
#
# Supabase: use the direct connection (db.<ref>.supabase.co:5432) or the *session* pooler
# (:5432). The transaction pooler (:6543) also works for this plain DDL but is not recommended.
# Append `?sslmode=require` if the URL lacks it. The URL is never printed (only host/db).
# Env: MIGRATIONS_DIR (override dir, for tests), PSQL (psql binary).
set -euo pipefail

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 1 ] || usage
URL="$1"; MODE="${2:-apply}"
case "$MODE" in apply|--apply) MODE=apply ;; --status) MODE=status ;; --baseline) MODE=baseline ;; *) usage ;; esac
case "$URL" in postgres://*|postgresql://*) ;; *) echo "db-migrate: first arg must be a postgres:// URL" >&2; exit 2 ;; esac

HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${MIGRATIONS_DIR:-$HERE/../infra/supabase/migrations}"
PSQL="${PSQL:-psql}"
command -v "$PSQL" >/dev/null 2>&1 || { echo "db-migrate: psql not found (brew install libpq / postgresql)" >&2; exit 2; }

# host/db only — never the password
REDACTED="$(printf '%s' "$URL" | sed -E 's#^[a-z]+://([^@/]*@)?([^/?]*)(/[^?]*)?.*#\2\3#')"
echo "db-migrate: target ${REDACTED} (${MODE})"

q() { PGCONNECT_TIMEOUT=10 "$PSQL" "$URL" -X -q -v ON_ERROR_STOP=1 -At "$@"; }

q -c "set client_min_messages = warning; create table if not exists public.persona_schema_migrations (
        version    text primary key,
        checksum   text not null,
        applied_at timestamptz not null default now())" >/dev/null

sha() { shasum -a 256 "$1" | awk '{print $1}'; }

applied_count="$(q -c "select count(*) from public.persona_schema_migrations")"
if [ "$MODE" = apply ] && [ "$applied_count" = 0 ] && \
   [ "$(q -c "select to_regclass('public.sessions') is not null")" = t ]; then
  echo "db-migrate: schema exists but no tracking rows (built by apply_migrations?)." >&2
  echo "            If it matches the migration files, run again with --baseline." >&2
  exit 1
fi

n_applied=0; n_skipped=0; n_drift=0
found=0
for f in "$DIR"/*.sql; do
  [ -e "$f" ] || continue
  found=1
  v="$(basename "$f")"
  case "$v" in *[!A-Za-z0-9_.-]*) echo "db-migrate: bad filename $v" >&2; exit 2 ;; esac
  sum="$(sha "$f")"
  have="$(q -c "select checksum from public.persona_schema_migrations where version = '$v'")"
  if [ -n "$have" ]; then
    if [ "$have" != "$sum" ]; then
      echo "  DRIFT    $v (applied checksum differs from file; not re-applied — add a new migration)"
      n_drift=$((n_drift + 1))
    else
      [ "$MODE" = status ] && echo "  applied  $v"
    fi
    n_skipped=$((n_skipped + 1))
    continue
  fi
  case "$MODE" in
    status) echo "  PENDING  $v" ;;
    baseline)
      q -c "insert into public.persona_schema_migrations(version, checksum) values ('$v', '$sum')" >/dev/null
      echo "  baseline $v"; n_applied=$((n_applied + 1)) ;;
    apply)
      echo "  apply    $v"
      q --single-transaction -f "$f" \
        -c "insert into public.persona_schema_migrations(version, checksum) values ('$v', '$sum')" >/dev/null \
        || { echo "db-migrate: FAILED $v — rolled back, nothing recorded" >&2; exit 1; }
      n_applied=$((n_applied + 1)) ;;
  esac
done
[ "$found" = 1 ] || { echo "db-migrate: no *.sql in $DIR" >&2; exit 2; }

echo "db-migrate: done — ${n_applied} applied, ${n_skipped} already applied, ${n_drift} drifted"
[ "$n_drift" = 0 ]
