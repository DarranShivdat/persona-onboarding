# Architecture — Persona onboarding

Status: M0 design (scaffold). Decisions marked **[decided]** came from Darran; **[proposed]**
are this design's choices; resolved decisions and open items live in `ROADMAP.md` → Decisions / Still open.

## 1. Product surfaces

One page, one session, two channels:

- **Chat (text-adaptive)** — greets, collects the agent name, offers a call, and can
  collect everything else if the user prefers typing.
- **Phone simulator (browser voice)** — a "call" UI beside the chat (stacked on mobile)
  over WebRTC; collects user name, need, and Gmail. Never asks for the agent name.
- **Gmail connect card** — Google OAuth; can be pushed onto the screen mid-call.
- **Graduation** — the "main experience" landing; missing items become gentle deferred prompts.

## 2. Components

```
 Browser (Next.js on Vercel)                    Agent service (Python, long-lived: Fly/Railway/Pipecat Cloud)
 ┌───────────────────────────┐   HTTPS/SSE    ┌────────────────────────────────────────────────────┐
 │ Chat UI ─ SessionDriver ──┼──────────────▶ │ agent/api   (FastAPI: sessions, turns, gmail, SSE)  │
 │ Phone simulator ──────────┼── WebRTC ────▶ │ agent/voice (Pipecat: SmallWebRTC, Deepgram, Claude, │
 │ Gmail card ─ OAuth route ─┼─ server→server▶│              Cartesia⇄Deepgram TTS, Flows adapter)  │
 └───────────────────────────┘                │ agent/brain (pure flow engine; one brain)           │
                                              │ agent/llm   (Anthropic SDK: extract + phrase)       │
                                              │ agent/obs   (Tracer interface → noop/jsonl/Langfuse)│
                                              │ agent/store (Postgres; single writer)               │
                                              └──────────────┬─────────────────────────────────────┘
                                                             ▼
                                          Supabase Postgres (sessions, session_events, calls, gmail_connections)
                                          Langfuse (traces, datasets, eval runs) — optional, behind interfaces
```

- **No LangChain/LangGraph** [decided]. LLM calls use the **Anthropic SDK directly**
  (text path) and Pipecat's Anthropic service (voice path).
- **Pipecat Flows** is the voice-side state machine runtime [decided]; its nodes are
  generated from `packages/flow/flow.yaml` and delegate transitions to `agent/brain`.

## 3. Flow spec and ownership

`packages/flow/flow.yaml` is the state machine as data: slots (with validator, channels,
"why" line, confirm policy), per-channel ask order, retry budgets, graduation rule,
intents, nodes (`say`/`collect`/`choice`/`terminal`) with per-node tool scoping.

**Owner [proposed]: one Python runtime (`agent/brain`)**, called by both channels.
TypeScript gets generated types only (FLOW-001 `flow:types`). Rationale: Pipecat Flows
forces Python on the voice side; a second (TS) engine for text would drift and double
the edge-case test matrix. Structural invariants are tested (`qa:fast`).

Nodes: `greet → agent_name → call_offer → user_name → need → gmail → value_demo → graduated`.
Code owns progress; the LLM owns phrasing and extraction.

## 4. Session state schema

Source of truth: `infra/supabase/migrations/0001_init.sql`; Python mirror
`agent/brain/state.py`.

| Field | Meaning |
|---|---|
| `sessions.version` | optimistic concurrency; every turn is `UPDATE ... WHERE version = $n` |
| `node`, `active_channel` | current node; `text` / `voice` / null |
| `slots` (jsonb) | per slot: value, status `empty/candidate/filled/skipped`, source, confidence, validated_by, attempts |
| `node_attempts` | retry-budget counters |
| `deferred_prompts` | slots skipped or deferred at graduation |
| `call_lease_holder`, `call_lease_expires_at` | one live call per session (double-dial lock) |
| `session_events` | append-only log of utterances, extractions, transitions, UI pushes, call events (+ trace_id) |
| `calls` | per-call rows with end_reason and reconnect count |
| `gmail_connections` | verified Google identity, granted scopes, encrypted refresh token (required — Gmail read+write), revoked_at |

## 5. Turn loop (both channels)

1. **Input**: typed text, or a final STT transcript (voice), plus UI events (OAuth result,
   typed-during-call text, button presses) — all serialized per session.
2. **Extraction** (LLM, every utterance): one `record_slots` tool call returning candidates
   for **all** slots + intents + per-slot confidence. Strict JSON schema; `tool_choice:
   auto` + strict tools (Opus 5.5 / Fable 5.1 reject forced tool_choice; latency-tier models
   may still force it — adapter handles both). On voice, `record_slots` is the Flows
   function exposed on every collect node.
3. **Validation** (code): validators per slot; only validated → `filled`. Typed/spoken email
   → `candidate`; only OAuth fills `gmail`.
4. **Transition** (code, `brain.apply`): intents (change, refuse, insist, unsure, injection,
   noise) → next node = first unfilled slot in the channel's order; retry budget reask →
   explain why → skip/defer; graduation legal from any node when `need` is filled or the
   user insists.
5. **Phrasing** (LLM, constrained): given the `ResponsePlan` (acknowledge X, ask Y, maybe
   explain why, deferred items) + persona + product facts, generate 1–2 short sentences.
   Critical lines (readbacks, NATO email chunks, graduation summary) are **templated**.
   An output guard drops lines that mention tool names, JSON, stage directions, or
   unapproved claims before TTS/render.
6. **Persist** state + events in one transaction (version check), emit UI pushes over
   SSE, trace everything via `agent.obs`.

Latency budget (voice): extraction on a fast model (Haiku-class or Sonnet; decided in
FLOW-002 by eval), prompt caching on the stable system+tools prefix, a short spoken filler
if the LLM exceeds ~1.2s, hard timeout → templated re-ask.

## 6. Web app and UI pushes

- `SessionDriver` (browser) = `snapshot()`, `sendText()`, `startCall()`, `endCall()`,
  `onPush(cb)`. Mock driver (FE-001) → real driver (FE-002) over Next route handlers → agent API.
- UI pushes from the brain (SSE): `transcript`, `state` (checklist), `gmail_connect_card`,
  `call_state` (ringing/connected/reconnecting/ended), `graduate`.
- Gmail during a call: the brain's `push_gmail_connect` emits the card; the user clicks;
  Google OAuth (testing mode, reviewers as test users; scopes in §11) → Next.js callback → server-to-server
  `POST /v1/sessions/{id}/gmail` with the verified address → brain fills `gmail` → the live
  call hears "Got it — connected as p…@gmail.com". Fallback on voice: chunked spoken capture
  (Deepgram keyterm boost for domains/NATO words, code normalization "at"/"dot", chunked NATO
  readback, partial correction) → "type it" escape; spoken/typed stays `candidate` until OAuth.

## 7. Voice pipeline, barge-in, failover

`SmallWebRTC in → Deepgram STT → user aggregator (Silero VAD + Smart Turn) → Claude →
Cartesia TTS ⇄ ServiceSwitcher(failover → Deepgram TTS) → SmallWebRTC out → assistant aggregator`.

- Barge-in: Pipecat interruptions (browser WebRTC has AEC → no PSTN echo mute needed).
  Interrupted bot text is truncated in context by the assistant aggregator.
- Failover: TTS via `ServiceSwitcher` failover strategy; STT failover via a switcher or
  reconnect-with-backoff (VOICE-001 decides; Deepgram-only means a second STT vendor or
  a reconnect + filler); LLM timeout → filler → retry once → templated fallback.
- Silence floor: every node has spoken nudges (≈7s, ≈15s), then offers text and ends politely.
- Warmup at process boot (VAD/turn models, TLS to vendors).
- Graceful goodbye: end only after playout completes.
- Patterns (and, where useful, code — Darran's IP, copied from the sanitized mirror, never
  modifying penciled-emr) come from the Penciled voice-agent; see `docs/penciled-reference-map.md`.

## 8. Channel handoff, hangup resume, locking

- **One session** (httpOnly cookie + shareable resume link). Text and voice call the same
  brain; per-session turn serialization (in-process lock + DB version check).
- **Call lease**: starting a call sets `call_lease_holder` with a TTL heartbeat; a second
  tab/device gets "call in progress" + explicit take-over (EC-02, EC-09).
- **Hangup**: turn-level persistence ⇒ at most the in-flight turn is lost. On disconnect:
  mark call ended, release lease after the **reconnect grace window** (default 20s; a
  reconnect inside it resumes the same call with a "we got cut off" line), then the chat
  shows a resume message listing what's left (EC-01, EC-04).
- **Typing during a call** is merged into the same turn stream (EC-28).
- **Return visits** resume at the first missing slot or land in the main experience if
  graduated (EC-30, EC-31).

## 9. Hosting

| Piece | Host | Notes |
|---|---|---|
| web | Vercel | preview URL per branch (design-review gate) |
| agent | **EM decides after INFRA-001** (Fly.io+TURN / Railway / Pipecat Cloud) | long-lived, `min_machines_running=1`, no scale-to-zero mid-call |
| db | Supabase Postgres | migrations in `infra/supabase` |
| tracing/evals | Langfuse Cloud or self-hosted | optional; system runs with `PERSONA_TRACING=noop` |

**Risk**: SmallWebRTC is P2P (aiortc); container hosts often lack inbound UDP → need a
TURN service or Pipecat Cloud. INFRA-001 spikes this before any deploy.

## 10. Observability and evals (Langfuse behind thin interfaces) [decided]

- `agent/obs/tracing.py` — `Tracer` protocol (`start_trace`, `span`, `generation`, `score`,
  `flush`) with `NoopTracer`, `JsonlTracer` (offline/harness), `LangfuseTracer`
  (`agent/obs/langfuse_adapter.py`, the only core file that imports `langfuse`). One trace
  per turn, tagged with session id, channel, node, flow version, prompt hash. Voice: route
  Pipecat's OpenTelemetry spans to Langfuse's OTLP endpoint (OBS-001).
- `harness/evals/interface.py` — `EvalBackend` (`sync_dataset`, `record_run`, `compare`) with
  `LocalEvalBackend` and a Langfuse backend (OBS-002).
- **Dataset**: `harness/edge-cases.yaml` → Langfuse dataset `persona-onboarding-edge-cases`
  (one item per EC id; input = setup + script, expected = expected state/behavior).
- **Runs** named `<git-sha>|flow-v<N>|prompts-<hash>`; scores per item:
  `slot_correctness` (deterministic), `on_track` (deterministic progress check + LLM judge),
  `no_fabrication` (LLM judge + deterministic "never asserts values not in state").
  Judge: Claude via Anthropic SDK with `harness/evals/judge-rubric.yaml`.
- **Regression gate**: before READY on any prompt/flow change, compare the new run with the
  last accepted run; any per-score drop beyond tolerance blocks.

## 11. Security and privacy

- Session token on every mutating call; per-IP and per-session rate limits on session
  creation, turns, and call starts (vendor spend protection).
- **Gmail OAuth scopes [decided 2026-09-26 — read + write; half the product is automation]:**
  `openid email profile` (verify which account connected) + `gmail.readonly` (read inbox) +
  `gmail.modify` (organize: labels, archive, mark read, drafts) + `gmail.send` (send, only
  after explicit user confirmation). `access_type=offline`, `prompt=consent`,
  `include_granted_scopes=true`; handle partial grants (user unticks a scope → connected
  with reduced capability, stated plainly).
- **Google testing mode**: restricted scopes are fine unverified; reviewers are added as
  **test users** (≤100; Darran supplies emails). Expect Google's "unverified app" screen
  (design copy prepares for it) and **refresh tokens expire after 7 days** in testing mode →
  reconnect prompt on `invalid_grant`.
- Tokens: refresh token encrypted at rest (app-level key, AES-GCM; key in env, never in DB),
  access tokens in memory only; disconnect = Google revoke + delete stored tokens.
- Automation guard: no send/modify during onboarding without an explicit confirm turn; the
  value demo is read-only (never fabricates inbox facts — reads real metadata or says less).
- Prompt-injection resistance is structural (code-owned progress); the output guard blocks
  system-prompt disclosure and tool/JSON leakage.
- Product facts for privacy answers are a fixed, reviewed file (`docs/product-facts.md`).
- No secrets in repo; env names in `.env.example` only.

## 12. Model choices [proposed; FLOW-002 validates with evals]

- Voice extraction/phrasing: a low-latency Claude model (Haiku-class) with prompt caching;
  escalate to Sonnet if `slot_correctness` suffers.
- Text path: same prompts; may use a stronger model (latency tolerance higher).
- LLM judge: Opus 5.5 (`claude-opus-5-5`) offline only.
- Workers (Claude Code): Opus 5.5 (`claude-opus-5-5`), Fable 5 for C-class.
