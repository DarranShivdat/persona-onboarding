# DOCS-001 — Reviewer README + submission walkthrough

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/docs-001
BRANCH: claude/docs-001
DEPENDS ON: INFRA-002b (DEPLOYED + SMOKE PASS)

## TASK
Write the reviewer handoff package for the Persona trial Google Form submission:
1. `docs/REVIEWER.md` — what the demo is, hosted URLs, how to use text + browser call +
   Gmail connect, what is intentionally cut, known caveats (OAuth Testing mode, invite-only
   Gmail), and a 5-minute happy path.
2. `docs/WALKTHROUGH.md` — Loom-style spoken script (~3–5 min) the reviewer (or Darran) can
   follow: open URL → name the agent → call → fill slots → Gmail card → graduate. Include
   backup if mic/TURN fails ("type it" path).
3. Update `README.md` (repo root) with a short top section linking hosted URL + REVIEWER.md
   (keep existing engineer content below).
4. Do **not** invent product claims; only use `docs/product-facts.md` and public Persona
   facts already approved there. No secrets, no env values, no internal IPs.

## WHY
Submission needs a hosted link + code link Monday 5pm PT. Reviewers need a clear path;
M6 handoff was unpacketed until deploy finished early (Sun ~10:48pm).

## SCOPE
docs/REVIEWER.md (new), docs/WALKTHROUGH.md (new), README.md (top section only).
Optional tiny fix to docs/deploy/DEPLOY-STATE.md if a URL typo is found (no state machine
changes). Do NOT edit apps/, services/, harness/, infra/, or .env*.

## READ
- CLAUDE.md; docs/ROADMAP.md (M6 + timeline); docs/product-facts.md
- docs/deploy/DEPLOY-STATE.md (live URLs, Google Console values)
- docs/deploy/RUNBOOK.md (smoke section); scripts/deploy/google-oauth.md
- docs/design/review/copy.md (tone); existing README.md

## DO NOT READ
- .env*, secrets, `.persona-deploy/`, credential stores
- Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI) + other
  Penciled repos; never modify penciled-emr

## REQUIREMENTS
- Hosted web: `https://persona-onboarding-darran.vercel.app`
- Agent (for engineers): `https://persona-onboarding-agent.fly.dev` — do not tell reviewers
  to hit agent URLs directly unless useful for health.
- Note OAuth Testing: only invited Google accounts; consent may need "Select all" for Gmail.
- Never-cut features called out: text flow, browser voice (TURN), Gmail OAuth read+write
  (testing), hangup resume, early graduation, steer-back.
- Cuts called out briefly: spoken NATO email, Langfuse live judge, automated voice fault
  injection.
- No push; no deploy; no cloud changes.

## ACCEPTANCE
- [ ] `npm run qa:fast` (docs-only change should stay green)
- [ ] `docs/REVIEWER.md` and `docs/WALKTHROUGH.md` exist and match live URLs
- [ ] README.md links REVIEWER.md + hosted URL in the first screenful
- [ ] No secrets or env values in any new/edited file

## CONSTRAINTS
Invariants from CLAUDE.md; product claims only from product-facts.md.

## OUTPUT
Commit on the branch (no push). STATUS / FILES_CHANGED / TESTS / COMMIT / RISKS /
PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 30 turns.
