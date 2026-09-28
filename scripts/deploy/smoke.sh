#!/usr/bin/env bash
# Post-deploy smoke test (read-mostly; creates one throwaway onboarding session).
#
#   bash scripts/deploy/smoke.sh <web-url> <agent-url>
#   e.g. bash scripts/deploy/smoke.sh https://persona-onboarding.vercel.app https://persona-onboarding-agent.fly.dev
#
# Checks: agent /health (+db ok), web home page, session create via the web proxy (cookie),
# one text turn via the proxy, direct agent session + turn, ICE route (if present), Google OAuth
# start redirect (Location only — never follows it, no login). Prints PASS/FAIL per check and
# exits 1 on any FAIL. SKIP = optional check whose route does not exist yet.
# Env: SMOKE_ICE_PATHS (space-separated URLs to try for the ICE route), SMOKE_TIMEOUT (s, 15).
set -uo pipefail

[ $# -eq 2 ] || { sed -n '2,11p' "$0"; exit 2; }
WEB="${1%/}"; AGENT="${2%/}"
T="${SMOKE_TIMEOUT:-15}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
JAR="$TMP/jar"
FAILS=0; PASSES=0

ok()   { PASSES=$((PASSES + 1)); echo "PASS  $1"; }
bad()  { FAILS=$((FAILS + 1));   echo "FAIL  $1${2:+ — $2}"; }
skip() { echo "SKIP  $1${2:+ — $2}"; }

# req METHOD URL [DATA] -> sets CODE, BODY=$TMP/body, HDRS=$TMP/hdrs (never follows redirects)
req() {
  local data=()
  [ $# -ge 3 ] && data=(-H 'content-type: application/json' --data "$3")
  CODE="$(curl -sS -o "$TMP/body" -D "$TMP/hdrs" -w '%{http_code}' --max-time "$T" \
          -b "$JAR" -c "$JAR" -X "$1" ${data[@]+"${data[@]}"} "$2" 2>"$TMP/err")" || CODE="000"
}
json() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" "$TMP/body" "$1" 2>/dev/null; }

echo "smoke: web=$WEB agent=$AGENT"

# 1. agent health
req GET "$AGENT/health"
if [ "$CODE" = 200 ] && [ "$(json "d.get('ok')")" = True ]; then
  db="$(json "d.get('db')")"
  if [ "$db" = ok ]; then ok "agent /health (db ok, flow v$(json "d.get('flow_version')"), sha $(json "d.get('git_sha')"))"
  else bad "agent /health" "db=$db"; fi
else bad "agent /health" "HTTP $CODE $(head -c 200 "$TMP/err")"; fi

# 2. web home
req GET "$WEB/"
if [ "$CODE" = 200 ]; then ok "web / (build $(grep -o 'name="build-sha" content="[^"]*' "$TMP/body" | sed 's/.*content="//' | cut -c1-12))"
else bad "web /" "HTTP $CODE"; fi

# 3. session create through the web proxy (sets the httpOnly cookie)
req POST "$WEB/api/session" '{}'
if [ "$CODE" = 201 ] || [ "$CODE" = 200 ]; then
  if grep -q persona_session "$JAR" 2>/dev/null; then ok "web POST /api/session → $CODE (node $(json "d['state'].get('node')"))"
  else bad "web POST /api/session" "no session cookie set"; fi
else bad "web POST /api/session" "HTTP $CODE $(head -c 200 "$TMP/body")"; fi

# 4. one text turn through the proxy
req POST "$WEB/api/session/turns" '{"text":"hi, this is a deploy smoke test"}'
if [ "$CODE" = 200 ] && [ -n "$(json "d.get('reply') or d.get('state')")" ]; then ok "web POST /api/session/turns → reply"
else bad "web POST /api/session/turns" "HTTP $CODE $(head -c 200 "$TMP/body")"; fi

# 5. direct agent session + turn (bearer token) — proves the agent API independent of the proxy
req POST "$AGENT/v1/sessions" '{}'
if [ "$CODE" = 201 ]; then
  SID="$(json "d['id']")"; TOK="$(json "d['token']")"
  CODE="$(curl -sS -o "$TMP/body" -w '%{http_code}' --max-time "$T" -X POST -H "authorization: Bearer $TOK" \
          -H 'content-type: application/json' --data '{"text":"hello"}' "$AGENT/v1/sessions/$SID/turns" 2>/dev/null)" || CODE=000
  if [ "$CODE" = 200 ]; then ok "agent POST /v1/sessions + /turns"; else bad "agent /v1/sessions/{id}/turns" "HTTP $CODE"; fi
else bad "agent POST /v1/sessions" "HTTP $CODE"; fi

# 6. ICE route (browser + server must share TURN; ADR 0001). Optional until the route exists.
ICE_PATHS="${SMOKE_ICE_PATHS:-$AGENT/v1/ice $AGENT/api/ice $WEB/api/session/ice $WEB/api/ice}"
ice_done=0
for u in $ICE_PATHS; do
  req GET "$u"
  if [ "$CODE" = 200 ]; then
    n="$(json "len(d.get('iceServers', d.get('ice_servers', d)) if not isinstance(d, list) else d)")"
    relay="$(python3 -c "import json,sys; print('turn' in open(sys.argv[1]).read())" "$TMP/body")"
    if [ "$relay" = True ]; then ok "ICE $u ($n servers, TURN present)"; else bad "ICE $u" "no turn: URLs (TURN not configured?)"; fi
    ice_done=1; break
  elif [ "$CODE" = 401 ] || [ "$CODE" = 403 ]; then
    ok "ICE $u exists (auth required: $CODE)"; ice_done=1; break
  fi
done
[ "$ice_done" = 1 ] || skip "ICE route" "none of: $ICE_PATHS (set SMOKE_ICE_PATHS once VOICE-005 lands)"

# 7. Google OAuth start: redirect to Google with our client + callback; never followed.
req GET "$WEB/api/oauth/google/start"
LOC="$(tr -d '\r' < "$TMP/hdrs" | sed -n 's/^[Ll]ocation: //p' | head -1)"
case "$CODE:$LOC" in
  30[27]:https://accounts.google.com/*)
    if printf '%s' "$LOC" | grep -q 'client_id=' && printf '%s' "$LOC" | grep -q 'redirect_uri=https%3A%2F%2F'; then
      cb="$(python3 -c "import sys,urllib.parse as u; print(u.parse_qs(u.urlsplit(sys.argv[1]).query)['redirect_uri'][0])" "$LOC")"
      ok "OAuth start → accounts.google.com (redirect_uri $cb)"
      case "$cb" in "$WEB"/api/oauth/google/callback) ;; *) echo "      WARN redirect_uri host differs from $WEB — must match Google Cloud + GOOGLE_OAUTH_REDIRECT_URL" ;; esac
    else bad "OAuth start" "Location lacks client_id or https redirect_uri"; fi ;;
  *) bad "OAuth start" "HTTP $CODE, Location=${LOC:-none} (oauth_unconfigured? check GOOGLE_OAUTH_* on Vercel)" ;;
esac

echo
if [ "$FAILS" = 0 ]; then echo "SMOKE PASS ($PASSES checks)"; exit 0; fi
echo "SMOKE FAIL ($FAILS failed, $PASSES passed)"; exit 1
