# DESIGN-001 — Research Persona's existing product/onboarding UI + onboarding design spec

CLASS: B-high
MODEL: opus
ROLE: design
WORKTREE: ../persona-onboarding-worktrees/design-001
BRANCH: claude/design-001
DEPENDS ON: none (M0 scaffold)

## TASK
Research Persona's public product and any visible onboarding UI, capture reference
screenshots, and produce the design spec + reference mockups for our onboarding:
chat, call offer, phone simulator (ringing / connected / muted / reconnecting /
ended), Gmail connect card (idle / connecting / connected / error / wrong account),
graduation, and the resume/"welcome back" state.

## WHY
Reviewers are Persona's team; the onboarding should feel native to their product
while being our original work. FE-001 implements from this spec.

## SCOPE
docs/design/** only (+ harness/visual/capture.mjs if you need a capture helper).

## READ
- CLAUDE.md; AGENTS.md "Worker roles"; docs/design/README.md
- docs/ARCHITECTURE.md §1 (product surfaces) and §6 (UI pushes)
- harness/edge-cases.yaml (only to enumerate UI states: EC-01..04, 08, 20-22, 29-31)
- .claude/skills/frontend-design/SKILL.md (mandatory), .claude/skills/webapp-testing/SKILL.md

## DO NOT READ
- .env*, secrets; any Penciled repository; services/** internals

## REFERENCES
- Primary candidate: https://usepersona.app (AI assistant that emails on your behalf;
  Gmail + phone number; matches "Persona" CTO trial context). Confirm it is the right
  company from the site content; also check linkedin.com/company/personaassistant.
- Other same-name products to rule out: thepersona.io, withpersona.com (identity verification).
- Darran has a browser tab titled "Persona — CTO Trial"; if identity is ambiguous, stop
  and report OPEN_DESIGN_QUESTIONS rather than guessing.
- Public pages only. Do not sign up, log in, or submit forms.

## DELIVERABLES
- docs/design/references/*.png — full-page + key-section screenshots (desktop + mobile)
  captured with Playwright; docs/design/research.md describing their visual language
  (type, color, spacing, illustration, voice/tone, onboarding patterns) with image links.
- docs/design/spec.md — flow diagram (mermaid), layout grid, type scale, color tokens,
  spacing/radii/shadows, motion, copy tone guide (sample lines for each node incl.
  steer-back and refusal), component specs + states, accessibility (focus, contrast,
  captions for the call), responsive rules, and an **acceptance checklist**.
- docs/design/tokens.json — machine-readable tokens.
- docs/design/mockups/ — static HTML/CSS mockups + PNG renders named
  `<state>@desktop.png` / `<state>@mobile.png` for states:
  `landing`, `chat-agent-name`, `call-offer`, `call-ringing`, `call-connected`,
  `call-reconnecting`, `gmail-card-idle`, `gmail-card-connected`, `gmail-card-error`,
  `graduation`, `welcome-back`.

## REQUIREMENTS
- Conversational, not a form: no stepper-with-fields look; progress is implicit (e.g.
  a subtle 4-item checklist that fills as slots are captured, including out of order).
- The phone simulator must read as a call (not a chat) and work beside the chat on
  desktop; stacked on mobile. Live captions always visible.
- Gmail card can appear mid-call without covering the call controls.
- Graduation shows deferred items as gentle, dismissible prompts.

## ACCEPTANCE
- [ ] all 11 states × 2 viewports rendered to PNG
- [ ] spec.md acceptance checklist is verifiable from screenshots alone
- [ ] tokens.json parses and matches spec.md
- [ ] `npm run qa:fast`

## CONSTRAINTS
Original UI informed by Persona's language; no logos/brand assets copied into mockups.
Reference screenshots stay in docs/design/references/.

## OUTPUT
Commit (no push). STATUS / FILES / SUMMARY / OPEN_DESIGN_QUESTIONS / RISKS.

## BUDGET
Opus 40 turns.
