# HOSTED-001 — Hosted Playwright probe against live web + agent

CLASS: B-high
MODEL: opus
ROLE: frontend
WORKTREE: ../persona-onboarding-worktrees/hosted-001
BRANCH: claude/hosted-001
DEPENDS ON: INFRA-002b (DEPLOYED + SMOKE PASS), FE-005

## TASK
Add a gated hosted e2e probe that proves the production URLs still work after deploy:
1. New Playwright spec(s) under `apps/web/e2e/` that run **only** when
   `PERSONA_E2E_HOSTED_WEB_URL` (and optionally `PERSONA_E2E_HOSTED_AGENT_URL`) are set.
   Default local `npm run qa:e2e` must skip them (no network to prod unless opted in).
2. Checks (read-mostly; one throwaway session OK):
   - web `/`, `/about`, `/privacy` return 200 and show expected titles/landmarks
   - create session via `/api/session`, one text turn via `/api/session/turns`, UI advances
   - ICE via `/api/session/ice` includes at least one TURN URL (or document SKIP if absent)
   - OAuth start navigates toward accounts.google.com (do not complete login)
3. `scripts/hosted-e2e.sh` — thin wrapper that exports the live URLs from DEPLOY-STATE
   defaults and runs the hosted Playwright project/grep.
4. Do **not** change production deploy config; no `--apply`; no push.

## WHY
Deploy smoked via curl; we need a browser-level regression gate before Mon product test
and for overnight drift detection.

## SCOPE
apps/web/e2e/** (new hosted specs + tiny helper), apps/web/playwright.config.ts (project or
grep tag only if needed), scripts/hosted-e2e.sh (new).
Do NOT edit services/**, docs/design/spec.md, infra/**, or deploy scripts.

## SPEC
docs/design/spec.md @ main (frozen for visual — this packet is functional hosted probe,
not a visual redesign). Mockups: n/a for this packet.

## READ
- CLAUDE.md; docs/deploy/DEPLOY-STATE.md; scripts/deploy/smoke.sh (curl checks to mirror)
- apps/web/e2e/{live.ts,public-pages.spec.ts,real-agent-call.spec.ts,README.md}
- apps/web/playwright.config.ts; .claude/skills/webapp-testing/SKILL.md

## DO NOT READ
- .env*, secrets; Penciled sensitive paths; never modify penciled-emr; services/agent
  internals beyond the HTTP contract

## REQUIREMENTS
- Default URLs when env set empty→fail closed; when unset→skip.
- Never follow OAuth through to a real login; never print tokens.
- One throwaway session max; no spam loops.
- Keep existing mock-driver e2e green without hosted env.

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `npm -w apps/web run typecheck && npm -w apps/web run build`
- [ ] `npm run qa:e2e` green with hosted specs skipped
- [ ] `bash scripts/hosted-e2e.sh` PASS against
      https://persona-onboarding-darran.vercel.app (and agent health if checked)

## VISUAL_ACCEPTANCE
States: none required (functional probe). If you touch layout accidentally, max pixel
diff 3% vs docs/design/mockups for affected states — prefer zero visual diffs.

## CONSTRAINTS
No transition logic in the browser; no spec edits; no deploy/push.

## OUTPUT
Commit (no push). STATUS / FILES / HOW TO RUN / TESTS / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 35 turns.
