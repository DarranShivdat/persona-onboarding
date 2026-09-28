#!/usr/bin/env bash
# HOSTED-001: browser-level probe of the live deploy (Playwright "hosted" project, e2e/hosted/).
#
#   bash scripts/hosted-e2e.sh [extra playwright args]
#
# Defaults are the DEPLOY-STATE URLs; override with PERSONA_E2E_HOSTED_WEB_URL /
# PERSONA_E2E_HOSTED_AGENT_URL (set AGENT to "-" to skip the agent /health check).
# Creates one throwaway onboarding session; never completes Google login; no deploy changes.
# PERSONA_E2E_HOSTED_REQUIRE_TURN=0 lets STUN-only ICE pass (annotated SKIP).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export PERSONA_E2E_HOSTED_WEB_URL="${PERSONA_E2E_HOSTED_WEB_URL-https://persona-onboarding-darran.vercel.app}"
AGENT="${PERSONA_E2E_HOSTED_AGENT_URL-https://persona-onboarding-agent.fly.dev}"
if [ "$AGENT" = "-" ]; then unset PERSONA_E2E_HOSTED_AGENT_URL; else export PERSONA_E2E_HOSTED_AGENT_URL="$AGENT"; fi
[ -n "${PERSONA_E2E_HOSTED_WEB_URL// }" ] || { echo "hosted-e2e: PERSONA_E2E_HOSTED_WEB_URL is empty" >&2; exit 2; }

echo "hosted-e2e: web=$PERSONA_E2E_HOSTED_WEB_URL agent=${PERSONA_E2E_HOSTED_AGENT_URL:-skipped}"
cd "$ROOT"
exec npm -w apps/web exec -- playwright test --project hosted "$@"
