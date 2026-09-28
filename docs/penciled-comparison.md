# Penciled voice agent vs Persona voice: latency, guardrails, node structure

Mon Sep 28, 2026, ~2:00am PT. Source: the sanitized read-only copy at
`~/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/` (`bot.py`, `services.py`,
`warmup.py`, `flow_engine.py`, `observers.py`, `echo_guard.py`, `RELIABILITY-LAYERS-PLAN.md`).
Nothing was read from `penciled-emr`.

Baseline (live, agent `d4de37d`): user-stop → first audio **p50 2.29–2.36 s / p90 ~2.5 s**
(hosted probe), connect ~1.8–2.3 s. Server span per turn: turn_wait ~440 ms, **extraction LLM
call ~1.10–1.16 s**, handler 146 ms (DB 145 ms), TTS TTFB ~165 ms, plus ~0.4 s WebRTC/TURN
transport and jitter buffer on the client.

## How Penciled reaches ~600–800 ms turns

1. **The LLM speaks directly and its text streams straight into TTS.** The generative node's
   reply starts playing at the LLM's first sentence (TTFT ~400–600 ms on Haiku).
   `FlushBeforeToolUseAnthropicLLMService` closes the spoken part of a response the moment a
   `tool_use` block starts, so "Let me look that up." plays while the tool JSON is still
   streaming. Its "~800 ms" target is the **sum of per-service TTFBs** (STT+LLM+TTS,
   `observers.py`), not caller-stop → first audio, so it is not directly comparable to our 2.3 s.
2. **Classify-then-template (its Layer 3 plan)**: the LLM emits *only* a tool call
   (classification + slots, "~15–25 tokens ≈ 200–400 ms"), then code picks a template spoken
   with `tts_say` and `respond_immediately=False`, which removes the post-tool LLM round.
   **We already do this** (LAT-001 direct speech). The plan itself says this halves the rounds
   rather than removing the classify round.
3. **Endpointing**: Silero VAD `confidence 0.7, start 0.2 s, stop 0.4 s` + Smart Turn v3 with
   default params. **Ours is already tighter** (stop 0.2 s, Smart Turn max silence 1.6 s).
   No interim-transcript speculation: it waits for the final transcript like we do.
4. **Prewarm (`warmup.py`)**: at process boot it loads the VAD and Smart Turn ONNX models (with
   a dummy inference) and opens the LLM, STT and TTS connections. **We had none.**
5. **Prompt caching**: a cache breakpoint on the byte-stable `[tools + system]` prefix.
   **No gain for us**: our prompt (~1.4k tokens) is below Haiku 4.5's 4,096-token minimum, and
   measured cache reads are 0.
6. **Failover**: Cartesia → Deepgram TTS through `ServiceSwitcher`, with gender-matched voices.
   **Same as ours.** It also uses Deepgram keyterm boosting (accuracy, not latency) and
   Cartesia `sonic-3.5` (ours: `sonic-2`; our TTS TTFB is already ~165 ms).
7. **Fillers/acks**: none as templates. Its "ack" is the model's own pre-tool sentence, which
   its reliability plan names as the root of a whole leak class (narrating tools, "let me look
   up your account").

## Guardrails

| Penciled | Persona | Delta |
|---|---|---|
| L4 `OutputValidationFilter` in the TTS `text_filters` chain: machinery names, stage directions, numeric dates, per-node `forbid_phrases`, JSON shape. Drop, never regenerate. | `speech_guard.py` on LLM text (same drop-never-rewrite rule) + `llm/guard.py` claim families checked against the approved facts; templated lines by construction | We are stricter on claims (a price/retention/encryption claim must be backed by `product-facts.md`). **Adopted:** the templated voice answers (VQA-001) also pass `guard.check`. |
| Allowlisted tools (`ALLOWED_TOOLS`), config-validated | GUARD-001: per-node tool scope, catch-all rejection, intents accepted per node/channel | Equivalent |
| Categorize nodes: an enum of buckets + "none" and code-owned routing | The brain owns routing, and the LLM only fills `record_slots` | **Adopted the enum pattern** for caller questions: the model picks an approved-answer id and never writes the answer. |
| Echo mute for PSTN (no AEC) | Browser WebRTC has AEC | Not applicable |

## Node structure

Penciled: a JSON flow (`say / collect / branch / tool / handoff / end / categorize`) built into
Flows `NodeConfig`s at runtime. Behaviors are `guards → steps → routes` with `goto`, context
`reset` per node, and terminal templated nodes put `tts_say` + `end_conversation` in
`pre_actions`. Persona: `packages/flow/flow.yaml` → the pure brain (`engine.apply`) decides the
next node and the line; Flows nodes are derived from it (`voice/flows.py`), and a
node never runs a second LLM round. **Same shape**, and the brain being shared with text
(one session row) is a stronger invariant than Penciled's per-call flow state. Penciled
note we already respect: goto-null bounces force an LLM run. Our handler always returns a
node config, so no bounce ever re-runs the LLM.

## Deltas ranked by impact on our live p50 (2.3 s)

Measured with the real voice prompt and tool against Claude Haiku 4.5 from the Mac
(`/tmp` bench, 11–12 samples each after one warm-up call; p50 of the full tool call):

| Variant | p50 | TTFB | output tokens |
|---|---|---|---|
| prod before (auto tool choice, per-slot confidences) | 1254–1278 ms | ~700 ms | 123 |
| forced `tool_choice=record_slots` | 1083–1138 ms | ~590 ms | 102 |
| forced + no per-slot confidence (**adopted**) | 791–869 ms | ~550 ms | 69 |
| ... + approved-answer enum (VQA-001) | 863 ms (vs 840 without) | 600 ms | 67 |

| # | Delta | Est. p50 gain | Risk | Status |
|---|---|---|---|---|
| 1 | **Force the extraction tool call + drop per-slot confidences on voice.** On a call every spoken name is read back and the STT confidence is used, so confidences carried no information. | **−350 to −450 ms** | Low: tool forced only where `record_slots` exists; the terminal node is untouched; a repeat call with no new caller words is ignored; kill switch `PERSONA_VOICE_FORCE_EXTRACTION=0` | **Adopted** (default on) |
| 2 | **Quick ack while extraction runs** (Penciled's pre-tool speech, but words from a fixed code list) | first audio **~1.0–1.2 s** (−1.1 s perceived); the brain's line itself is unchanged | Behaviour change, so **flag `PERSONA_VOICE_QUICK_ACK`, default OFF**. Never on the greeting or around the name read-back, no back-to-back repeats, barge-in safe, not added to the LLM context | **Built, off** (Darran's call) |
| 3 | **Per-call LLM connection prewarm** (Pipecat makes a new Anthropic client per call, so turn 1 paid TCP+TLS) + boot prewarm (imports ~0.8 s, VAD/Smart Turn models, TURN creds) | −100 to −250 ms on turn 1; first call after a deploy −1 s connect | Low (best effort, off the call path) | **Adopted** |
| 4 | **Server ICE relay-first** (no STUN wait) + gather cap 5 s → 2.5 s; browser cap 3 s → 2 s | Removes the rare 5 s connect outlier (`sdp_ms=5007`) | Low: on Fly only the relay carries media (ADR 0001); `PERSONA_SERVER_ICE=all` restores | **Adopted** |
| 5 | Speculative extraction on the final transcript before Smart Turn confirms the end of the turn | −200 to −400 ms (overlaps turn_wait) | Medium-high: cancel/reconcile logic inside Pipecat's aggregator; split turns (the "An" bug) | Not now (post-deadline) |
| 6 | Stream the model's own pre-tool sentence to TTS (`FlushBeforeToolUse`) | similar to #2 | High: generative speech on every turn; Penciled's own plan retires it | Rejected (#2 is the safe form) |
| 7 | Fly `sjc → iad` (next to the DB) | ~−140 ms DB, ~+60 ms media for West Coast callers | Medium (deploy move) | Not now |
| 8 | Cartesia `sonic-3.5`, Deepgram keyterms (`Persona`, `Gmail`) | ≈0 latency; accuracy | Low | Later |
| 9 | Prompt caching on `[tools+system]` | 0 (below the 4,096-token minimum) | — | Not applicable |

Expected after #1, #3 and #4 with the ack off: p50 ≈ 1.9 s. With the ack on, the caller hears
audio at ≈ 1.0–1.2 s. The measured before/after numbers are in the section below.

## Measured after deploy
(filled in after the redeploy: see "Results" below)
