# 0001 — Voice hosting for the SmallWebRTC agent

- Status: proposed (INFRA-001 spike). EM (Grok) decides; Darran delegated the call.
- Date: 2026-09-26
- Deadline it must fit: hosted URL live by **Mon Sep 28 noon PT**.
- Artifacts: `services/agent/agent/voice/spike_echo.py`, `infra/voice-spike/`
  (`call_proof.py`, `fly.spike.toml`, `spike.Dockerfile`, `README.md`).

## Problem
SmallWebRTC (aiortc) is peer-to-peer: the server itself is an ICE peer. Container hosts
usually give no inbound UDP on arbitrary ports, and reviewers may call from networks
that block UDP entirely. We need a host where a browser call connects from a home
network *and* a UDP-blocked network, fast to stand up, without re-architecting VOICE-001.

## What was measured (local, 2026-09-26)
`python infra/voice-spike/call_proof.py` — Chromium headless shell (Playwright rev 1234)
with `--use-fake-device-for-media-stream`, Pipecat 1.4.0, Python 3.13, macOS:

| Check | Result |
|---|---|
| peer connection state | `connected` in **389 ms** (offer → connected, non-trickle, ≤4s gather cap) |
| selected pair | host ↔ host, UDP, RTT ~0 ms (loopback) |
| bot → browser audio | 44,391 bytes inbound-rtp (greeting tone + echo) |
| browser → bot audio | 198 input audio frames at the server |
| server candidates gathered | host, srflx (STUN) |
| hangup | server teardown fired, `ended_reason = "client disconnected"` |

**Not measured yet** (needs TURN creds and/or a deploy, both gated on Darran's go-ahead):
`--relay` (TURN-only) run, a hosted run, and a true UDP-blocked network run. The harness
and exact commands for all three are ready (`infra/voice-spike/README.md`).

## Key technical finding
On a host without inbound UDP the **server must use TURN too**, not just the browser.
A browser-only relay candidate still has to reach the server's host candidate, which is
unreachable. The spike therefore hands the *same* ICE list to both peers (`GET /api/ice`
for the browser; the same list into `SmallWebRTCConnection`). TURN must offer
TCP/TLS on 443 so UDP-blocked callers work (both legs then relay via TURN; the server's
egress to TURN is outbound, which every host allows).

## Options

| | Fly.io + Cloudflare TURN | Railway + Cloudflare TURN | Pipecat Cloud |
|---|---|---|---|
| Inbound UDP | UDP proxy exists but needs dedicated IPv4 + fixed ports; aiortc uses random ports → effectively none | none | managed (Daily transport by default) |
| Works for UDP-blocked callers | yes, via TURN TCP/TLS 443 on both legs | yes, same | yes with Daily (Daily runs TURN); SmallWebRTC on PCC still needs our TURN |
| Long-lived process / websockets | yes (`min_machines_running = 1`, no auto-stop) | yes | yes (agent sessions spawned per call) |
| Code change vs our design | none — spike runs as-is (`fly.spike.toml`) | none — same Dockerfile | moderate: PCC's session/bot entrypoint model; brain + text API must live elsewhere or be adapted |
| Setup time (expected) | ~30–60 min incl. TURN key | ~30–60 min | ~2–4 h + account/onboarding; unknown unknowns |
| Cost (expected, trial scale) | shared-cpu-1x 512 MB ≈ $3–5/mo; TURN: Cloudflare 1 TB/mo free tier then $0.05/GB — ~$0 for review traffic | ~$5/mo hobby + usage; TURN same | per-minute agent pricing + Daily minutes; small at trial scale but metered |
| Region / latency | pick `sjc` (near Anthropic/Deepgram/Cartesia US) | US regions | managed |
| Ops risk | one box = SPOF (fine for trial); machine restart drops in-flight call (brain state persists in Postgres) | same | least ops, most coupling |

TURN alternatives (env names only; all supported by the spike's static path):
Twilio Network Traversal (`PERSONA_TURN_URLS` + `PERSONA_TURN_USERNAME` /
`PERSONA_TURN_CREDENTIAL`, ~$0.40/GB), metered.ca (same env, free 500 MB/mo), self-hosted
coturn (not worth it for the deadline). Cloudflare (`CLOUDFLARE_TURN_KEY_ID`,
`CLOUDFLARE_TURN_API_TOKEN`) is preferred: short-lived per-call creds minted server-side,
443 TCP/TLS URLs included, generous free tier.

## Recommendation
**Fly.io (`sjc`, 1 always-on machine) + Cloudflare Realtime TURN on both legs.**
Fastest reliable path: zero code change from the spike, same container runs the text
API and voice, no platform coupling, predictable cost. Railway is an equivalent
fallback (same Dockerfile, same env) if Fly account setup stalls. Pipecat Cloud is not
recommended for this deadline: it pulls the architecture toward its session model and
Daily transport, which conflicts with "one brain, one state" living in our service.

## Exact next steps (in order; steps 2+ need Darran's go-ahead — creates cloud resources)
1. EM accepts/overrides this record.
2. Cloudflare dashboard → Realtime → TURN → create key; store `CLOUDFLARE_TURN_KEY_ID`,
   `CLOUDFLARE_TURN_API_TOKEN` in the secret store (never commit).
3. Local relay proof: `python infra/voice-spike/call_proof.py --relay` → expect
   `pair.local == "relay"`, `PASS`.
4. `fly apps create persona-voice-spike`; `fly secrets set` the two TURN vars +
   `PERSONA_SPIKE_TOKEN`; `fly deploy -c infra/voice-spike/fly.spike.toml .`
5. Hosted proofs: `call_proof.py --url https://persona-voice-spike.fly.dev --token …`
   and the same with `--relay`.
6. Manual UDP-blocked test (pf rule or VPN'd hotspot, see README); confirm
   `relayProtocol` is `tcp`/`tls`.
7. Record measured numbers here, `fly apps destroy persona-voice-spike`, then carry the
   ICE rules (same list both peers, 443 TCP/TLS, fresh PC per call, idempotent teardown)
   into VOICE-001 and the production Fly app.

## Risks
- Relay and hosted paths are **expected, not yet measured**; the one measured run is loopback.
- TURN relays add latency (≈ +20–60 ms RTT via Cloudflare anycast; TCP/TLS worse under loss).
- Cloudflare TURN creds are minted per `/api/ice` call; if the Cloudflare API is down the
  server falls back to the static `PERSONA_TURN_*`/STUN config (STUN-only if none is set,
  so UDP-blocked callers would fail) — set a static TURN fallback in production.
- `PERSONA_SPIKE_TOKEN` is a shared secret in a URL query — spike-only, not the product auth.
