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
- docs/product-facts.md (Gmail read+write scopes, testing mode — drives consent copy)

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI) + other Penciled repos; never modify penciled-emr; services/** internals

## REFERENCES
- Company (confirmed by Darran): **Persona** by Zach Yadegari (after Cal AI) — a personal
  AI assistant "that gets things done". You text it (iMessage / iOS app) and it acts across
  Gmail, Google Calendar, Notion, Slack, Uber, DoorDash, etc., confirming before it acts.
  Darran calls the wearable the "personal band" = **Persona Band** (screenless wristband
  with mics + speaker + LED ring; preorders, ships Dec 2026).
- Primary: https://yourpersona.com (home) and https://yourpersona.com/band.
- iOS App Store listing "Persona - Your AI" (Iris Assistant, Inc.) — screenshots show the
  in-app look and onboarding. Launch coverage (Band announced Sep 15, 2026) for tone.
- Their core UX is *texting an assistant*: study chat bubbles, message cadence, tone, and
  how they explain integrations/permissions (Gmail consent!) and privacy.
- NOT these (different companies): usepersona.app, thepersona.io, withpersona.com
  (identity verification), linkedin.com/company/personaassistant.
- Public pages only. Do not sign up, log in, join waitlists/preorders, or submit forms.
  If a page blocks automated capture, note it and use what is public (App Store, press).

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
- Gmail card consent copy: Persona asks for **read and write** Gmail access (read, organize,
  draft, send — nothing sent or changed without the user's OK); prepare the user for
  Google's "unverified app" testing-mode screen (reviewers are test users).
- Timeline: FE-001 starts from this spec Sunday morning PT — prefer a complete, clear spec
  over exhaustive research.

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
