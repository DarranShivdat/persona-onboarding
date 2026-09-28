# PROMPT-001 — Agent-name suggestion chips + phrasing polish (DQ-04 residual)

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/prompt-001
BRANCH: claude/prompt-001
DEPENDS ON: FE-005 (merged; DQ-04 e2e exists), FLOW-002

## TASK
Close the residual DESIGN-002 DQ-04 gap on the **live** brain path and light tone polish:
1. Ensure the brain's transcript/push for the `agent_name` ask includes
   `suggestions: ["Juno","Atlas","Surprise me"]` (or the frozen copy from
   `docs/design/review/copy.md`) so `ApiSessionDriver` renders chips (see existing
   `apps/web/e2e/dq-04-name-chips.spec.ts`).
2. Confirm mapping in `apps/web/lib/session/api-driver.ts` still turns suggestions into a
   `chips` item; fix only if broken on the real agent contract (not the stub).
3. Small phrasing guard / prompt tweaks so agent-name and early turns match copy.md tone
   (calm, not form-like). No new LLM frameworks; keep Anthropic SDK + existing adapter.
4. Tests: unit/flow covering suggestions on agent_name ask; keep qa:e2e DQ-04 green.

## WHY
Reviewers feel "form vs conversation" in the first 30s. Chips were fixed for fixtures;
hosted live path must match before Mon product test.

## SCOPE
services/agent/agent/** (brain/LLM adapter/phrasing only as needed),
services/agent/tests/**, apps/web/lib/session/api-driver.ts (only if mapping broken),
apps/web/e2e/dq-04-name-chips.spec.ts (extend if needed).
Do NOT edit docs/design/spec.md; do NOT broaden into Gmail/voice lease work.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §brain/phrasing; docs/design/review/copy.md (DQ-04)
- docs/design/review/REPORT.md (DQ-04 section)
- services/agent/agent/ (llm adapter, flow apply, transcript push)
- apps/web/lib/session/api-driver.ts; apps/web/e2e/dq-04-name-chips.spec.ts

## DO NOT READ
- .env*, secrets; Penciled sensitive paths; never modify penciled-emr

## REQUIREMENTS
- Chips send text only — no browser transition logic.
- No LangChain/LangGraph; Langfuse only via existing adapters.
- Offline tests; no vendor network in qa tiers.
- Ports if local stack needed: agent :8400, web :3400; scratch DB `persona_prompt001`.

## ACCEPTANCE
- [ ] `npm run qa:fast`, `npm run qa:flow`
- [ ] `npm run qa:e2e` (including DQ-04)
- [ ] New/updated unit test proves agent_name ask carries suggestions on the live path
- [ ] no push / no deploy

## CONSTRAINTS
Stay in SCOPE; preserve FLOW invariants; no product-facts invention.

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 40 turns.
