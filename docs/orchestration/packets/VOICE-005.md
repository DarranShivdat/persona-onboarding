# VOICE-005 — Host the voice pipeline behind the agent API (real SDP answer) + entrypoint + browser ICE

CLASS: B-high (critical path)
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/voice-005
BRANCH: claude/voice-005
DEPENDS ON: VOICE-001/002/003, FE-004, GMAIL-001 (all merged at main 29ce352+)

## TASK
Make a browser call actually connect through the agent API. Today `POST /v1/sessions/{id}/call`
acquires the lease but returns `answer: null` (placeholder in agent/api/app.py), there is no
`agent/main.py` although infra/agent.Dockerfile runs `python -m agent.main`, and the browser
only gets static STUN (`NEXT_PUBLIC_PERSONA_ICE_URLS`), so UDP-blocked callers cannot relay.
1. On `POST /call` with `{sdp, type}`: create a SmallWebRTC connection using the ICE servers
   from `agent/voice/ice.py` (Cloudflare short-lived creds or static TURN, else STUN), build
   and start `CallSession` for this session/call (shared brain, Flows adapter), wire
   `CallSession.attach(calls, session_id)` (heartbeat / grace / hangup from VOICE-003), and
   return the SDP answer. Keep lease-only behavior (`answer: null`) when voice is not
   configured (no vendor keys and not `PERSONA_VOICE_FAKE_VENDORS=1`).
   Handle take-over (stop the replaced call's pipeline) and `resume_call_id` renegotiation.
   Support trickle/renegotiation only if SmallWebRTC needs it (`PATCH`/ICE candidate route).
2. `GET /v1/ice` (session token required) → `{ice_servers: [...], ttl_s}` for the browser leg,
   minted per request (Cloudflare) or static; add the Next same-origin proxy
   `apps/web/app/api/session/ice/route.ts` and make `api-driver.ts` fetch it before creating
   the offer (fallback to `NEXT_PUBLIC_PERSONA_ICE_URLS`, then Google STUN).
3. `services/agent/agent/main.py`: `python -m agent.main --host 0.0.0.0 --port 8080` →
   uvicorn on `create_app_from_env()`, boot warmup (best-effort, time-boxed), a startup env
   report that lists MISSING variable NAMES (never values) and which features are disabled
   (voice / gmail tokens / tracing), `/health` unchanged (must answer while warming).
4. Local end-to-end proof: agent (local Postgres, FakeLlm, `PERSONA_VOICE_FAKE_VENDORS=1`) +
   `next start` + Playwright Chromium with `--use-fake-device-for-media-stream` →
   call connects (bot answer applied, `call_state=connected`), a greeting is heard/captioned
   (fake vendors), hangup releases the lease and the chat shows the resume message.
   Add it as a Playwright spec that is skipped unless `PERSONA_E2E_REAL_AGENT_URL` is set,
   and a script `scripts/local-call-smoke.sh` that brings the stack up, runs it, tears down.

## WHY
"Browser voice call" is a never-cut requirement and the hosted demo is due Mon Sep 28 noon PT.
Everything else in voice is merged but not reachable from the browser through the API.

## SCOPE
services/agent/agent/api/**, services/agent/agent/voice/** (glue only; keep VOICE-003
semantics), services/agent/agent/main.py (new), services/agent/tests/** (new tests),
apps/web/app/api/session/ice/** (new), apps/web/lib/session/{api-driver.ts,webrtc.ts},
apps/web/e2e/** (new real-agent spec), scripts/local-call-smoke.sh (new).
Do NOT edit infra/** (INFRA-002 owns Dockerfile/fly.toml/.env.example/deploy scripts),
docs/design/**, or packages/flow/**.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §6–§9; docs/decisions/0001-voice-hosting.md
- services/agent/agent/api/{app.py,README.md}; services/agent/agent/voice/{session.py,handoff.py,
  ice.py,config.py,fakes.py,spike_echo.py (working SmallWebRTC offer/answer example)}
- apps/web/lib/session/{api-driver.ts,webrtc.ts}; apps/web/app/api/session/call/**; apps/web/e2e/{call.ts,live.ts}
- Optional (copy allowed, Darran's IP): /Users/darranshivdat/IdeaProjects/persona-onboarding-ref/penciled-voice-agent/bot.py
  (SmallWebRTC offer handling, idempotent teardown, pc_id reuse), warmup.py, static/phone.html

## DO NOT READ
- .env*, secrets; Penciled sensitive paths (.env*, transcripts/, data/, demo patients, PHI)
  + other Penciled repos; never modify penciled-emr

## REQUIREMENTS
- Never two live pipelines per session (lease is the gate; take-over stops the old one first).
- One brain, one state: the pipeline's Flows handlers go through the same SessionService /
  brain as text turns (VOICE-002). No transition logic in the browser.
- Teardown is idempotent on client disconnect, hangup, take-over, max duration, and process
  shutdown (lifespan) — no leaked peer connections or tasks (test it).
- ICE: `PERSONA_TURN_URLS`/`PERSONA_TURN_USERNAME`/`PERSONA_TURN_CREDENTIAL` or
  `CLOUDFLARE_TURN_KEY_ID`/`CLOUDFLARE_TURN_API_TOKEN`; the server leg uses the same TURN.
  Never log credentials; the ICE route is rate-limited and session-authenticated.
- Env report prints NAMES only. No new runtime deps without noting them in OUTPUT.
- Offline tests (fake vendors / fake transport); no vendor network in qa tiers.
- Ports: use agent :8300 and web :3300 for your local stack/smoke (other workers use 3100, 3200/8200);
  scratch Postgres DB name `persona_voice005`.

## ACCEPTANCE
- [ ] `npm run qa:fast`, `npm run qa:flow`, `npm run qa:e2e` green
- [ ] new API tests: call with sdp returns an answer when voice configured (fake vendors), lease-only
      otherwise; take-over stops the old pipeline; hangup/drop go through VOICE-003 paths; ICE route auth
- [ ] `python -m agent.main --help` works; `/health` answers during warmup
- [ ] `scripts/local-call-smoke.sh` passes locally (document exact command + output in OUTPUT)
- [ ] no cloud resources; no push

## OUTPUT
Commit (no push). STATUS / FILES / SUMMARY / HOW TO RUN LOCALLY / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 50 turns (raised from 40: critical path, three prior voice/infra packets hit the 40 cap).
