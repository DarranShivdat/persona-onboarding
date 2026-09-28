# VOICE-004-lite — Gmail card on call + "type it" escape (no spoken NATO)

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/voice-004-lite
BRANCH: claude/voice-004-lite
DEPENDS ON: VOICE-005, FE-004, GMAIL-001 (merged)

## TASK
On an active browser call, when the flow reaches the Gmail connect step:
1. Push the existing Gmail OAuth card into the call UI (same card as text; read+write scopes),
   so the user can complete Google consent without leaving the call.
2. Offer a spoken/text escape: "or type your email in the chat" — capturing email via the
   shared brain text path (no spoken NATO alphabet capture; that was cut for deadline).
3. Cover EC-19/20/21 voice variants at the harness/e2e level you can reach offline
   (fake vendors): card appears on call, wrong-account / partial-grant surfaces, skip/type-it
   continues the flow. Hangup/resume must not lose the Gmail step progress (VOICE-003 lease).

## WHY
Never-cut: Gmail OAuth on the call path. Spoken NATO was cut; card + type-it is the lite path
reviewers need Mon.

## SCOPE
services/agent/agent/voice/** (Flows node / handoff to push card event only),
services/agent/agent/api/** (if a small SSE/event is required),
apps/web/components/** and apps/web/lib/session/** (render card during call; type-it composer),
apps/web/e2e/**, services/agent/tests/**, harness edge-case PENDING clears for EC-19/20/21 voice.
Do NOT edit infra/** or docs/design/**.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §6–§11; harness/edge-cases.yaml EC-19/20/21
- services/agent/agent/voice/{session.py,handoff.py,host.py}; FE-003/GMAIL-001 Gmail card code
- apps/web/components/GmailCard.tsx; apps/web/lib/session/api-driver.ts

## DO NOT READ
- .env*, secrets; Penciled sensitive paths; never modify penciled-emr

## REQUIREMENTS
- One brain: card + type-it go through SessionService / same slots as text.
- No spoken email letter-by-letter. No LangChain. Offline tests only (fake vendors).
- Ports if needed: agent :8400, web :3500; scratch DB `persona_voice004`.

## ACCEPTANCE
- [ ] `npm run qa:fast`, `npm run qa:flow`, `npm run qa:e2e` green
- [ ] EC-19/20/21 voice variants no longer PENDING (or documented skip with reason)
- [ ] unit/e2e proof: card on call; type-it escape; hangup resume keeps Gmail progress
- [ ] no cloud; no push

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 40 turns.
