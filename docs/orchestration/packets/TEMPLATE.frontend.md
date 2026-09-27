# FE-<nnn> — <title>

CLASS: B-high
MODEL: opus
ROLE: frontend
WORKTREE: ../persona-onboarding-worktrees/<slug>
BRANCH: claude/<slug>
DEPENDS ON: DESIGN-<nnn>

## TASK

## WHY

## SCOPE
apps/web/**, harness/visual/**

## SPEC
docs/design/spec.md @ <commit sha> (frozen). Mockups: docs/design/mockups/ @ same sha.

## READ
- CLAUDE.md; docs/design/spec.md; docs/ARCHITECTURE.md §Web
- .claude/skills/frontend-design/SKILL.md, .claude/skills/webapp-testing/SKILL.md

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI) + other Penciled repos; never modify penciled-emr; services/agent internals beyond the API contract

## REQUIREMENTS

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `npm -w apps/web run typecheck && npm -w apps/web run build`
- [ ] `npm run qa:e2e` (named specs)

## VISUAL_ACCEPTANCE
States: <list>. Viewports: 1440x900, 390x844. Max pixel diff per state: 3% (after
masking dynamic regions). Attach .persona-qa/visual/report.json summary in OUTPUT.

## CONSTRAINTS
No transition logic in the browser; no spec edits (raise SPEC_QUESTION instead).

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / VISUAL_DIFF / SPEC_QUESTIONS / RISKS.

## BUDGET
Opus 40 turns.
