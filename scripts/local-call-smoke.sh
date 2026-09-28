#!/usr/bin/env bash
# VOICE-005 local end-to-end call proof (offline: no vendor keys, no cloud, no public STUN).
#
#   scripts/local-call-smoke.sh            # build web, bring the stack up, run the spec, tear down
#   SMOKE_SKIP_BUILD=1 scripts/local-call-smoke.sh   # reuse apps/web/.next
#
# Stack: throwaway Postgres cluster (initdb, db persona_voice005) -> agent
# (`python -m agent.main`, FakeLlm, PERSONA_VOICE_FAKE_VENDORS=1, host-only ICE) on :8300 ->
# `next start` on :3300 -> Playwright Chromium (--use-fake-device-for-media-stream) running
# apps/web/e2e/real-agent-call.spec.ts. Vendor key env vars are unset for the agent.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PERSONA_PYTHON:-python3}"
AGENT_PORT="${SMOKE_AGENT_PORT:-8300}"
WEB_PORT="${SMOKE_WEB_PORT:-3300}"
PG_PORT="${SMOKE_PG_PORT:-55305}"
DB=persona_voice005
TMP="$(mktemp -d "${TMPDIR:-/tmp}/persona-voice005.XXXXXX")"
LOGS="$ROOT/.persona-qa/local-call-smoke"
mkdir -p "$LOGS"
PIDS=()

cleanup() {
  local code=$?
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
  done
  [ -d "$TMP/pg" ] && pg_ctl -D "$TMP/pg" -m fast stop >/dev/null 2>&1 || true
  rm -rf "$TMP"
  if [ $code -eq 0 ]; then echo "SMOKE PASS"; else echo "SMOKE FAIL (exit $code) — logs in $LOGS"; fi
  exit $code
}
trap cleanup EXIT INT TERM

wait_http() { # url, seconds
  for _ in $(seq 1 "$2"); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "timeout waiting for $1" >&2; return 1
}

for port in "$AGENT_PORT" "$WEB_PORT" "$PG_PORT"; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then echo "port $port is busy" >&2; exit 1; fi
done

echo "== postgres (throwaway cluster, :$PG_PORT, db $DB)"
initdb -D "$TMP/pg" -A trust -U postgres --no-sync >"$LOGS/initdb.log" 2>&1
pg_ctl -D "$TMP/pg" -w -l "$LOGS/postgres.log" \
  -o "-p $PG_PORT -k $TMP -c listen_addresses=127.0.0.1 -c fsync=off" start >/dev/null
createdb -h 127.0.0.1 -p "$PG_PORT" -U postgres "$DB"
DSN="postgresql://postgres@127.0.0.1:$PG_PORT/$DB"
(cd "$ROOT/services/agent" && "$PY" -c "import sys; from agent.store.postgres import apply_migrations; apply_migrations(sys.argv[1])" "$DSN")

echo "== agent :$AGENT_PORT (fake vendors, FakeLlm)"
(cd "$ROOT/services/agent" && exec env -u DEEPGRAM_API_KEY -u CARTESIA_API_KEY -u ANTHROPIC_API_KEY \
  -u CLOUDFLARE_TURN_KEY_ID -u CLOUDFLARE_TURN_API_TOKEN -u PERSONA_TURN_URLS -u PERSONA_TRACING \
  PERSONA_DATABASE_URL="$DSN" PERSONA_VOICE_FAKE_VENDORS=1 PERSONA_STUN_URLS=none \
  PERSONA_INTERNAL_SECRET=local-smoke-not-a-secret PERSONA_CALL_GRACE_S=3 \
  "$PY" -m agent.main --host 127.0.0.1 --port "$AGENT_PORT") >"$LOGS/agent.log" 2>&1 &
PIDS+=($!)
wait_http "http://127.0.0.1:$AGENT_PORT/health" 60
echo "   /health (during warmup): $(curl -fsS "http://127.0.0.1:$AGENT_PORT/health")"
grep -E "env: " "$LOGS/agent.log" | sed 's/^.*| - /   /' || true

cd "$ROOT/apps/web"
if [ "${SMOKE_SKIP_BUILD:-0}" != "1" ]; then
  echo "== next build"
  NEXT_PUBLIC_PERSONA_ICE_URLS=none npm run build >"$LOGS/next-build.log" 2>&1
fi
echo "== next start :$WEB_PORT"
PERSONA_AGENT_BASE_URL="http://127.0.0.1:$AGENT_PORT" PERSONA_INTERNAL_SECRET=local-smoke-not-a-secret \
  npx next start -p "$WEB_PORT" >"$LOGS/next.log" 2>&1 &
PIDS+=($!)
wait_http "http://localhost:$WEB_PORT/" 60

echo "== playwright (Chromium, fake media)"
PERSONA_WEB_URL="http://localhost:$WEB_PORT" PERSONA_E2E_AGENT_URL="http://127.0.0.1:$AGENT_PORT" \
  PERSONA_E2E_REAL_AGENT_URL="http://127.0.0.1:$AGENT_PORT" \
  npx playwright test e2e/real-agent-call.spec.ts --project=desktop --reporter=list

echo "== agent call log"
grep -E "call teardown|SDP answer|call_setup|ERROR" "$LOGS/agent.log" | sed 's/^.*| - /   /' || true
