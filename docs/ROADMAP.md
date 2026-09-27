# Roadmap

Worker-sized packets (≤ ~40 Opus turns each). Class: A (Grok), B-low (Grok Build),
B-high (Opus 5.5 worker), C (Fable). Role: impl / design / frontend.
Ready packets live in `docs/orchestration/packets/`.

## Deadline and timeline (all times PT)
**Submission due Mon Sep 28, 2026, 8:00pm ET = 5:00pm PT.** Target: hosted URL live and
feature-complete by **Mon 12:00pm PT** (5h buffer).

| When (PT) | Milestone | Owner |
|---|---|---|
| Sat Sep 26, 7pm–midnight | DESIGN-001 ∥ FLOW-001 ∥ INFRA-001 running; EM reviews as they land; **EM picks voice host** from INFRA-001 | Opus workers / EM |
| Sun Sep 27, by 10am | Darran: accounts + keys (Anthropic, Deepgram, Cartesia, Google Cloud OAuth client in testing mode + reviewer test-user emails, Supabase, Vercel, chosen voice host) and **go-ahead for remotes/deploys** | Darran |
| Sun, morning | FLOW-002 ∥ FLOW-003 ∥ FE-001 (from DESIGN-001 spec) | workers |
| Sun, afternoon | FE-002 ∥ VOICE-001 ∥ HARNESS-001; INFRA-002/003 first deploy → **staging URL by Sun 8pm** | workers / EM |
| Sun, evening–night | FE-003 + GMAIL-001 (OAuth, read+write) ∥ VOICE-002 ∥ OBS-001; then VOICE-003/004, FE-004 | workers |
| Mon Sep 28, 6am–noon | Edge-case sweep (all tiers), red-team, latency tuning, INFRA-004 limits; **hosted URL live by 12:00pm** | EM + workers |
| Mon, 12–3pm | Darran product test → fixes only; reviewer README + walkthrough | Darran / EM |
| Mon, 3pm | Final deploy frozen (only critical fixes after) | EM |
| **Mon, 5:00pm** | **Submit** (8pm ET) | Darran |

Cut line (if staging URL slips past Sun 8pm PT), cut in this order: OBS-002 live eval
runs → HARNESS-003 automated voice fault injection → qa:visual pixel diff → DESIGN-002.
Never cut: text flow, browser voice call, Gmail OAuth (read+write), hangup resume,
early graduation, steer-back.

## M0 — Foundation ✅ (this commit series)
Repo, flow spec + invariants, edge-case catalog, QA tiers, ported supervisor, docs.

## M1 — Brain (text-first, fully tested)
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **FLOW-001** ✅ merged | Pure flow engine + validators + TS types | B-high / impl | — | qa:flow 0 PENDING; flow-tier ECs implemented |
| **FLOW-002** ▶running | LLM adapter: `record_slots` extraction + constrained phrasing + output guard (Anthropic SDK), prompt caching, model choice by eval | B-high / impl | FLOW-001 | extraction contract tests; recorded-fixture tests; guard table tests |
| **FLOW-003** ▶running | Agent API (FastAPI) + Postgres store (version-checked turns, events, SSE) | B-high / impl | FLOW-001 | API tests w/ ephemeral Postgres; concurrency test (two writers) |
| OBS-001 | Langfuse tracer adapter + Pipecat OTel routing | B-low→B-high / impl | FLOW-002 | traces visible with PERSONA_TRACING=langfuse; noop default unchanged |

## M2 — Web experience
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **DESIGN-001** ✅ merged | Research Persona's product/onboarding UI; spec + mockups | B-high / design | — | 11 states × 2 viewports; spec checklist; tokens.json |
| **FE-001** ▶running | Next.js shell from spec with mock driver; visual harness; Playwright | B-high / frontend | DESIGN-001 | build + e2e + qa:visual ≤3% diff |
| FE-002 | Real SessionDriver: chat wired to agent API + SSE; refresh/resume | B-high / frontend | FE-001, FLOW-003 | EC-08, EC-30, EC-31 e2e |
| FE-003 | Google OAuth (testing mode, reviewers as test users; `openid email profile` + `gmail.readonly` + `gmail.modify` + `gmail.send`, offline refresh token) + Gmail card states + wrong-account + partial-grant flow | B-high / frontend | FE-002 | EC-20, 21, 22 e2e |
| GMAIL-001 | Server Gmail client: encrypted refresh-token store, refresh/`invalid_grant` → reconnect, revoke; read-only value demo from real metadata; draft/send/modify only behind an explicit confirm turn | B-high / impl | FLOW-003, FE-003 | unit tests w/ recorded Gmail API fixtures; no-send-without-confirm test |
| DESIGN-002 | Design QA pass on hosted preview; copy tone review | B-high / design | FE-003 | gate report PASS; Darran taste question prepared |

## M3 — Voice
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **INFRA-001** ✅ merged (voice host: Fly+Cloudflare TURN) | SmallWebRTC hosting spike (Fly+TURN vs Pipecat Cloud) → EM decides voice host | B-high / impl | — | decision record |
| VOICE-001 | Pipecat pipeline: SmallWebRTC, Deepgram, Claude, Cartesia⇄Deepgram TTS failover, VAD/turn, warmup, idempotent teardown, graceful goodbye | B-high / impl | INFRA-001 | local call works; failover unit tests |
| VOICE-002 | Pipecat Flows adapter over brain (nodes from spec; record_slots on every node) | B-high / impl | VOICE-001, FLOW-002 | shared-brain tests: same script text vs voice → same state |
| VOICE-003 | Call lease, reconnect grace window, hangup resume, typing-during-call merge, silence floor | C? → B-high / impl | VOICE-002, FLOW-003 | EC-01, 02, 04, 10, 28 |
| VOICE-004 | Gmail on call: push card (read+write consent via OAuth); spoken email fallback (keyterms, NATO chunks, partial correction, type-it) | B-high / impl | VOICE-002, FE-003 | EC-19/20/21 voice variants |
| FE-004 | Phone simulator wired to real WebRTC (Pipecat JS client), mic-denied fallback | B-high / frontend | VOICE-001, FE-001 | EC-03, EC-09 e2e |

## M4 — Harness and evals
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **HARNESS-001** ▶ready (after FLOW-001/002) | qa:convo runner (mock/replay) + LocalEvalBackend | B-high / impl | FLOW-001, FLOW-002 | convo-tier ECs pass offline |
| OBS-002 | Langfuse dataset sync + live runner + LLM judge + run comparison gate | B-high / impl | HARNESS-001, OBS-001 | `qa:live` creates a dataset run with 3 scores; compare works |
| HARNESS-003 | Headless voice client + fault injection (hangup/drop/silence/barge-in/vendor fail) | B-high / impl | VOICE-002 | voice-tier ECs pass locally |

## M5 — Hosting (requires Darran go-ahead for remotes/deploys — needed by Sun Sep 27 10am PT)
| ID | Title | Class/role | Depends |
|---|---|---|---|
| INFRA-002 | Supabase project + migrations; agent deploy on the EM-chosen voice host | B-high / impl | INFRA-001, FLOW-003 |
| INFRA-003 | Vercel project + preview URLs + design-review gate automation | B-low | FE-002 |
| INFRA-004 | Rate limits, spend caps, abuse controls, health/alerts | B-high / impl | INFRA-002 |

## M6 — Hardening and reviewer handoff
Full edge-case sweep (all 31 green across tiers), red-team session (A-class scripted +
Grok adversarial), latency tuning, reviewer README + Loom-style walkthrough script,
READY FOR PRODUCT TEST to Darran.

## Parallelism
Now: FE-001 ∥ FLOW-002 ∥ FLOW-003 (DESIGN-001 / FLOW-001 / INFRA-001 merged 2026-09-26 evening PT; voice host accepted: Fly.io + Cloudflare TURN). Then HARNESS-001 (after FLOW-002), VOICE-001.

## Decisions (resolved by Darran, 2026-09-26)
1. **Gmail scopes: read + write** (half the product is automation): `openid email profile`
   + `gmail.readonly` + `gmail.modify` + `gmail.send`, Google **testing mode** with
   reviewers as test users (Darran supplies emails). Refresh token required, encrypted.
   See ARCHITECTURE §11 and `docs/product-facts.md`.
2. **Voice host: the EM decides after INFRA-001** (Fly+TURN vs Railway vs Pipecat Cloud),
   optimizing for "reviewers can call from anywhere by Mon noon PT".
3. **Worker model**: `claude-opus-5-5`, verified 2026-09-26 with `--probe-model` on
   Claude Code 2.1.283 (native install `~/.local/bin/claude`; the wrapper prefers it).
4. **Penciled code**: Darran's IP — workers may copy from `penciled-emr/voice-agent` via the
   sanitized read-only mirror; never modify penciled-emr; never touch .env/transcripts/
   data/demo patients/PHI. Enforced by `.claude/settings.json` + guard hook.
5. **Product identity**: Persona by Zach Yadegari — https://yourpersona.com (personal AI
   assistant; Persona Band wearable). Not usepersona.app.
6. **Deadline**: Mon Sep 28, 2026, 5pm PT (8pm ET); hosted URL by Mon noon PT.

## Still open (D — Darran)
- Reviewer Google account emails (test users) and account/key provisioning (Sun 10am PT).
- product-facts.md approval (esp. voice retention wording).
