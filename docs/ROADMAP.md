# Roadmap

Worker-sized packets (≤ ~40 Opus turns each). Class: A (Grok), B-low (Grok Build),
B-high (Opus 5.5 worker), C (Fable). Role: impl / design / frontend.
Ready packets live in `docs/orchestration/packets/`.

## M0 — Foundation ✅ (this commit series)
Repo, flow spec + invariants, edge-case catalog, QA tiers, ported supervisor, docs.

## M1 — Brain (text-first, fully tested)
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **FLOW-001** ▶ready | Pure flow engine + validators + TS types | B-high / impl | — | qa:flow 0 PENDING; flow-tier ECs implemented |
| FLOW-002 | LLM adapter: `record_slots` extraction + constrained phrasing + output guard (Anthropic SDK), prompt caching, model choice by eval | B-high / impl | FLOW-001 | extraction contract tests; recorded-fixture tests; guard table tests |
| FLOW-003 | Agent API (FastAPI) + Postgres store (version-checked turns, events, SSE) | B-high / impl | FLOW-001 | API tests w/ ephemeral Postgres; concurrency test (two writers) |
| OBS-001 | Langfuse tracer adapter + Pipecat OTel routing | B-low→B-high / impl | FLOW-002 | traces visible with PERSONA_TRACING=langfuse; noop default unchanged |

## M2 — Web experience
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **DESIGN-001** ▶ready | Research Persona's product/onboarding UI; spec + mockups | B-high / design | — | 11 states × 2 viewports; spec checklist; tokens.json |
| **FE-001** ▶ready (after DESIGN-001) | Next.js shell from spec with mock driver; visual harness; Playwright | B-high / frontend | DESIGN-001 | build + e2e + qa:visual ≤3% diff |
| FE-002 | Real SessionDriver: chat wired to agent API + SSE; refresh/resume | B-high / frontend | FE-001, FLOW-003 | EC-08, EC-30, EC-31 e2e |
| FE-003 | Google OAuth (testing mode) + Gmail card states + wrong-account flow | B-high / frontend | FE-002 | EC-20, 21, 22 e2e |
| DESIGN-002 | Design QA pass on hosted preview; copy tone review | B-high / design | FE-003 | gate report PASS; Darran taste question prepared |

## M3 — Voice
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **INFRA-001** ▶ready (spike) | SmallWebRTC hosting spike (Fly+TURN vs Pipecat Cloud) | B-high / impl | — | decision record |
| VOICE-001 | Pipecat pipeline: SmallWebRTC, Deepgram, Claude, Cartesia⇄Deepgram TTS failover, VAD/turn, warmup, idempotent teardown, graceful goodbye | B-high / impl | INFRA-001 | local call works; failover unit tests |
| VOICE-002 | Pipecat Flows adapter over brain (nodes from spec; record_slots on every node) | B-high / impl | VOICE-001, FLOW-002 | shared-brain tests: same script text vs voice → same state |
| VOICE-003 | Call lease, reconnect grace window, hangup resume, typing-during-call merge, silence floor | C? → B-high / impl | VOICE-002, FLOW-003 | EC-01, 02, 04, 10, 28 |
| VOICE-004 | Gmail on call: push card; spoken email fallback (keyterms, NATO chunks, partial correction, type-it) | B-high / impl | VOICE-002, FE-003 | EC-19/20/21 voice variants |
| FE-004 | Phone simulator wired to real WebRTC (Pipecat JS client), mic-denied fallback | B-high / frontend | VOICE-001, FE-001 | EC-03, EC-09 e2e |

## M4 — Harness and evals
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **HARNESS-001** ▶ready (after FLOW-001/002) | qa:convo runner (mock/replay) + LocalEvalBackend | B-high / impl | FLOW-001, FLOW-002 | convo-tier ECs pass offline |
| OBS-002 | Langfuse dataset sync + live runner + LLM judge + run comparison gate | B-high / impl | HARNESS-001, OBS-001 | `qa:live` creates a dataset run with 3 scores; compare works |
| HARNESS-003 | Headless voice client + fault injection (hangup/drop/silence/barge-in/vendor fail) | B-high / impl | VOICE-002 | voice-tier ECs pass locally |

## M5 — Hosting (requires Darran go-ahead for remotes/deploys)
| ID | Title | Class/role | Depends |
|---|---|---|---|
| INFRA-002 | Supabase project + migrations; agent deploy per INFRA-001 decision | B-high / impl | INFRA-001, FLOW-003 |
| INFRA-003 | Vercel project + preview URLs + design-review gate automation | B-low | FE-002 |
| INFRA-004 | Rate limits, spend caps, abuse controls, health/alerts | B-high / impl | INFRA-002 |

## M6 — Hardening and reviewer handoff
Full edge-case sweep (all 31 green across tiers), red-team session (A-class scripted +
Grok adversarial), latency tuning, reviewer README + Loom-style walkthrough script,
READY FOR PRODUCT TEST to Darran.

## Parallelism
Now: DESIGN-001 ∥ FLOW-001 ∥ INFRA-001 (disjoint scopes). Then FE-001 (after DESIGN-001),
FLOW-002/003, HARNESS-001.

## Open decisions (D — Darran)
1. **Gmail scopes**: identity-only (email verification; simplest, no restricted-scope
   review) vs `gmail.readonly` (enables a real value demo; restricted scope, fine in testing
   mode with test users). Recommendation: identity + `gmail.readonly` in testing mode, value
   demo reads only message counts/subjects with explicit consent copy.
2. **Voice host**: after INFRA-001 (Fly+TURN vs Pipecat Cloud).
3. **Model id for workers**: Opus 5.5 = `claude-opus-5-5` per Anthropic docs; confirm the
   installed CLI serves it with `./scripts/claude-worker.sh opus --probe-model` (one tiny call)
   or update Claude Code first (installed 2.1.209 predates Opus 5.5).
4. **Penciled code reuse**: currently reference-only. Written permission would let VOICE-001
   port specific modules (see penciled-reference-map.md) and save time.
5. **Persona product identity** for DESIGN-001 (usepersona.app assumed; confirm against the
   "Persona — CTO Trial" tab).
