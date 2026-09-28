# AUDIT-001 — Playwright button audit (every control, every screen, desktop + mobile, local + LIVE)

CLASS: B-high
MODEL: opus
ROLE: frontend
WORKTREE: ../persona-onboarding-worktrees/audit-001
BRANCH: claude/audit-001
DEPENDS ON: main @ HEAD

## TASK
Build an automated audit proving there are NO dead controls:
1. New Playwright suite `apps/web/e2e/audit/**` + config `apps/web/playwright.audit.config.ts`
   with two projects: desktop (1280x800 Chromium) and mobile (iPhone 13 emulation).
   Two targets: LOCAL (stub agent, like qa:e2e) and LIVE (`PERSONA_AUDIT_URL`, default
   https://persona-onboarding-darran.vercel.app; skipped unless set).
2. Screens: landing, text chat (each onboarding step incl. agent-name chips), call (ringing /
   live / captions / mute / end; fake media flags `--use-fake-ui-for-media-stream
   --use-fake-device-for-media-stream`), mic-denied, hangup/call-ended state, Gmail connect
   card (start → on LIVE assert navigation to accounts.google.com with the right
   redirect_uri and STOP there; LOCAL: use the existing gmail-oauth mock helpers for return
   success / cancel / error / wrong account), graduation/home, /about, /privacy, error states
   (agent down → error banner + retry; 404 route), refresh/resume.
3. For EVERY visible button, link, input, textarea, chip, checkbox and [role=button] on each
   screen: enumerate them automatically (fail the audit if a new control appears that has
   no expectation), then assert each does something correct (navigation, state change,
   network call, focus, visible text change, or is properly disabled with aria-disabled and
   no pointer cursor). Also: accessible name present, tap target >= 44px on mobile, visible
   focus ring, links not 404 (check hrefs with request.get).
4. Screenshots of each screen × viewport → `docs/qa/button-audit/<target>-<viewport>-<screen>.png`.
   Report → `docs/qa/button-audit.md`: table screen | control | viewport | expected | result
   (PASS/FAIL + note) for LOCAL and LIVE, summary counts, list of failures + fixes.
5. FIX every failure you find in web code, EXCEPT the graduation/home screen internals
   (Home.tsx, home composer behaviour, edit/dismiss) which GRAD-001 is rebuilding in parallel:
   for those, record the failure in the report as "owned by GRAD-001" and write the audit
   expectations for the NEW behaviour (message → reply, tap-to-edit, dismiss, Connect Gmail)
   so the audit passes once GRAD-001 merges (mark them `test.fixme` only if they cannot pass
   on current main, with a comment).
6. QA tier: add `qa:audit` to harness/qa.mjs + package.json (LOCAL audit; offline), include it
   in `qa` all, and make the deploy scripts gate on it: `scripts/deploy/vercel-web.sh --apply`
   and `scripts/deploy/fly-agent.sh --apply` must run `npm run qa:audit` first and abort on
   failure (override flag `--skip-audit` that prints a loud warning). Add a post-deploy LIVE
   audit hook to `scripts/deploy/smoke.sh` behind `--audit` (runs audit with PERSONA_AUDIT_URL).
   Document in docs/qa/README or harness docs.
7. Run LIVE audit once against production (read-only: it may create onboarding sessions;
   it must NOT complete a real Google sign-in or start > 2 real calls) and include results.

## WHY
Darran found a dead composer on the graduation screen; he wants proof every control works.

## SCOPE
apps/web/e2e/audit/**, apps/web/playwright.audit.config.ts, docs/qa/button-audit.md,
docs/qa/button-audit/**, harness/qa.mjs, package.json + apps/web/package.json (scripts only),
scripts/deploy/{vercel-web.sh,fly-agent.sh,smoke.sh} (gate only), web fixes in
apps/web/components/{TopBar,Thread,Composer,CallPanel,GmailCard,Ring}.tsx, apps/web/app/(public)/**,
apps/web/app/globals.css, apps/web/lib/session/** (small fixes only; GRAD-001 also edits
api-driver.ts — keep your diffs there minimal and localized).
Do NOT touch: Home.tsx, services/agent/**, apps/web/app/api/session/** (GRAD-001).

## READ
CLAUDE.md; apps/web/playwright.config.ts; apps/web/e2e/{README.md,live.ts,call.ts,gmail-oauth.ts,
stub-agent.mjs,real-agent-call.spec.ts,hosted/**}; harness/qa.mjs; scripts/deploy/*.sh;
apps/web/components/*.tsx; docs/design/spec.md (screens).

## DO NOT READ
.env*, .persona-deploy/**, secrets; never touch penciled-emr.

## REQUIREMENTS
- LOCAL audit must be offline and deterministic (< 4 min). LIVE audit gated on env.
- Ports: web :3420, stub :3219.
- Never print or commit secrets/tokens; screenshots must not show any.

## ACCEPTANCE
- [ ] `npm run qa:audit` green locally (fixme only for GRAD-001-owned items)
- [ ] LIVE audit run + results in docs/qa/button-audit.md with screenshots
- [ ] `npm run qa:fast`, `npm run qa:e2e` still green
- [ ] deploy scripts gate on qa:audit
- [ ] no push / no deploy

## OUTPUT
Commit on your branch (no push). Final message: STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 60 turns.
