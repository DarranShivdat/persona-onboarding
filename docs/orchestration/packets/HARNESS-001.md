# HARNESS-001 — Scripted text-conversation tier (qa:convo) + eval plumbing

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/harness-001
BRANCH: claude/harness-001
DEPENDS ON: FLOW-001, FLOW-002 (extraction contract)

## TASK
Build harness/convo: a runner that executes every `convo`-tier case from
harness/edge-cases.yaml against the in-process text turn loop with a mock or replayed
LLM, asserts `expected.state`, and records per-case results through
harness/evals/interface.py (LocalEvalBackend). Wire `npm run qa:convo` to it.

## WHY
Makes prompt/flow changes safe: deterministic state assertions on every run; the same
scripts later become the Langfuse dataset for the live tier (OBS-002).

## SCOPE
harness/convo/**, harness/evals/**, harness/qa.mjs (convo tier only), services/agent/agent/llm/testing.py.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §5, §8; harness/README.md; harness/edge-cases.yaml
- harness/evals/interface.py; services/agent/agent/brain/*.py; services/agent/agent/llm/*.py

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI) + other Penciled repos; never modify penciled-emr; apps/**

## REQUIREMENTS
- Modes: `mock` (fixture extractor keyed by utterance), `replay` (recorded Anthropic
  responses in harness/convo/fixtures/recordings/<case>.jsonl), `--record` (live, opt-in).
- Deterministic slot_correctness score per case; on_track deterministic proxy (no reask
  of filled slots, node progress monotone except intentional change_answer).
- Run name `<git-sha>|flow-v<version>|prompts-<hash>`; `compare` prints per-case deltas.

## ACCEPTANCE
- [ ] `npm run qa:fast`, `npm run qa:convo` passes in mock + replay modes, 0 network calls
- [ ] all convo-tier cases implemented or explicitly PENDING with a reason

## CONSTRAINTS
No langfuse import outside harness/evals/langfuse_backend.py (not in this packet).

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 40 turns.
