# INFRA-002c — Finish deploy kit: local-stack, RUNBOOK, OAuth/TURN docs

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/infra-002c
BRANCH: claude/infra-002c
DEPENDS ON: INFRA-002 (merged partial — env/check-env/migrate/Dockerfile/fly/deploy dry-runs done)

## TASK
Close the INFRA-002 acceptance gaps left after the hard-cap finish:
1. `scripts/local-stack.sh up|down|status` — scratch local Postgres DB + `db-migrate.sh` +
   agent via `python -m agent.main` (or create_app_from_env) + `next build && next start`,
   with local-only secrets written under gitignored `.persona-local/` (never commit values).
   Defaults: agent :8200, web :3200, scratch DB `persona_local`. Wire
   `PERSONA_E2E_AGENT_URL` / `PERSONA_WEB_URL` so existing live Playwright specs can hit it.
2. `docs/deploy/RUNBOOK.md` — ordered ~30-minute Mon morning checklist: keys →
   `check-env.py` → `supabase-db.sh --apply` → `fly-agent.sh --apply` → `vercel-web.sh --apply`
   → Google redirect URI → Cloudflare TURN → `smoke.sh`. Include rollback
   (`fly releases` / `vercel rollback`) and the vendor spend caps Darran should set.
3. `scripts/deploy/google-oauth.md` — testing mode, test users, scopes
   (`openid email profile gmail.readonly gmail.modify gmail.send`), authorized redirect
   `https://<vercel-domain>/api/oauth/google/callback`.
4. `scripts/deploy/cloudflare-turn.md` — Realtime TURN key creation steps (names only).
5. Re-verify `scripts/db-migrate.sh` twice on a scratch DB (second run no-op) and that every
   `scripts/deploy/*.sh` dry-run exits 0 with zero network side effects.

## WHY
INFRA-002b (`--apply` deploy) is blocked until the runbook + local-stack proof exist.
Hosted URL target: Mon Sep 28 12:00pm PT.

## SCOPE
scripts/local-stack.sh (new), docs/deploy/RUNBOOK.md (new),
scripts/deploy/{google-oauth.md,cloudflare-turn.md} (new), minor fixes to
scripts/deploy/* or scripts/db-migrate.sh / scripts/check-env.py if needed for local-stack.
Do NOT edit services/agent/agent/** or apps/web/app/** or apps/web/lib/** (owned by FE/VOICE).
Do NOT create cloud resources; dry-run only; no `--apply`; no push.

## READ
- CLAUDE.md; docs/deploy/ENV.md; infra/{fly.toml,agent.Dockerfile,README.md}
- scripts/{check-env.py,db-migrate.sh,deploy/*}; scripts/local-call-smoke.sh (pattern)
- docs/decisions/0001-voice-hosting.md; docs/orchestration/packets/INFRA-002.md (original)

## DO NOT READ
- .env*, secrets (except `.env.example`); Penciled sensitive paths; never modify penciled-emr

## REQUIREMENTS
- ABSOLUTELY no cloud resource creation / deploy / login / push.
- Never print or commit secret values; `.persona-local/` gitignored.
- bash-3.2 compatible; ports agent :8200 web :3200 (overridable).

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `scripts/local-stack.sh up` reaches /health + web 200; `down` cleans up
- [ ] dry-run every `scripts/deploy/*.sh` exits 0
- [ ] `db-migrate.sh` twice on scratch DB (second no-op)
- [ ] RUNBOOK.md + google-oauth.md + cloudflare-turn.md present and actionable

## OUTPUT
Commit (no push). STATUS / FILES / SUMMARY / DEPLOY_CHECKLIST / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 35 turns.
