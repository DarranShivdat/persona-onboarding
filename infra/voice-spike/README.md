# infra/voice-spike — INFRA-001 SmallWebRTC hosting spike

Echo/greeting bot (`services/agent/agent/voice/spike_echo.py`) + a Chromium call proof.
No vendor keys needed. Decision record: `docs/decisions/0001-voice-hosting.md`.

## Local proof (done, passes)

```
# needs: pipecat-ai[webrtc] fastapi uvicorn httpx loguru playwright (python)
python infra/voice-spike/call_proof.py
# if pip playwright's pinned Chromium isn't downloaded, point at any Chromium:
SPIKE_CHROMIUM=~/Library/Caches/ms-playwright/chromium_headless_shell-<rev>/chrome-headless-shell-mac-arm64/chrome-headless-shell \
  python infra/voice-spike/call_proof.py
```

Chromium runs with `--use-fake-device-for-media-stream`; the proof asserts `connected`,
inbound audio growing at the browser (greeting chime + echo), caller audio frames at
the server, and server-side teardown after hangup. Prints the selected candidate pair.

Manual: `cd services/agent && python -m agent.voice.spike_echo` → http://127.0.0.1:7860/

## ICE / TURN configuration (env var names only)

| Env var | Used by | Meaning |
|---|---|---|
| `CLOUDFLARE_TURN_KEY_ID` | server | Cloudflare Realtime TURN key id (recommended) |
| `CLOUDFLARE_TURN_API_TOKEN` | server | its API token; server mints short-lived creds per call (TTL 1h) |
| `PERSONA_TURN_URLS` | server | static alternative: comma-separated `turn:`/`turns:` URLs (Twilio NTS, metered.ca, coturn) |
| `PERSONA_TURN_USERNAME` / `PERSONA_TURN_CREDENTIAL` | server | static TURN creds |
| `PERSONA_STUN_URLS` | server | STUN list, default `stun:stun.l.google.com:19302` |
| `PERSONA_SPIKE_TOKEN` | server | optional shared secret required on `POST /api/offer` (`X-Spike-Token` / `?token=`) |
| `PERSONA_SPIKE_MAX_CALL_SECS` | server | hard cap per call (default 300) |

Rules the spike encodes (carry into VOICE-001):
1. **Both peers get the same ICE list.** The browser fetches `GET /api/ice`; aiortc on the
   server gets the same list. On a host with no inbound UDP the *server* must allocate a
   TURN relay too — a client-only relay still has to reach the server's unreachable host
   candidate.
2. **TURN URLs must include TCP/TLS 443** (`turn:...:443?transport=tcp`, `turns:...:443`)
   so UDP-blocked networks work. Cloudflare returns these; drop its `:53` URLs (browsers
   block port 53; the server filters them).
3. Non-trickle signalling: client waits for ICE gathering (≤4s cap) before POSTing the
   offer; aiortc answers after its own gathering.
4. Fresh `RTCPeerConnection` per call; server teardown is idempotent and fires on
   disconnect / max duration / pipeline end.

## Restrictive-network proof (ready to run; needs TURN creds)

```
# local server, TURN creds in env (never commit them):
python infra/voice-spike/call_proof.py --relay          # iceTransportPolicy=relay; asserts pair.local == relay
# against a deployed spike:
python infra/voice-spike/call_proof.py --url https://persona-voice-spike.fly.dev --relay --token "$PERSONA_SPIKE_TOKEN"
```

For a true UDP-blocked test (not just relay-forced): run the browser on a network that
drops outbound UDP (e.g. macOS `pf` rule `block drop out proto udp from any to any port != 53`
on a test machine, or a phone hotspot with a corporate VPN), open
`https://<host>/?token=...`, and confirm the log shows `pc: connected`;
`window.spike.stats()` should report `relayProtocol: "tcp"` or `"tls"`.

## Deploy (NOT done — needs Darran's go-ahead; creates cloud resources)

Fly.io (recommended, see decision record):
```
fly apps create persona-voice-spike
fly secrets set -a persona-voice-spike CLOUDFLARE_TURN_KEY_ID=… CLOUDFLARE_TURN_API_TOKEN=… PERSONA_SPIKE_TOKEN=…
fly deploy -c infra/voice-spike/fly.spike.toml .          # from repo root
python infra/voice-spike/call_proof.py --url https://persona-voice-spike.fly.dev --token …
python infra/voice-spike/call_proof.py --url https://persona-voice-spike.fly.dev --token … --relay
fly apps destroy persona-voice-spike                      # after the spike
```
Cloudflare TURN key: Cloudflare dashboard → Realtime → TURN → Create key (gives key id + API token).

Railway (alternative): new service from `infra/voice-spike/spike.Dockerfile`, set the same
variables, `PORT=8080`. Railway has no inbound UDP at all, so TURN on both legs is mandatory.
