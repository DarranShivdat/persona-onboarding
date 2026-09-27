# FLOW-003 — Agent API (FastAPI) + Postgres store (version-checked turns, events, SSE)

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/flow-003
BRANCH: claude/flow-003
DEPENDS ON: FLOW-001 (merged). May stub LLM with the FLOW-002 interface if FLOW-002 is
not merged yet — use a FakeLlm that returns fixed extractions/phrases so API tests do
not require network.

## TASK
Implement `services/agent/agent/api/` (FastAPI) and `services/agent/agent/store/` (Postgres)
per `services/agent/agent/api/README.md` and `infra/supabase/migrations/0001_init.sql`:
create session, snapshot, text turn (extract→brain→phrase→persist), SSE event stream,
gmail server-to-server fill (shared secret), call lease acquire stub (offer/answer body
can be placeholder; real WebRTC is VOICE-001), `/health`. Optimistic concurrency on
`sessions.version`; append `session_events` in the same transaction.

## WHY
FE-002 and voice need a single brain-backed API with durable session state.

## SCOPE
services/agent/agent/api/**, services/agent/agent/store/**, services/agent/tests/test_api_*.py,
services/agent/tests/test_store_*.py, services/agent/pyproject.toml (deps only),
infra/supabase/** (migrations only if a gap vs state.py — prefer matching existing 0001).

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §2, §4, §5, §6
- services/agent/agent/api/README.md; infra/supabase/migrations/0001_init.sql
- services/agent/agent/brain/*; services/agent/agent/llm/* if present (else FakeLlm)
- services/agent/agent/obs/* (trace_id on events)

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI)
  + other Penciled repos; never modify penciled-emr; apps/**; docs/design/**

## REQUIREMENTS
- Routes from api/README.md; mutating routes require session token; rate limit hooks
  (in-memory OK for tests).
- Store: async or sync SQL (asyncpg/psycopg) against ephemeral Postgres in tests
  (testcontainers or `PERSONA_TEST_DATABASE_URL`); apply 0001_init.sql in fixtures.
- Turn path: load session → LLM extract (injectable) → `brain.apply` → phrase →
  `UPDATE sessions SET ... WHERE id=$1 AND version=$n` → insert events → bump version.
  Concurrent two-writer test: one wins, one gets conflict (409 or typed error).
- SSE: `GET /v1/sessions/{id}/events` streams UI pushes (`transcript`, `state`,
  `gmail_connect_card`, …) after turns; reconnect with Last-Event-ID optional.
- Gmail route: shared-secret header; sets gmail slot filled via brain; notifies SSE.
- Call route: acquires `call_lease_holder` with TTL; second acquire while live → 409.
- `/health` returns ok + git sha if available.
- Default Tracer noop; no Langfuse required.

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] API tests with ephemeral Postgres green, including concurrency (two writers)
- [ ] SSE test receives at least one UI push after a text turn
- [ ] FakeLlm path needs no network

## CONSTRAINTS
No cloud resources / no Supabase project creation (local/ephemeral DB only). No push.
No real Google OAuth. No Pipecat/WebRTC media in this packet.

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / RISKS.

## BUDGET
Opus 40 turns.
