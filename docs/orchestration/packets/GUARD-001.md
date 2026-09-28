# GUARD-001 — Constrain the LLM per node + modularity proofs

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/guard-001
BRANCH: claude/guard-001
DEPENDS ON: NAME-001 merged to main

## TASK
Read docs/qa/requirements-audit.md ("LLM-constraint audit" + "Modularity"). Then:
1. Per-node acceptance: keep ONE `record_slots` schema (invariant 2: out-of-order answers),
   but add a code-side per-node acceptance table derived from flow.yaml (e.g. agent_name is
   never accepted on the voice channel; gmail never becomes filled from extraction; intents
   not meaningful on a node are dropped with an event). The brain logs `rejected_extraction`.
2. Out-of-node tool calls: if the voice LLM calls a function not declared on the current
   node (or any unknown function), it is rejected (no state change, logged, the node's line
   re-spoken). Add a guard wrapper in voice/flows.py; the text path already forces the tool.
3. Voice phrasing constraints: AnthropicLLMService max_tokens 300 → 120, temperature 0.3;
   change the task instruction from "in your own words" to "say this line; you may shorten
   it, never add facts, questions or offers"; run the existing output guard (llm/guard.py) on
   what the voice LLM says when feasible, else on the transcript (log violations).
4. Tests (offline): (a) out-of-node tool call rejected, state unchanged; (b) transitions come
   only from the brain (monkeypatch LLM output that "asks" for another node → ignored);
   (c) adding a new optional slot/node to a copy of flow.yaml needs no engine change (spec-
   driven test); (d) agent_name extraction on voice ignored; (e) voice LLM settings asserted.
5. Update docs/qa/requirements-audit.md LLM-constraint rows to PASS with test names.

## WHY
Darran asked for proof the model can't steer the flow or call out-of-scope tools.

## SCOPE
services/agent/agent/{brain,llm,voice}/**, services/agent/tests/**, packages/flow/flow.yaml
(acceptance metadata only), docs/qa/requirements-audit.md.
Do NOT touch apps/web/**, agent/api/** (except tests), harness/**.

## READ
CLAUDE.md; docs/ARCHITECTURE.md; docs/qa/requirements-audit.md; flow.yaml; brain/*; llm/*;
voice/flows.py, voice/services.py; tests/test_voice_flows*.py.

## DO NOT READ
.env*, .persona-deploy/**, secrets; never touch penciled-emr.

## REQUIREMENTS
Offline tests only; keep all green; no behaviour regressions in qa:flow replay fixtures.

## ACCEPTANCE
- [ ] `npm run qa:fast`, `npm run qa:flow` green (PATH=/opt/anaconda3/bin:$PATH)
- [ ] tests (a)–(e) exist and pass
- [ ] no push / no deploy

## OUTPUT
Commit on your branch (no push). STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 45 turns.
