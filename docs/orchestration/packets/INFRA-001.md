# INFRA-001 — Spike: SmallWebRTC voice from a hosted agent (Fly.io vs Pipecat Cloud)

CLASS: B-high (spike)
MODEL: opus
ROLE: impl
WORKTREE: ../persona-onboarding-worktrees/infra-001
BRANCH: claude/infra-001
DEPENDS ON: none (can run parallel to FLOW-001)

## TASK
Prove (or disprove) that a minimal Pipecat SmallWebRTC echo/greeting bot deployed as a
long-lived service can hold a browser call from a normal home network and from a
restrictive network (UDP blocked), and document the required ICE/TURN configuration.
Produce a decision record with a recommendation: Fly.io (+TURN) vs Railway vs Pipecat Cloud.

## WHY
Top hosting risk: SmallWebRTC is peer-to-peer (aiortc); container hosts often lack
inbound UDP. Reviewers must be able to call from anywhere.

## SCOPE
infra/**, services/agent/agent/voice/spike_echo.py, docs/decisions/0001-voice-hosting.md.

## READ
- CLAUDE.md; docs/ARCHITECTURE.md §7, §9; infra/README.md; Pipecat public docs for
  SmallWebRTCTransport and Pipecat Cloud.

## DO NOT READ
- .env*, secrets; any Penciled repository

## REQUIREMENTS
- Local-only proof first (no deploy without Darran's go-ahead; this packet must not
  create cloud resources or push). Deliver deploy steps + config ready to run.
- Include TURN option (e.g. Cloudflare Realtime TURN, Twilio NTS, metered) with env names only.

## ACCEPTANCE
- [ ] local echo bot call works in Chromium via `--use-fake-device-for-media-stream`
- [ ] decision record lists measured/expected tradeoffs, costs, and exact next steps
- [ ] `npm run qa:fast`

## CONSTRAINTS
No cloud resource creation; no pushes; env var names only.

## OUTPUT
Commit (no push). STATUS / FILES / DECISION / RISKS / PRODUCT_DECISION_REQUIRED.

## BUDGET
Opus 30 turns.
