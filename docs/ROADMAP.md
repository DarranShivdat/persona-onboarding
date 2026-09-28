# Roadmap

Worker-sized packets (≤ ~40 Opus turns each). Class: A (Grok), B-low (Grok Build),
B-high (Opus 5.5 worker), C (Fable). Role: impl / design / frontend.
Ready packets live in `docs/orchestration/packets/`.

## Deadline and timeline (all times PT) — revised Sun Sep 27, 8:25pm
**Submission due Mon Sep 28, 2026, 8:00pm ET = 5:00pm PT.** Target: hosted URL live and
working end to end by **Mon 12:00pm PT**.

Status Sun ~8:25pm: M1 ✅, M2 DESIGN-002 ✅ merged (PASS-WITH-FIXES → FE-005), VOICE-001..005 ✅,
FE-004 ✅, GMAIL-001 ✅, INFRA-002 ✅ merged (partial: env/migrate/Dockerfile/deploy dry-runs;
local-stack+RUNBOOK → INFRA-002c). Post-merge qa:fast+qa:flow green. **Call path now hosted
locally** (`POST /call` SDP answer + `agent.main` + `GET /ice` + local-call-smoke). Deploy still
waits on keys (Google OAuth + Cloudflare TURN remaining) + INFRA-002c runbook; EM will run
INFRA-002b `--apply` after that — not in this reconcile.

| When (PT) | Milestone | Owner |
|---|---|---|
| Sun ~8:25pm | **VOICE-005** ✅ merged + **INFRA-002** ✅ merged (kit) + **DESIGN-002** ✅ merged | done |
| Sun night → Mon 1am | **FE-005** (DESIGN-002 P1s) ∥ **INFRA-002c** (local-stack + RUNBOOK + OAuth/TURN docs) ∥ **VOICE-004-lite** (Gmail card on call + type-it) | Opus workers |
| Sun night, ASAP (latest Mon 7:00am) | Darran: remaining keys — **Google OAuth client** (testing mode) + reviewer test-user emails, **Cloudflare TURN key**; confirm Anthropic/Deepgram/Cartesia/Supabase already in. Go-ahead previously given once keys are in. | Darran |
| Mon 6:00–8:00am | EM runs INFRA-002b `scripts/deploy/* --apply` after INFRA-002c lands + keys: Supabase → Fly agent → Vercel web → Google redirect; **staging URL by 8:00am** | EM |
| Mon 8:00–10:00am | Hosted smoke: text flow, voice call (normal + TURN-only), Gmail OAuth test user, hangup/redial; fix-only from FE-005/VOICE-004-lite | EM + workers |
| Mon 10:00am–12:00pm | Hosted edge-case sweep (hangup, refusal, nonsense, early graduation, two tabs), latency tuning, fix-only; spend caps set | EM + workers |
| **Mon 12:00pm** | **Hosted URL live and end-to-end** | EM |
| Mon 12:00–3:00pm | Darran product test → fixes only; reviewer README + walkthrough script | Darran / EM |
| Mon 3:00pm | Final deploy frozen (critical fixes only) | EM |
| **Mon 5:00pm** | **Submit** (8pm ET) | Darran |

**Cut (decided Sun 8:30pm to protect the hosted E2E demo):**
- OBS-002 (Langfuse dataset sync / live runner / judge gate) → post-deadline. Tracing stays
  optional (`PERSONA_TRACING=noop` default; Langfuse only if keys are handy).
- HARNESS-003 automated voice fault injection → replaced by VOICE-005's Playwright
  fake-media call smoke + a manual hosted voice checklist (hangup, drop Wi-Fi, redial, barge-in).
- VOICE-004 spoken NATO email capture → cut; keep Gmail card pushed on the call + "type it" escape (VOICE-004-lite).
- DESIGN-002 hosted pass → done tonight on the local production build; 10-minute hosted glance Mon.
- INFRA-004 → reduced to existing per-IP/session rate limits + vendor-dashboard spend caps (Darran)
  + `min_machines_running=1`.

**Never cut:** text flow, browser voice call (with TURN), Gmail OAuth read+write (testing mode),
hangup resume, early graduation, steer-back.

## M0 — Foundation ✅ (this commit series)
Repo, flow spec + invariants, edge-case catalog, QA tiers, ported supervisor, docs.

## M1 — Brain (text-first, fully tested)
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **FLOW-001** ✅ merged | Pure flow engine + validators + TS types | B-high / impl | — | qa:flow 0 PENDING; flow-tier ECs implemented |
| **FLOW-002** ✅ merged | LLM adapter: `record_slots` extraction + constrained phrasing + output guard (Anthropic SDK), prompt caching, model choice by eval | B-high / impl | FLOW-001 | extraction contract tests; recorded-fixture tests; guard table tests |
| **FLOW-003** ✅ merged | Agent API (FastAPI) + Postgres store (version-checked turns, events, SSE) | B-high / impl | FLOW-001 | API tests w/ ephemeral Postgres; concurrency test (two writers) |
| **OBS-001** ✅ merged | Langfuse tracer adapter + Pipecat OTel routing | B-high / impl | FLOW-002 | traces visible with PERSONA_TRACING=langfuse; noop default unchanged |

## M2 — Web experience
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **DESIGN-001** ✅ merged | Research Persona's product/onboarding UI; spec + mockups | B-high / design | — | 11 states × 2 viewports; spec checklist; tokens.json |
| **FE-001** ✅ merged | Next.js shell from spec with mock driver; visual harness; Playwright | B-high / frontend | DESIGN-001 | build + e2e + qa:visual ≤3% diff |
| **FE-002** ✅ merged | Real SessionDriver: chat wired to agent API + SSE; refresh/resume | B-high / frontend | FE-001, FLOW-003 | EC-08, EC-30, EC-31 e2e |
| **FE-003** ✅ merged | Google OAuth (testing mode, reviewers as test users; `openid email profile` + `gmail.readonly` + `gmail.modify` + `gmail.send`, offline refresh token) + Gmail card states + wrong-account + partial-grant flow | B-high / frontend | FE-002 | EC-20, 21, 22 e2e |
| **GMAIL-001** ✅ merged | Server Gmail client: encrypted refresh-token store, refresh/`invalid_grant` → reconnect, revoke; read-only value demo from real metadata; draft/send/modify only behind an explicit confirm turn | B-high / impl | FLOW-003, FE-003 | unit tests w/ recorded Gmail API fixtures; no-send-without-confirm test |
| **DESIGN-002** ✅ merged | Design QA pass on the local production build (hosted glance Mon); copy tone review | B-high / design | FE-003 | PASS-WITH-FIXES (0 P0 / 9 P1); FE-005 implements |
| **FE-005** ▶running | Implement DESIGN-002 P1 fixes + copy table | B-high / frontend | DESIGN-002 | qa:e2e + qa:visual green; DQ-01..09 closed |

## M3 — Voice
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **INFRA-001** ✅ merged (voice host: Fly+Cloudflare TURN) | SmallWebRTC hosting spike (Fly+TURN vs Pipecat Cloud) → EM decides voice host | B-high / impl | — | decision record |
| **VOICE-001** ✅ merged | Pipecat pipeline: SmallWebRTC, Deepgram, Claude, Cartesia⇄Deepgram TTS failover, VAD/turn, warmup, idempotent teardown, graceful goodbye | B-high / impl | INFRA-001 | local call works; failover unit tests |
| **VOICE-002** ✅ merged | Pipecat Flows adapter over brain (nodes from spec; record_slots on every node) | B-high / impl | VOICE-001, FLOW-002 | shared-brain tests: same script text vs voice → same state |
| **VOICE-003** ✅ merged | Call lease, reconnect grace window, hangup resume, typing-during-call merge, silence floor | C? → B-high / impl | VOICE-002, FLOW-003 | EC-01, 02, 04, 10, 28 |
| **VOICE-004-lite** ▶running | Gmail on call: push card (read+write consent via OAuth) + "type it" escape. ~~Spoken NATO capture~~ cut for deadline | B-high / impl | VOICE-005 | EC-19/20/21 voice variants |
| **FE-004** ✅ merged | Phone simulator wired to real WebRTC (Pipecat JS client), mic-denied fallback | B-high / frontend | VOICE-001, FE-001 | EC-03, EC-09 e2e |

| **VOICE-005** ✅ merged | Host the pipeline behind `POST /call` (real SDP answer), `agent.main` entrypoint + env report, `GET /v1/ice` + browser ICE, local fake-media call smoke | B-high / impl | VOICE-001..003, FE-004 | qa:fast/flow/e2e + local-call-smoke PASS |

## M4 — Harness and evals
| ID | Title | Class/role | Depends | Acceptance |
|---|---|---|---|---|
| **HARNESS-001** ✅ merged | qa:convo runner (mock/replay) + LocalEvalBackend | B-high / impl | FLOW-001, FLOW-002 | convo-tier ECs pass offline |
| ~~OBS-002~~ cut (post-deadline) | Langfuse dataset sync + live runner + LLM judge + run comparison gate | B-high / impl | HARNESS-001, OBS-001 | `qa:live` creates a dataset run with 3 scores; compare works |
| ~~HARNESS-003~~ cut (manual hosted checklist + VOICE-005 smoke instead) | Headless voice client + fault injection (hangup/drop/silence/barge-in/vendor fail) | B-high / impl | VOICE-002 | voice-tier ECs pass locally |

## M5 — Hosting (requires Darran go-ahead for remotes/deploys — needed ASAP, latest Mon 7am PT)
| ID | Title | Class/role | Depends |
|---|---|---|---|
| **INFRA-002** ✅ merged (partial) | Deploy kit (no cloud): env contract, check-env, migrations, Dockerfile/fly.toml, dry-run deploy scripts + smoke | B-high / impl | INFRA-001, FLOW-003 |
| **INFRA-002c** ▶running | local-stack.sh + RUNBOOK.md + google-oauth.md + cloudflare-turn.md (INFRA-002 remainder) | B-high / impl | INFRA-002 |
| INFRA-002b | Execute the runbook (`--apply`) after Darran's go-ahead: Supabase → Fly agent → Vercel web → Google redirect | A (EM) | INFRA-002, VOICE-005 |
| INFRA-003 | Vercel project + preview URLs (folded into INFRA-002b; gate automation cut) | B-low | FE-002 |
| INFRA-004 (reduced) | Existing rate limits + vendor spend caps (Darran) + min 1 machine; alerts cut | A (EM) + Darran | INFRA-002b |

## M6 — Hardening and reviewer handoff
Full edge-case sweep (all 31 green across tiers), red-team session (A-class scripted +
Grok adversarial), latency tuning, reviewer README + Loom-style walkthrough script,
READY FOR PRODUCT TEST to Darran.

## Parallelism
Now (Sun ~8:25pm PT): FE-005 ∥ INFRA-002c ∥ VOICE-004-lite (disjoint: apps/web polish /
scripts+docs/deploy / voice+web Gmail-on-call). VOICE-005 + INFRA-002 + DESIGN-002 merged
this reconcile. Next: INFRA-002b deploy (EM, after keys + INFRA-002c) → hosted smoke.

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
- Remaining keys ASAP (latest Mon 7am PT): **Google OAuth client** (testing mode) + reviewer
  test-user emails, **Cloudflare TURN** key. Anthropic/Deepgram/Cartesia/Supabase already
  collected; go-ahead given once keys are in. See docs/deploy/ENV.md.
- product-facts.md: Settings→Disconnect vs no Settings surface (DESIGN-002 DQ-03); voice retention wording.
