# Cloudflare Realtime TURN key (manual, ~5 min)

Why (ADR `docs/decisions/0001-voice-hosting.md`): Fly gives the agent no inbound UDP, so BOTH
call legs (browser and the agent's SmallWebRTC peer) must relay through TURN, and callers on
UDP-blocked networks need TURN over TCP/TLS 443. The agent mints short-lived credentials per
call from a long-lived TURN key (`services/agent/agent/voice/ice.py` →
`POST https://rtc.live.cloudflare.com/v1/turn/keys/<key id>/credentials/generate-ice-servers`)
and serves the same list to the browser via `GET /api/session/ice`. The long-lived token
never leaves the agent.

Names only below — values go in `.persona-deploy/agent.env` (gitignored), never in git.

## 1. Create the key
1. dash.cloudflare.com → log in with the account Darran owns (a free plan is fine).
2. Left nav **Realtime** (sometimes under *Media* / "Calls") → **TURN Server** →
   **Create** (a.k.a. *Create TURN key*).
3. Name it `persona-onboarding-agent`. Create.
4. The page shows two values **once**:
   - **Turn Token ID** → `CLOUDFLARE_TURN_KEY_ID` (not secret, but keep it with the token)
   - **API Token** → `CLOUDFLARE_TURN_API_TOKEN` (**secret**; copy it now — it can't be shown
     again; if lost, delete the key and create a new one)
5. Put both in `.persona-deploy/agent.env`. Agent only — NOT in the web env and never in
   `NEXT_PUBLIC_PERSONA_ICE_URLS` (that is inlined into the browser bundle).

## 2. Ship it
- `python3 scripts/check-env.py --target agent --env-file .persona-deploy/agent.env` → both
  names `present`.
- `bash scripts/deploy/fly-agent.sh --apply --secrets-only` (stages them) then a deploy, or
  the full `fly-agent.sh --apply` in the RUNBOOK order. At boot the agent log's env report
  must NOT list `turn` under disabled features.

## 3. Verify
- `bash scripts/deploy/smoke.sh https://<vercel-domain> https://<fly-app>.fly.dev` → check 6
  `PASS ICE .../api/session/ice (N servers, TURN present)`.
  `FAIL ... no turn: URLs` = vars missing/wrong on Fly, or the key was deleted.
- Real call from a laptop, then from a phone on cellular (usually UDP-hostile): the call
  connects and the agent log shows the call set up. Optional deeper check:
  `chrome://webrtc-internals` → selected candidate pair `relay`, protocol `tcp`/`tls` on the
  UDP-blocked network.
- Local relay-only proof (optional, spends a few MB of TURN): put the two names in your shell
  from the password manager and run `python infra/voice-spike/call_proof.py --relay`
  → `pair.local == "relay"`, `PASS`.

## Cost / limits
Free tier: 1,000 GB/month of TURN egress, then $0.05/GB. A 15-minute call (Opus audio) relayed on
both legs is on the order of tens of MB — review traffic is ~$0. Cloudflare has no hard spend
cap for Realtime: set a **Billing → Notifications** usage alert (e.g. $5) instead
(see `docs/deploy/RUNBOOK.md` → spend caps). `PERSONA_VOICE_MAX_CALL_SECS` (default 900)
caps each call on our side.

## Rotate / revoke
Create a second key, swap both names on Fly (`fly-agent.sh --apply --secrets-only`, then
`fly deploy` or `fly secrets deploy`), confirm smoke check 6, then delete the old key in the
dashboard. Deleting the key immediately breaks new calls; live calls keep their minted creds
until the TTL.

## Fallback (if Cloudflare stalls)
Static TURN (Twilio Network Traversal / metered.ca): set `PERSONA_TURN_URLS` (comma-separated,
include a `turns:...:443?transport=tcp` URL), `PERSONA_TURN_USERNAME`,
`PERSONA_TURN_CREDENTIAL` instead of the two Cloudflare names. Used only if the Cloudflare
names are unset.
