# VOICE-003 — Call lease, reconnect grace, hangup resume, silence floor

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/voice-003
BRANCH: claude/voice-003
DEPENDS ON: VOICE-002 (merged), FLOW-003 (merged)

## TASK
Complete channel handoff for voice: call lease acquire/heartbeat/release with take-over,
reconnect grace window (default ~20s) that resumes the same call with a "we got cut off"
line, hangup → chat resume message listing what's left, typing-during-call merge into the
same turn stream, and per-node silence floor nudges (~7s / ~15s then offer text + polite
end). Wire to existing store lease helpers and VOICE-002 Flows session.

## WHY
Reviewers hang up, drop Wi‑Fi, redial, and type while on the call — EC-01/02/04/10/28 are
must-keep for Mon demo. Unblocks FE-004 lease UX and HARNESS-003 fault injection.

## SCOPE
services/agent/agent/voice/** (lease heartbeat, grace, silence nudges, hangup resume
hooks), services/agent/agent/api/** (call start/end/take-over semantics already sketched —
complete them), services/agent/agent/store/** only if lease/grace fields need a small
extension, services/agent/tests/test_voice_lease*.py / test_voice_silence*.py / related
(new). Prefer Fake transport / stub STT; no vendor network. Do not edit apps/** (FE-004
owns UI).

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §7–§8; harness/edge-cases.yaml (EC-01, 02, 04, 10, 28)
- services/agent/agent/voice/session.py, flows.py, lifecycle.py; agent/api/app.py (CallIn,
  lease 409); agent/store/postgres.py (acquire_call_lease / release_call_lease)
- services/agent/tests/test_voice_*.py (patterns); docs/penciled-reference-map.md

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI)
  + other Penciled repos; never modify penciled-emr; apps/**; docs/design/**

## REQUIREMENTS
- **Lease (EC-02)**: starting a call acquires lease + heartbeat; second acquire while live
  → 409 `call_in_progress` with take-over option that steals after explicit confirm
  (server flag). Never two live pipelines for one session.
- **Hangup resume (EC-01)**: on end outside grace, release lease, persist state, emit UI
  push / turn outcome so chat can show resume copy naming remaining slots.
- **Reconnect grace (EC-04)**: default ~20s (env-tunable); reconnect inside window resumes
  same call_id + spoken filler; after window behaves as hangup.
- **Silence floor (EC-10)**: per-node spoken nudges ≈7s then ≈15s with example; then offer
  text and end politely — never dead air. Offline-testable with fake clock / injected
  timers.
- **Typing during call (EC-28)**: text turns while `active_channel=voice` merge into the
  same serialized turn stream; voice acknowledges; no double-process. Use existing session
  lock/version checks.
- Unit/integration tests offline only. Live WebRTC optional/PENDING without keys.

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `pytest services/agent/tests/test_voice_*.py` (+ new lease/silence/handoff tests) green offline
- [ ] Tests cover EC-01, EC-02, EC-04, EC-10, EC-28 behaviors at API/voice layer (document
      any UI-only remainder for FE-004)
- [ ] No cloud resources; no push

## CONSTRAINTS
No Gmail-on-call (VOICE-004). No LangChain. Langfuse only via existing Tracer. No deploy.

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 40 turns.
