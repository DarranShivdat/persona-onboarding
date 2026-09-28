#!/usr/bin/env bash
# Supabase Postgres for the agent: project creation is MANUAL (dashboard); this script prints the
# steps and, with --apply, runs the migrations against PERSONA_DATABASE_URL from the agent env file.
#
#   bash scripts/deploy/supabase-db.sh               # DRY RUN: print steps + commands
#   bash scripts/deploy/supabase-db.sh --apply       # migrate (idempotent) + show status
# Options: --env-file PATH (default .persona-deploy/agent.env)
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

parse_apply "$@"
ENV_FILE="$ROOT/.persona-deploy/agent.env"
set -- $REST
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

banner "supabase-db"
need_tool psql "brew install libpq && brew link --force libpq (or postgresql)"

step "Create the project (manual, ~2 min)"
manual "supabase.com/dashboard → New project: name persona-onboarding, region West US (N. California)"
manual "  (closest to Fly sjc), generate a strong DB password and keep it in the password manager."
manual "Plan: Free is fine for the trial; if on Pro, set Spend Cap = ON (Org → Billing)."
manual "Project Settings → Data API: turn OFF 'Enable Data API' (the agent uses Postgres directly;"
manual "  migration 0004 also enables RLS on every table so the anon key can read nothing)."

step "Connection string → PERSONA_DATABASE_URL (in $(basename "$ENV_FILE"), never committed)"
manual "Connect → Session pooler (IPv4, port 5432): postgresql://postgres.<ref>:<pw>@aws-0-us-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
manual "  Session pooler, not the transaction pooler (:6543): the agent keeps a psycopg pool with"
manual "  prepared statements; the direct host db.<ref>.supabase.co is IPv6-only unless the IPv4 add-on."

step "Migrations (idempotent; tracking table persona_schema_migrations)"
if [ -f "$ENV_FILE" ]; then
  if python3 "$ROOT/scripts/check-env.py" --names agent --env-file "$ENV_FILE" | grep -qx PERSONA_DATABASE_URL; then
    echo "  PERSONA_DATABASE_URL: present in $(basename "$ENV_FILE")"
  else
    echo "  PERSONA_DATABASE_URL: MISSING in $(basename "$ENV_FILE")"
    if [ "$APPLY" = 1 ]; then exit 1; fi
  fi
else
  echo "  env file $ENV_FILE not found (dry run continues)"
  if [ "$APPLY" = 1 ]; then exit 1; fi
fi
run_desc "scripts/db-migrate.sh \"\$PERSONA_DATABASE_URL\"" \
  bash -c 'bash "$1/scripts/db-migrate.sh" "$(python3 "$1/scripts/check-env.py" --emit agent --env-file "$2" | sed -n "s/^PERSONA_DATABASE_URL=//p")"' _ "$ROOT" "$ENV_FILE"
run_desc "scripts/db-migrate.sh \"\$PERSONA_DATABASE_URL\" --status" \
  bash -c 'bash "$1/scripts/db-migrate.sh" "$(python3 "$1/scripts/check-env.py" --emit agent --env-file "$2" | sed -n "s/^PERSONA_DATABASE_URL=//p")" --status' _ "$ROOT" "$ENV_FILE"

step "Rollback (reference)"
echo "  Migrations are forward-only. Supabase → Database → Backups (daily on Pro; PITR add-on)."
echo "  Trial data is disposable: worst case, drop + recreate the project and re-run this script."
echo
if [ "$APPLY" = 1 ]; then echo "supabase-db: applied."; else echo "supabase-db: dry run complete (0 commands executed)."; fi
