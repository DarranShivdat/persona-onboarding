# Penciled voice-agent — reference map (patterns only, NO code)

Source inspected read-only on 2026-09-26: `/Users/darranshivdat/IdeaProjects/penciled-emr/voice-agent`
(checked-out branch of penciled-emr; Pipecat 1.4 + Pipecat Flows, Python 3.11+).
This is Darran's employer's (healthcare) codebase. **Nothing was copied.** This file
describes *what exists and the pattern*, so Persona workers can reimplement from Pipecat's
public docs. Until Darran confirms in writing that verbatim reuse is allowed, workers must
not open any Penciled repository (see CLAUDE.md).

Not read (by rule): `.env`, `.env.example`, `transcripts/` (real call transcripts),
`data/*.json` (patient/slot/insurance fixtures), `components/*.json`, flow JSON contents.

## Structure

| Path | What it does | Persona reimplementation |
|---|---|---|
| `bot.py` (~1.2k lines) | Entrypoint. `run_bot(transport, runner_args)` assembles the pipeline; `bot()` is the Pipecat-runner entry; `__main__` warms up, installs middleware/routes, then calls the Pipecat runner `main()`. Also serves a phone UI, a flow builder UI, JSON APIs, Twilio webhook/WS routes. | `agent/voice/pipeline.py` (`build_pipeline(session)`), `agent/main.py` (FastAPI app owning our routes; mount the SmallWebRTC offer endpoint ourselves rather than piggy-backing on the runner's app). No builder UI, no telephony. |
| Pipeline order (bot.py) | `transport.input → STT → user aggregator → LLM → TTS → transport.output → assistant aggregator` with universal `LLMContext` + `LLMContextAggregatorPair`. | Same order (it is Pipecat's canonical shape). |
| VAD / turn-taking (bot.py) | Silero VAD (confidence 0.7, start 0.2s, stop 0.4s) + Smart Turn v3 local analyzer as the user-turn stop strategy; comment notes A/B testing multi-part utterances before lowering stop time. | Same components; start from Pipecat defaults, tune with the voice harness (EC-11/EC-12) rather than copying constants. |
| Echo guard (`echo_guard.py`) | PSTN-only: mutes caller audio while bot audio is audible + a measured playout-drain tail, to stop echo-triggered false interruptions on Twilio. WebRTC path keeps full barge-in. | **Not needed** (browser WebRTC has AEC). Keep default Pipecat interruptions. Revisit only if real phone numbers are added. |
| `services.py` | Env-driven STT/LLM/TTS factories. Deepgram STT with Nova-3 keyterm boosting. Anthropic LLM subclass that flushes pre-tool-call text to TTS immediately (avoids multi-second stalls), plus an adapter that puts a cache breakpoint on the system prompt (stable prefix caching). TTS failover via Pipecat `ServiceSwitcher` + failover strategy (Cartesia → Deepgram TTS, gender-matched fallback voice), logging switch events. Same text filter chain applied to every TTS provider. | `agent/voice/services.py`: same *ideas* — `ServiceSwitcher` failover for TTS, keyterm boost for email domains/NATO words, system-prompt caching, a spoken-text filter chain. Decide on pre-tool flush only if we keep generative speech before tool calls (our turn loop mostly templates critical lines, reducing the need). |
| `flow_engine.py` | Data-driven Pipecat Flows builder: loads a JSON flow (nodes typed say/collect/branch/tool/handoff/end/categorize), builds `NodeConfig`s and `FlowsFunctionSchema` handlers; behaviors = guards/steps/routes; allow-listed tools; blocking tool work moved off the event loop with `asyncio.to_thread`; "resume" gotos suppress context reset; categorize nodes classify intent silently. | **Closest analogue to our design.** Persona: `flow.yaml` + pure `agent/brain` (transitions in code, unit-tested without Pipecat) + a thin Flows adapter that generates nodes and delegates handlers to the brain. Keep: tool allow-list, off-loop tool execution, "resume without context reset". Drop: the expression mini-language (our transitions are code, not JSON routes). |
| `flow.py` | Active-flow selection with a lock; hot-swap affects only new sessions; per-session snapshot of the engine. | Flow version pinned per session (`sessions.flow_version`); new versions apply to new sessions only. |
| `tools.py` | Mock/live clinic data + callable tools (lookup, slots, booking, estimates, confirmation), fuzzy name matching, slot prefetch/caching. Domain-specific. | Not reusable. Our tools: `record_slots`, `start_call`, `push_gmail_connect`, `capture_spoken_email`, `request_typed_email`. |
| `observers.py` | `TranscriptObserver` (per-turn transcript to console/file, per-turn STT/LLM/TTS TTFB latency, end-of-call summary) and `PlayoutObserver` (waits for bot turn end → used for graceful goodbye). Uses Pipecat `MetricsLogObserver` too. | `agent/obs`: a Pipecat observer that emits turn spans + TTFB metrics through our `Tracer` (→ Langfuse); a playout observer for graceful end. Transcripts go to `session_events`, not files. |
| Teardown (bot.py) | Idempotent per-call teardown on client disconnect **and** flow-driven end: cancels the worker, cleans transport, closes the WebRTC peer so a reconnect with the same pc_id doesn't hang; custom `end_conversation` action waits for goodbye playout before `EndFrame`. | Same pattern (VOICE-001); plus our lease release + resume message (VOICE-003). |
| `warmup.py` | Boot-time, best-effort, time-boxed warmup: load VAD/turn ONNX models, 1-token LLM call, STT init, short TTS synth. | Same idea in `agent/voice/warmup.py`; also run in the container healthcheck path. |
| `text_normalization.py` | TTS text filter: abbreviation expansion and a digit-by-digit policy for identifiers. | Persona needs email/NATO spoken forms instead: a filter that speaks `@` as "at", `.` as "dot", chunks addresses, and NATO-spells on readback. |
| Security middleware (bot.py) | Per-IP sliding-window rate limits on session-start routes, shared-token gate on mutating routes, `/health`, frame-ancestors CSP. | `agent/api`: per-IP + per-session limits on session create/turn/call start (vendor spend), session tokens, `/health`. |
| `sms.py`, Twilio routes | Outbound SMS + inbound telephony. | Not applicable (no real phone numbers). |
| `static/phone.html` | Browser call UI using the Pipecat JS client from a CDN; creates a fresh client/transport per call so call→hangup→call works without refresh. | FE-004 in Next.js with `@pipecat-ai/client-js`; keep "fresh client per call". |

## Tests / harness / evals

- ~20 pytest files next to the code: flow structure invariants (no node can strand a live
  call; farewell only from terminal nodes), template/flow loading, confirmation gates
  (no terminal handoff without an affirmative turn), spell-back gate for new names, phone
  read-back gate, router must not preview the next question, prompt caching, keyterm boost
  config, echo guard, text normalization, hosting hardening, Twilio security.
- `scripts/analyze-phone-batch.py`: post-hoc transcript analyzer (double-emit "seam"
  detection = two bot turns without a caller turn; beat checklist; worst LLM latency).
- `RELIABILITY-LAYERS-PLAN.md`: "classify-then-template" (LLM does classification + slot
  extraction; speech is selected templates; generation only on marked open beats) and a
  pre-TTS output validation filter (drop tool names/stage directions/JSON; sanitize numeric
  dates). `KNOWN_ISSUES.md` frames "three walls": zero open speech surfaces, silence-with-a-
  floor everywhere, machine-scale verification.
- No Dockerfile / fly.toml in voice-agent; hosting was being planned separately.

**Persona takeaways (adopted in ARCHITECTURE.md):** structural invariant tests over the flow
spec; absorb-first handling of fragments/noise; spell-back confirmation for names on voice;
templated critical lines + an output guard; a spoken silence floor on every node; a
double-emit (seam) detector in the voice harness; idempotent teardown + graceful goodbye.

## Deps (requirements.txt, names only)
`pipecat-ai[deepgram,anthropic,openai,google,cartesia,silero,local-smart-turn,webrtc,runner]>=1.0`,
`pipecat-ai-flows`, `pipecat-ai-small-webrtc-prebuilt`, `python-dotenv`, `loguru`, `httpx`, `twilio`.
Persona drops openai/google/twilio/prebuilt; adds `anthropic`, `fastapi`, `psycopg`, optional `langfuse`.

## Sensitivity notes (described, not quoted)
- `voice-agent/.env` exists (real vendor credentials presumably) — not read.
- `voice-agent/transcripts/` holds ~150 call transcripts (Jul 2026), gitignored — may contain
  real names/phone numbers/health details — not read.
- `data/` holds patient/appointment/insurance JSON; README lists demo patients with DOBs
  (presented as mock) — data files not opened.
- `sms.py` documentation describes use of a production parent Twilio account for SMS;
  `tools.py` has a live EMR integration path authenticated by an API key from env.
- Planning docs reference production hosting/BAA work and an internal pager.
- A newer, more advanced copy of this agent exists at `IdeaProjects/penciled-dev/voice-agent`
  (extra modules for turn authority, greeting watchdog, output guard, templates, and a
  harness). It was **not** inspected (outside the requested path).
