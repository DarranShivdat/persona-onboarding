# DESIGN-<nnn> — <title>

CLASS: B-high
MODEL: opus
ROLE: design
WORKTREE: ../persona-onboarding-worktrees/<slug>
BRANCH: claude/<slug>

## TASK
Research / specify / mock up <surface>. Spec + mockups only — no production code.

## WHY

## SCOPE
docs/design/** only (references/, mockups/, spec.md, research.md). May add capture
helpers under harness/visual/ if needed for screenshots.

## READ
- CLAUDE.md, AGENTS.md "Worker roles"
- docs/design/README.md
- .claude/skills/frontend-design/SKILL.md (mandatory), .claude/skills/webapp-testing/SKILL.md

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI) + other Penciled repos; never modify penciled-emr; apps/** implementation details

## REFERENCES
URLs / products / screenshots to study (public pages only; no logging into accounts
you were not given).

## DELIVERABLES
- docs/design/research.md — what the reference product does, with screenshot links
- docs/design/spec.md — flow, layout grid, type scale, color tokens, spacing, radii,
  motion, copy tone, component states, accessibility, acceptance checklist
- docs/design/mockups/<state>@desktop.png and <state>@mobile.png (+ HTML/CSS source)

## REQUIREMENTS

## ACCEPTANCE
- [ ] every state listed in DELIVERABLES has desktop + mobile mockups
- [ ] spec.md has an acceptance checklist the EM can verify from screenshots
- [ ] tokens are machine-readable (docs/design/tokens.json)
- [ ] `npm run qa:fast`

## CONSTRAINTS
Reference screenshots stay in docs/design/references/ (analysis only). Our UI must be
original — informed by, not cloned from, Persona's brand.

## OUTPUT
Commit (no push). STATUS / FILES / SUMMARY / OPEN_DESIGN_QUESTIONS (for Darran) / RISKS.

## BUDGET
Opus 40 turns.
