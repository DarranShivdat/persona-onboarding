# harness/voice — headless voice tests (qa:voice, packet HARNESS-003)

A headless Pipecat client connects to the agent over SmallWebRTC (aiortc), streams
TTS-generated user audio (Cartesia/Deepgram TTS rendered once into `audio/` and
cached), and injects faults on a timeline from each `voice`-tier case:
`hangup`, `network_drop`/`reconnect`, `silence`, `barge_in` (start speaking while
bot audio is flowing), `fail_stt`/`fail_tts` (vendor fault injection via an env
flag honored only in test mode). It records bot audio + transcript + state and
asserts on `expected.state`, time-to-first-audio, and max dead-air.
