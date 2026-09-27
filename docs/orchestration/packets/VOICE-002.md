# VOICE-002 — Pipecat Flows adapter over brain (shared state)

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/voice-002
BRANCH: claude/voice-002
DEPENDS ON: VOICE-001 (merged), FLOW-002 (merged)

## TASK
Replace the VOICE-001 stub/pass-through LLM path with a Pipecat Flows adapter whose
nodes mirror the flow spec. Every collect node exposes `record_slots` (same contract as
the text LLM adapter). Voice turns must mutate the same brain/session state as text so
a scripted conversation yields the same final state on either channel.

## WHY
Unblocks VOICE-003 (hangup/lease) and FE-004 (real WebRTC UI) with one shared brain.
Reviewers will start in chat and continue on the call — state must not fork.

## SCOPE
services/agent/agent/voice/** (Flows adapter, node builders; keep failover/lifecycle),
services/agent/agent/brain/** only if a narrow shared helper is required (prefer calling
existing engine/validators), services/agent/tests/test_voice_flows*.py (new) and any
shared-brain tests under services/agent/tests/. Optional harness/convo note only if a
voice-vs-text parity helper belongs there. Do not edit apps/**.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §3–§5, §7–§8; docs/penciled-reference-map.md
- services/agent/agent/voice/session.py, stub_llm.py, services.py
- services/agent/agent/brain/*.py; services/agent/agent/llm/*.py (record_slots contract)
- Optional copy source: /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/
  (patterns OK; never modify penciled-emr; never read .env/transcripts/data/PHI)

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI)
  + other Penciled repos; never modify penciled-emr; apps/**; docs/design/**

## REQUIREMENTS
- Pipecat Flows nodes derived from the flow spec (greet/collect/choice/terminal as needed).
  `record_slots` on every collect node; progress owned by brain code, not the LLM.
- Shared-brain test: same scripted utterances (text turn loop vs voice Flows path with
  FakeLlm / stub STT injection) → same slot fills + node (deterministic).
- Keep TTS failover, teardown, warmup from VOICE-001. Do not deploy or create cloud resources.
- Offline unit tests only in default pytest (no vendor network).
- Live call still optional/PENDING without keys.

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `pytest services/agent/tests/test_voice_*.py` (+ new shared-brain / Flows tests) green offline
- [ ] Documented how the voice Flows path is selected vs stub (env flag OK)
- [ ] No cloud resources created

## CONSTRAINTS
No Gmail-on-call (VOICE-004). No hangup lease/reconnect (VOICE-003). No LangChain.
Langfuse only via existing Tracer. No push. No deploy.

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 40 turns.
