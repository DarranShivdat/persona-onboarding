# FLOW-002 — LLM adapter: record_slots extraction + constrained phrasing + output guard

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/flow-002
BRANCH: claude/flow-002
DEPENDS ON: FLOW-001 (merged)

## TASK
Implement `services/agent/agent/llm/` — Anthropic SDK adapter that (1) extracts slot
candidates + intents via one `record_slots` tool call and (2) phrases 1–2 short sentences
from a `ResponsePlan`, with prompt caching on the stable system+tools prefix and an
output guard that drops tool/JSON/stage-direction/unapproved-claim lines before render.
Wire a thin turn helper that runs extract → `brain.apply` → phrase (no I/O/Postgres yet;
that is FLOW-003). Choose default models by a small offline fixture eval (Haiku-class vs
Sonnet for extraction latency/quality) and document the pick in OUTPUT.

## WHY
Text + voice both need the same extract/phrase contract before the API and Pipecat Flows
adapter can call the brain end-to-end.

## SCOPE
services/agent/agent/llm/**, services/agent/tests/test_llm_*.py,
services/agent/tests/fixtures/llm/**, docs/decisions/0002-llm-models.md (if you change
ARCHITECTURE §12 defaults).

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §5, §12; docs/product-facts.md
- services/agent/agent/brain/{engine,state,validators,spec}.py (contracts only)
- packages/flow/flow.yaml (slot names, intents, why lines)
- Optional mirror (copy OK): /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/
  for prompt/guard patterns only — adapt; do not import Penciled modules.

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI)
  + other Penciled repos; never modify penciled-emr; apps/**; infra/**

## REQUIREMENTS
- `record_slots` tool: strict JSON schema for all slots + intents + per-slot confidence;
  adapter must handle both forced and auto `tool_choice` (Opus/Fable reject forced).
- Extraction never writes state — only returns candidates for `brain.apply`.
- Phrasing input = `ResponsePlan` (ack X, ask Y, optional why, deferred) + persona +
  product facts; critical lines (readbacks, NATO email chunks, graduation summary) may
  be templated constants, not free LLM text.
- Output guard: drop lines mentioning tool names, JSON braces/keys, stage directions,
  or claims not present in plan/state; unit-test a table of bad/good lines.
- Prompt caching: stable system + tools prefix; dynamic user/turn content after.
- Testing: (a) contract tests with a fake Anthropic client; (b) recorded-fixture replay
  tests under `tests/fixtures/llm/` (no network in default pytest); (c) guard table tests.
  Live calls only behind an explicit env flag, never in `qa:fast` / `qa:flow`.
- No LangChain/LangGraph. Langfuse only via existing `agent.obs` Tracer (noop default).

## ACCEPTANCE
- [ ] `npm run qa:fast` and `npm run qa:flow` still green
- [ ] `pytest services/agent/tests/test_llm_*.py` — contract + fixture replay + guard table
- [ ] documented default extract/phrase model ids in OUTPUT (and 0002 if changed)

## CONSTRAINTS
No Postgres/HTTP in this packet. No browser. No cloud. No push. Env var names for API
keys only (read from env at runtime; never commit keys).

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / MODEL_PICK / RISKS.

## BUDGET
Opus 40 turns.
