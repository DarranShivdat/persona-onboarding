# VOICE-001 — Pipecat pipeline: SmallWebRTC + Deepgram + Claude + Cartesia failover

CLASS: B-high
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/voice-001
BRANCH: claude/voice-001
DEPENDS ON: INFRA-001 (merged; voice host = Fly.io + Cloudflare TURN — do NOT deploy)

## TASK
Implement `services/agent/agent/voice/` production pipeline (not the echo spike):
SmallWebRTC in → Deepgram STT → user aggregator (Silero VAD + Smart Turn) → Claude →
Cartesia TTS with ServiceSwitcher failover to Deepgram TTS → SmallWebRTC out →
assistant aggregator. Warmup at boot, barge-in, idempotent teardown, graceful goodbye
after playout. Wire call route to return a real SDP answer when keys are present; keep
offline unit tests for failover without network.

## WHY
Local browser call path unblocks FE-004 and VOICE-002 (Flows over brain). Hosting
decision is already Fly+TURN; this packet is local-only — no Fly/Cloudflare resources.

## SCOPE
services/agent/agent/voice/** (replace/extend beyond spike_echo), services/agent/tests/test_voice_*.py,
services/agent/pyproject.toml (deps), optional docs/decisions/0003-* only if you change
ARCH §7 defaults. May touch `agent/api` call handlers narrowly to return SDP answer +
lease renewal hooks. Do not edit apps/**.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §7, §9; docs/decisions/0001-voice-hosting.md
- docs/penciled-reference-map.md
- services/agent/agent/voice/spike_echo.py; infra/voice-spike/*
- services/agent/agent/api/{app,service}.py (call lease contract)
- Optional copy source: /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/
  (patterns/code OK; never modify penciled-emr; never read .env/transcripts/data/PHI)

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI)
  + other Penciled repos; never modify penciled-emr; apps/**; docs/design/**

## REQUIREMENTS
- Pipeline per ARCH §7. Pipecat Flows adapter is VOICE-002 — here a minimal pass-through
  LLM/context that can echo or use a stub turn is OK; prefer Claude via Pipecat Anthropic
  service when `PERSONA_QA_LIVE=1` / keys exist.
- TTS failover: Cartesia primary → Deepgram TTS on failure; unit-test the switcher with
  fakes (no network in default pytest).
- STT: Deepgram; document reconnect/backoff or second-vendor choice in OUTPUT (VOICE-001 decides).
- Warmup: load VAD/turn models + vendor TLS at process boot (best-effort if models missing in CI).
- Teardown: idempotent cancel of tasks/transports; goodbye waits for playout complete.
- Call API: `POST .../call` acquires lease and returns SDP answer when pipeline can; without
  keys, keep lease stub behavior and skip live integration tests.
- Local proof: document command to run a Chromium/local call (extend call_proof or README).
  Do NOT create Fly apps, TURN credentials, or any cloud resource.
- Env var names only (DEEPGRAM_*, CARTESIA_*, ANTHROPIC_*, PERSONA_*), never commit values.

## ACCEPTANCE
- [ ] `npm run qa:fast`
- [ ] `pytest services/agent/tests/test_voice_*.py` — failover (+ teardown) unit tests green offline
- [ ] Documented local-call steps in OUTPUT; if keys absent, mark live call PENDING with reason
- [ ] No cloud resources created

## CONSTRAINTS
No deploy. No push. No Pipecat Flows brain wiring (VOICE-002). No Gmail-on-call (VOICE-004).
No LangChain. Langfuse only via existing Tracer noop default.

## OUTPUT
Commit (no push). STATUS / FILES / TESTS / COMMIT / STT_FAILOVER_CHOICE / RISKS.

## BUDGET
Opus 40 turns.
