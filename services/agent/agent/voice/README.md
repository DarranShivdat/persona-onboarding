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
