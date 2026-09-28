#!/usr/bin/env bash
# Deploy apps/web to Vercel (production), from the REPO ROOT with Root Directory = apps/web.
#
#   bash scripts/deploy/vercel-web.sh                # DRY RUN: print every command (default)
#   bash scripts/deploy/vercel-web.sh --apply        # run them (EM, after Darran's go-ahead)
# Options: --env-file PATH (default .persona-deploy/web.env), --project NAME, --scope TEAM,
#          --no-preview (production env only), --env-only, --deploy-only,
#          --skip-audit (deploy without the qa:audit gate; prints a loud warning)
#
# Why repo root: apps/web's prebuild reads docs/design/tokens.json and the npm workspace
# lockfile lives at the root, so the upload must include the whole repo (Vercel builds only
# the Root Directory). NEXT_PUBLIC_* values are inlined at build: set env BEFORE deploying.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

parse_apply "$@"
ENV_FILE="$ROOT/.persona-deploy/web.env"
PROJECT="persona-onboarding-darran"  # persona-onboarding.vercel.app is taken by a third party (checked 2026-09-27)
SCOPE=""
ENVS="production preview"
DO_LINK=1; DO_ENV=1; DO_DEPLOY=1; SKIP_AUDIT=0
set -- $REST
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift ;;
    --project) PROJECT="$2"; shift ;;
    --scope) SCOPE="$2"; shift ;;
    --no-preview) ENVS="production" ;;
    --env-only) DO_LINK=0; DO_DEPLOY=0 ;;
    --deploy-only) DO_LINK=0; DO_ENV=0 ;;
    --skip-audit) SKIP_AUDIT=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
VC="npx --yes vercel@latest"
SCOPE_ARGS=""
[ -n "$SCOPE" ] && SCOPE_ARGS="--scope $SCOPE"

banner "vercel-web ($PROJECT)"

step "Gate: button audit (npm run qa:audit, LOCAL stub, offline)"
audit_gate "$SKIP_AUDIT"
need_tool npx "Node 22+"
cd "$ROOT"

step "Env contract for the web app (names only, local check — no network)"
check_env_file web "$ENV_FILE" apps/web/.env.example

step "Vercel account (interactive login is manual)"
manual "npx vercel@latest login   # once, by the account owner"
run $VC whoami $SCOPE_ARGS

if [ "$DO_LINK" = 1 ]; then
  step "Link the repo root to the project (creates it on first link)"
  run $VC link --yes --project "$PROJECT" $SCOPE_ARGS
  # vercel link may auto-write a multi-service vercel.json at the repo root (web + agent); we deploy
  # web only (agent is on Fly) and .vercelignore keeps it out of the upload — remove it.
  [ "$APPLY" = 1 ] && git -C "$ROOT" ls-files --error-unmatch vercel.json >/dev/null 2>&1 || rm -f "$ROOT/vercel.json"
  step "Project settings: Root Directory = apps/web, Next.js, Node 22 (upload = repo root, see .vercelignore)"
  run $VC project update "$PROJECT" --root-directory apps/web --framework nextjs --node-version 22.x --yes $SCOPE_ARGS
fi

if [ "$DO_ENV" = 1 ]; then
  step "Env vars for: $ENVS (values piped from the env file, never printed)"
  if [ -f "$ENV_FILE" ]; then
    NAMES="$(python3 "$ROOT/scripts/check-env.py" --names web --env-file "$ENV_FILE")"
  else
    NAMES="PERSONA_AGENT_BASE_URL PERSONA_INTERNAL_SECRET GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET GOOGLE_OAUTH_REDIRECT_URL NEXT_PUBLIC_PERSONA_ICE_URLS"
    echo "  (no env file: showing the standard set)"
  fi
  for name in $NAMES; do
    for e in $ENVS; do
      # rm first so re-runs update values (add fails on an existing var); ignore "not found".
      run_desc "$VC env rm $name $e --yes $SCOPE_ARGS   (ignore 'not found')" \
        bash -c "$VC env rm \"\$1\" \"\$2\" --yes $SCOPE_ARGS >/dev/null 2>&1 || true" _ "$name" "$e"
      run_desc "<value of $name from $(basename "$ENV_FILE")> | $VC env add $name $e $SCOPE_ARGS" \
        bash -c "python3 \"\$1/scripts/check-env.py\" --emit web --env-file \"\$2\" | sed -n \"s/^\$3=//p\" | tr -d '\n' | $VC env add \"\$3\" \"\$4\" $SCOPE_ARGS" \
        _ "$ROOT" "$ENV_FILE" "$name" "$e"
    done
  done
  run $VC env ls $SCOPE_ARGS
fi

if [ "$DO_DEPLOY" = 1 ]; then
  step "Production deploy (build on Vercel; build id passed explicitly — CLI deploys have no git SHA)"
  run $VC deploy --prod --yes --build-env "PERSONA_BUILD_SHA=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)" $SCOPE_ARGS
  manual "note the production domain (e.g. https://$PROJECT.vercel.app); it must match GOOGLE_OAUTH_REDIRECT_URL"
  manual "and the Authorized redirect URI in Google Cloud (scripts/deploy/google-oauth.md)."
  echo "  then: bash scripts/deploy/smoke.sh https://<domain> https://<fly-app>.fly.dev"
fi

step "Rollback (reference)"
echo "  $VC ls $PROJECT            # previous deployments"
echo "  $VC rollback [<deployment-url>]   # instant alias swap back to a previous production deploy"
echo
if [ "$APPLY" = 1 ]; then echo "vercel-web: applied."; else echo "vercel-web: dry run complete (0 commands executed)."; fi
