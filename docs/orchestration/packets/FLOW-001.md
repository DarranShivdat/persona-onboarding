# FLOW-001 — Pure flow engine (brain) over flow.yaml

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/flow-001
BRANCH: claude/flow-001
DEPENDS ON: none

## TASK
Implement `agent.brain.engine.apply(spec, state, turn) -> TurnResult` and the slot
validators, as pure deterministic Python, and replace every PENDING test in
services/agent/tests/test_engine_pending.py with real tests (rename to test_engine.py).
Add `npm run flow:types` that emits TS types (slot names, node ids, intents) from
flow.yaml into apps/web/lib/flow-types.ts.

## WHY
Everything (text API, Pipecat Flows adapter, harness) depends on the brain.

## SCOPE
services/agent/agent/brain/**, services/agent/tests/**, packages/flow/**, scripts/flow-types.mjs, package.json (script only).

## READ
- CLAUDE.md (invariants); packages/flow/flow.yaml + README.md; docs/ARCHITECTURE.md §3–§5
- services/agent/agent/brain/{spec,state,engine}.py; harness/edge-cases.yaml (flow-tier cases)
- Optional (copy allowed, Darran's IP): /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/flow_engine.py, flow.py,
  tests/test_flow_structure.py, tests/test_confirmation_gate.py — structural-invariant test
  patterns. Our brain stays pure Python (no Pipecat import).

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI) + other Penciled repos; never modify penciled-emr; apps/**

## REQUIREMENTS
- Validators: agent_name (1–40 chars, flags joke/profane → confirm; abusive → reject),
  person_name (charset, length; low-confidence voice → needs spell-back confirm),
  need (non-empty, not "idk" → unsure_need intent), gmail_oauth (only `oauth_verified=True`
  input fills; typed/spoken → `candidate`).
- Next node = first unfilled, non-skipped slot in the active channel's ask order;
  greet → agent_name → call_offer precede slot order in text; voice never asks agent_name.
- Retry budget per node: attempt 1 reask, 2 explain_why (spec `why`), 3 skip → deferred.
- Intents: insist_graduate, change_answer, refuse_slot, unsure_need, decline/accept_call,
  noise_or_fragment (absorb: no attempt counted, no reask), prompt_injection (no state change).
- Graduation from any node when need filled or insist_graduate; deferred prompts listed.
- Deterministic events list for persistence (transition, slot_filled, slot_candidate, ...).
- 100% branch coverage of engine.py by tests; property test: random extraction
  sequences never produce an illegal transition and never fill gmail without OAuth.

## ACCEPTANCE
- [ ] `npm run qa:fast` and `npm run qa:flow` (0 PENDING in flow tier)
- [ ] flow-tier edge cases implemented: EC-01, 11, 13, 14, 15, 16, 17, 20, 23, 26, 28, 29, 30, 31
- [ ] `npm run flow:types` produces a file that `tsc --noEmit` accepts standalone

## CONSTRAINTS
No I/O, no LLM, no Pipecat imports in agent/brain.

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 40 turns.
