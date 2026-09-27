# agent/voice — Pipecat pipeline (packets VOICE-001..004)

Pipeline (pattern in docs/penciled-reference-map.md; code MAY be copied from Darran's
Penciled voice-agent via the sanitized mirror /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/ and adapted
so every handler delegates to the brain; name the source file in the commit):

    SmallWebRTC in -> Deepgram STT (keyterm boost) -> user aggregator (Silero VAD +
    Smart Turn) -> Claude (Anthropic) -> Cartesia TTS [ServiceSwitcher failover ->
    Deepgram TTS] -> SmallWebRTC out -> assistant aggregator

- Pipecat Flows nodes are generated from packages/flow/flow.yaml; every function
  handler delegates to agent.brain.engine.apply(...) (one brain for text + voice).
- Barge-in: default Pipecat interruptions (WebRTC has AEC, so no PSTN echo mute).
- Idempotent teardown on disconnect; turn-level persistence means a hangup
  loses at most the in-flight turn; a lease row prevents double-dial.
- Graceful goodbye waits for playout before EndFrame.
- Silence floor: every node has a spoken max-silence nudge (never dead air).
