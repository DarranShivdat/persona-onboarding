# DESIGN-002 — Design QA + copy tone pass on the local production build

CLASS: B-high
MODEL: opus
ROLE: design
WORKTREE: ../persona-onboarding-worktrees/design-002
BRANCH: claude/design-002
DEPENDS ON: DESIGN-001, FE-001..FE-004 (merged)

## TASK
Run the design-review gate against the real app (local production build; hosted preview is
not up yet) and produce a prioritized, implementable fix list for the Monday-morning polish
packet. Walk every state in docs/design/spec.md on desktop 1440×900 and mobile 390×844:
landing, chat agent-name, call offer, ringing/connected/muted/reconnecting/ended, mic denied,
call-in-another-tab, Gmail card idle/connecting/connected/error/wrong-account/partial grant,
graduation, welcome back. Review conversational copy (greeting, steer-backs, refusals,
privacy answers vs docs/product-facts.md, Gmail read+write consent copy, Google
"unverified app" preparation).

## WHY
Reviewers are Persona's team (yourpersona.com). First impression and conversational tone
decide the trial; the deadline is Mon Sep 28 (hosted by noon PT).

## SCOPE
docs/design/review/** only (report, screenshots, copy table). harness/visual/** read-only.

## READ
- CLAUDE.md; AGENTS.md "Worker roles" + design-review gate; docs/design/{spec.md,research.md,tokens.json}
- docs/product-facts.md; harness/edge-cases.yaml (UI states only)
- .claude/skills/frontend-design/SKILL.md, .claude/skills/webapp-testing/SKILL.md

## DO NOT READ
- .env*, secrets; Penciled sensitive paths + other Penciled repos; never modify penciled-emr;
  services/** internals

## REFERENCES
- https://yourpersona.com, https://yourpersona.com/band (public pages only; no sign-up).
- docs/design/references/ (captured in DESIGN-001).

## DELIVERABLES
- docs/design/review/REPORT.md: gate verdict (PASS / PASS-WITH-FIXES / FAIL), then a table of
  issues: id, state, viewport, severity (P0 blocks demo / P1 visible to reviewers / P2 nice),
  screenshot link, exact fix (file + CSS/copy change), effort (S/M).
- docs/design/review/copy.md: current line → proposed line for every agent-authored template
  and UI string that should change (tone: warm, brief, confident; never form-like).
- docs/design/review/shots/*.png from the running app.
- One "Darran taste question" (max 2 options with screenshots) if a judgment call remains.

## REQUIREMENTS
- Start the app yourself: `npm -w apps/web run build && npx next start -p 3100` from apps/web
  (mock/stub driver as the e2e suite does — see apps/web/e2e/README.md and playwright.config.ts);
  stop it when done. Use port 3100 (3000 may be used by other workers).
- Only P0/P1 items get exact fixes; keep the list ≤ 20 items, ordered by reviewer impact.
- Do not edit apps/** (a frontend packet implements your list Monday morning).

## ACCEPTANCE
- [ ] REPORT.md verdict + issue table with screenshots for every P0/P1
- [ ] copy.md covers greeting, each node ask, steer-back, refusal, privacy answer, graduation
- [ ] `npm run qa:fast`

## OUTPUT
Commit (no push). STATUS / FILES / SUMMARY / OPEN_DESIGN_QUESTIONS / RISKS.

## BUDGET
Opus 30 turns.
