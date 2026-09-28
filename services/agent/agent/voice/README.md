# agent/voice — Pipecat pipeline (packets VOICE-001..004)

Pipeline (pattern in docs/penciled-reference-map.md; code MAY be copied from Darran's
Penciled voice-agent via the sanitized mirror /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/ and adapted
so every handler delegates to the brain; name the source file in the commit):

    SmallWebRTC in -> Deepgram STT (keyterm boost) -> user aggregator (Silero VAD +
    Smart Turn) -> Claude (Anthropic) -> Cartesia TTS [ServiceSwitcher failover ->
    Deepgram TTS] -> SmallWebRTC out -> assistant aggregator

- Pipecat Flows nodes are generated from packages/flow/flow.yaml (`flows.py`); every
  collect/choice node exposes `record_slots` (the text adapter's schema) and its handler
  delegates to agent.brain.engine.apply(...) via a `BrainPort` (one brain for text +
  voice). `ServiceBrain` runs voice turns through the text channel's `SessionService`
  (same session row); `LocalBrain` is the in-memory engine (tests / no store).

LLM slot selection (`VoiceConfig.llm_mode`, logged in `describe()`):

| env | llm_mode | what runs |
|---|---|---|
| live (DEEPGRAM_API_KEY) + ANTHROPIC_API_KEY, PERSONA_VOICE_STUB_LLM unset | `flows` | Claude under Pipecat Flows over the brain |
| no Anthropic key, PERSONA_VOICE_STUB_LLM=1, or PERSONA_VOICE_FAKE_VENDORS=1 | `stub` | `StubTurnLLM` echo; never moves state |

Offline tests: `tests/test_voice_flows.py` (nodes, scoping, text/voice parity),
`test_voice_flows_pipeline.py` (real FlowManager + scripted LLM + injected transcripts),
`test_voice_flows_shared_session.py` (chat -> call -> chat on one Postgres row).
- Barge-in: default Pipecat interruptions (WebRTC has AEC, so no PSTN echo mute).
- Idempotent teardown on disconnect; turn-level persistence means a hangup
  loses at most the in-flight turn; a lease row prevents double-dial.
- Graceful goodbye waits for playout before EndFrame.
- Silence floor: every node has a spoken max-silence nudge (never dead air).

## Latency (LAT-001)

Budget: user stops -> first bot audio < 1.5 s. Every caller turn logs one line
(`timing.py`): `voice_turn_timing node=.. direct=.. stt_final_ms turn_wait_ms llm_tool_ms
handler_ms db_ms db_calls llm_speech_ttfb_ms tts_ttfb_ms total_ms`.

- **Direct speech** (`PERSONA_VOICE_DIRECT_SPEECH`, default on): after `record_slots` the
  brain's templated line is final, so it goes straight to TTS (`tts_say`); the LLM only
  extracts. Saves the phrasing run (~0.5 s TTFB). `=0` restores LLM phrasing.
- **Handler**: 2 DB round trips per voice turn (was ~14): autocommit pool, state + lease in
  one select (`PgStore.load_for_turn`), version-checked update + events in one statement,
  and the committed state is handed back (no re-load).
- Benchmark: `python3 scripts/bench-voice-turn.py --rtt-ms 65` (local Postgres behind a
  65 ms-RTT TCP shim). 2026-09-28: handler p50 912 ms before -> 142 ms after.

### Region recommendation (EM decides; no infra changed)

The agent runs on Fly `sjc`; Supabase is `us-east-1` (~65 ms RTT). After this packet a
voice turn pays 2 DB round trips (~130 ms); text turns, lease heartbeats and SSE polling
pay the same RTT per query.

- **Recommended: move the Fly machine to `iad`.** DB RTT drops to ~1-2 ms (handler ~10-20
  ms), a region flag change that is reversible in minutes, no data migration, no secret
  rotation. Cost: browser <-> agent WebRTC media for West Coast callers adds ~60-70 ms RTT
  (≈ +35 ms each way on caller audio in and bot audio out), roughly offsetting half the DB
  win for them; East Coast callers gain on both. Worth confirming the Deepgram / Cartesia /
  Anthropic endpoints the agent resolves to from `iad` before switching (not measured here).
- **Not recommended: moving the DB** to a West region. Supabase regions are per project,
  so it means a new project, a data/schema migration, rotated connection secrets, and
  re-pointing the web app's server routes; more risk for the same ~130 ms.
- If reviewers are mostly West Coast, keeping `sjc` is acceptable now that the handler is
  2 round trips; revisit only if `voice_turn_timing` shows `db_ms` dominating.
