#!/usr/bin/env bash
# Deploy services/agent to Fly.io (ADR 0001: sjc, 1 always-on machine, Cloudflare TURN).
#
#   bash scripts/deploy/fly-agent.sh                 # DRY RUN: print every command (default)
#   bash scripts/deploy/fly-agent.sh --apply         # run them (EM, after Darran's go-ahead)
# Options: --env-file PATH (default .persona-deploy/agent.env), --app NAME, --org SLUG,
#          --skip-create (app already exists), --secrets-only, --deploy-only
#
# Needs: flyctl (`brew install flyctl`), a Fly login (`fly auth login` — interactive, done by
# Darran/EM), and a filled agent env file (template: services/agent/.env.example).
# The image is built by Fly's remote builder (Docker is not needed locally).
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

parse_apply "$@"
ENV_FILE="$ROOT/.persona-deploy/agent.env"
APP="persona-onboarding-agent"
ORG="personal"
DO_CREATE=1; DO_SECRETS=1; DO_DEPLOY=1
set -- $REST
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift ;;
    --app) APP="$2"; shift ;;
    --org) ORG="$2"; shift ;;
    --skip-create) DO_CREATE=0 ;;
    --secrets-only) DO_CREATE=0; DO_DEPLOY=0 ;;
    --deploy-only) DO_CREATE=0; DO_SECRETS=0 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done
FLY_TOML="$ROOT/infra/fly.toml"
TOML_APP="$(sed -n 's/^app = "\(.*\)"/\1/p' "$FLY_TOML")"

banner "fly-agent ($APP)"
need_tool fly "brew install flyctl"
cd "$ROOT"

step "Env contract for the agent (names only, local check — no network)"
check_env_file agent "$ENV_FILE" services/agent/.env.example
if [ "$APP" != "$TOML_APP" ]; then
  echo "  NOTE: --app $APP differs from infra/fly.toml app=$TOML_APP; passing -a everywhere."
fi

step "Fly account (interactive login is manual)"
manual "fly auth login   # once, in a terminal, by the account owner"
run fly auth whoami

if [ "$DO_CREATE" = 1 ]; then
  step "Create the app (idempotent: skipped if it already exists)"
  if [ "$APPLY" = 1 ] && fly status -a "$APP" >/dev/null 2>&1; then
    echo "  app $APP exists — skipping create"
  else
    run fly apps create "$APP" --org "$ORG"
  fi
fi

if [ "$DO_SECRETS" = 1 ]; then
  step "Secrets: every non-empty agent var from the env file (values piped, never printed)"
  if [ -f "$ENV_FILE" ]; then
    echo "  names: $(python3 "$ROOT/scripts/check-env.py" --names agent --env-file "$ENV_FILE" | tr '\n' ' ')"
  fi
  run_desc "python3 scripts/check-env.py --emit agent --env-file $ENV_FILE | fly secrets import -a $APP --stage" \
    bash -c 'python3 "$1/scripts/check-env.py" --emit agent --env-file "$2" | fly secrets import -a "$3" --stage' _ "$ROOT" "$ENV_FILE" "$APP"
  run fly secrets list -a "$APP"
fi

if [ "$DO_DEPLOY" = 1 ]; then
  step "Build remotely + deploy (context = repo root; .dockerignore allowlists the agent only)"
  # --ha=false: exactly ONE machine — call pipelines live in-process, so a 2nd machine would split
  # POST /call and the WebRTC session across machines (fly defaults to 2 on first deploy).
  run fly deploy --remote-only --ha=false -c infra/fly.toml -a "$APP" --env "GIT_SHA=$(git_sha)" --wait-timeout 600 .
  step "Verify"
  run fly status -a "$APP"
  run curl -fsS --max-time 10 "https://$APP.fly.dev/health"
  echo "  then: bash scripts/deploy/smoke.sh <web-url> https://$APP.fly.dev"
fi

step "Rollback (reference)"
echo "  fly releases -a $APP                       # find the last good version / image"
echo "  fly deploy -a $APP -c infra/fly.toml --image <previous-image-ref>   # redeploy it"
echo
if [ "$APPLY" = 1 ]; then echo "fly-agent: applied."; else echo "fly-agent: dry run complete (0 commands executed)."; fi
