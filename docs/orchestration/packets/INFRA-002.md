# INFRA-002 — Deploy kit (no cloud): env contract, Fly/Vercel/Supabase runbooks, migrations, prod-like local stack

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/infra-002
BRANCH: claude/infra-002
DEPENDS ON: INFRA-001 decision (Fly.io sjc + Cloudflare Realtime TURN), FLOW-003, GMAIL-001 (merged)

## TASK
Make Monday's first deploy a 30-minute, copy-paste operation once Darran gives the go-ahead
and keys — WITHOUT creating any cloud resource now.
1. **Env contract**: reconcile `.env.example` with the names the code actually reads (e.g. code
   uses `PERSONA_DATABASE_URL`, `PERSONA_TURN_URLS/USERNAME/CREDENTIAL`, `CLOUDFLARE_TURN_KEY_ID/
   API_TOKEN`, `PERSONA_AGENT_BASE_URL`, `PERSONA_WEB_URL`, `NEXT_PUBLIC_PERSONA_ICE_URLS`,
   `PERSONA_TOKEN_ENCRYPT_KEY`, `PERSONA_INTERNAL_SECRET`, model vars, Langfuse) — grep
   `services/`, `apps/web/` (not e2e) for every env read. Produce `docs/deploy/ENV.md`: one row
   per variable → required/optional, which component (agent on Fly / web on Vercel / local),
   secret vs public, how to generate (e.g. token key, internal secret), default.
   Split examples: `.env.example` (root, documents all), `apps/web/.env.example`,
   `services/agent/.env.example`. Names only, no values.
2. **scripts/check-env.py** `--target agent|web|local`: reports MISSING/present by NAME only
   (never prints values), exit 1 if a required one is missing. Tested.
3. **Migrations**: `scripts/db-migrate.sh "$PERSONA_DATABASE_URL"` applies
   `infra/supabase/migrations/*.sql` in order, idempotently (tracking table), tested against
   the local Homebrew Postgres (create/drop a scratch DB). Works for Supabase's pooled/direct URL.
4. **Agent image**: fix `infra/agent.Dockerfile` (python 3.12-slim, pinned deps from
   pyproject extras, non-root user, `python -m agent.main --host 0.0.0.0 --port 8080`
   — VOICE-005 creates agent/main.py in parallel; do not create it), `.dockerignore`.
   Docker is NOT installed on the Mac: validate by review + `fly deploy --remote-only` in the
   runbook (Fly builds remotely). `infra/fly.toml`: app name, `sjc`, http service 8080,
   `/health` check, `auto_stop_machines="off"`, `min_machines_running=1`, memory for ONNX models,
   `[env]` non-secret defaults only.
5. **Runbooks as scripts (dry-run by default)**: `scripts/deploy/{fly-agent.sh,vercel-web.sh,
   supabase-db.sh,google-oauth.md,cloudflare-turn.md,smoke.sh}`. Each script prints the exact
   commands it would run and only executes with `--apply` (the EM runs `--apply` after Darran's
   go-ahead — you must NOT). Fly: `fly apps create`, `fly secrets import < agent env file`,
   `fly deploy --remote-only -c infra/fly.toml`. Vercel: `npx vercel link`, `vercel env add` per
   var for production+preview, `vercel deploy --prod`, root `apps/web`. Supabase: project
   create is manual (dashboard) → connection string → `db-migrate.sh`. Google: testing mode,
   test users, authorized redirect `https://<vercel-domain>/api/oauth/google/callback`, scopes.
   `smoke.sh <web-url> <agent-url>`: /health, create session, one text turn, ICE route, OAuth
   start redirect (no login), prints PASS/FAIL.
6. **Prod-like local stack**: `scripts/local-stack.sh up|down|status` — scratch local Postgres DB +
   migrations + agent (uvicorn via `agent.api.app:create_app_from_env`, or `agent.main` if present)
   + `next build && next start` wired together with local-only generated secrets (written to a
   gitignored `.persona-local/` dir, never committed); then run the existing Playwright live
   specs against it (`PERSONA_E2E_AGENT_URL`) and `smoke.sh`.
7. `docs/deploy/RUNBOOK.md`: ordered 30-minute deploy checklist for Monday morning with
   rollback (`fly releases`, `vercel rollback`) and the vendor spend caps Darran should set.

## WHY
Hosted URL must be live by Mon Sep 28 12:00pm PT. Keys/go-ahead may arrive late; everything
else must be ready so deploy is mechanical.

## SCOPE
infra/**, scripts/deploy/**, scripts/check-env.py, scripts/db-migrate.sh, scripts/local-stack.sh,
docs/deploy/**, .env.example, apps/web/.env.example, services/agent/.env.example, .gitignore,
.dockerignore, harness/tests/test_check_env.py (new). Do NOT edit services/agent/agent/**,
apps/web/app/**, apps/web/lib/** (VOICE-005 owns those tonight).

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §9, §11; docs/decisions/0001-voice-hosting.md;
  infra/README.md, infra/voice-spike/README.md, infra/fly.toml, infra/agent.Dockerfile
- services/agent/pyproject.toml; services/agent/agent/api/app.py (create_app_from_env, Settings);
  services/agent/agent/voice/{config.py,ice.py}; apps/web/lib/{oauth/google.ts,session/server.ts}

## DO NOT READ
- .env*, secrets (except `.env.example` files); Penciled sensitive paths + other Penciled repos;
  never modify penciled-emr

## REQUIREMENTS
- ABSOLUTELY no cloud resource creation, no `fly apps create`/`fly deploy`/`vercel deploy`/
  `vercel link`/`supabase` project commands, no logins, no pushes. Dry-run output only.
- Never print or commit secret values; generated local secrets go to gitignored `.persona-local/`.
- Scripts are bash-3.2 compatible (macOS) and zsh-safe when invoked via `bash script`.
- Ports: local-stack defaults agent :8200, web :3200 (overridable); scratch DB `persona_infra002`
  (other workers use 3100 and 3300/8300).

## ACCEPTANCE
- [ ] `npm run qa:fast` (incl. new check-env tests)
- [ ] `scripts/db-migrate.sh` applied twice to a scratch local DB (second run no-op)
- [ ] `scripts/local-stack.sh up` + live Playwright specs + `smoke.sh` pass locally; `down` cleans up
- [ ] every `scripts/deploy/*.sh` dry-run prints commands and exits 0 without network side effects
- [ ] docs/deploy/ENV.md covers every env var read by services/ and apps/web/

## OUTPUT
Commit (no push). STATUS / FILES / SUMMARY / DEPLOY_CHECKLIST / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 40 turns.
