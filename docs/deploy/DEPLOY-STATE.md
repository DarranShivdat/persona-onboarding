# Deploy state (INFRA-002b) — single source of truth; read before running any `--apply`

**Rule:** whoever runs `scripts/deploy/* --apply` first sets `OWNER`/`STATE` here and commits,
so the hourly reconcile and the EM never double-deploy. Only the OWNER may run `--apply`.

| Field | Value |
|---|---|
| STATE | **REDEPLOYING — agent + web from main 904c0da (HONEST-001 fixed need ack / permissions-only Gmail pitch, NAME-003 name read-back until explicit yes). Previous LIVE: agent 49d8010, web 64b0652** |
| OWNER | EM executor (honest-need/name fix) — started 1:15pm PT Mon |
| Go-ahead | Darran, Sun Sep 27 8:09pm PT |

## Env files (gitignored, mode 600, values never committed/printed)
- `.persona-deploy/agent.env` (read by supabase-db.sh / fly-agent.sh) = `services/agent/.env`
- `.persona-deploy/web.env` (read by vercel-web.sh) = `apps/web/.env.local`
- `.persona-deploy/web.env` now has PERSONA_AGENT_BASE_URL + GOOGLE_OAUTH_REDIRECT_URL; `apps/web/.env.local`
  keeps them blank on purpose (local dev must not hit the prod agent/DB).

## Key verification (Sun 10:36pm PT)
Anthropic PASS · Deepgram PASS · Cartesia PASS · Cloudflare TURN mint PASS ·
**Postgres PASS** — Session pooler `aws-0-us-east-1.pooler.supabase.com:5432` (IPv4, user
`postgres.<ref>`, password percent-encoded, `sslmode=require`); `select 1` OK from box and Mac.
Fly: `fly auth whoami` PASS (`darranshivdat1@gmail.com`).

## Live (Sun Sep 27, 10:55pm PT)
- Web (Vercel `persona-onboarding-darran`, scope darran-s-projects): https://persona-onboarding-darran.vercel.app
- Agent (Fly `persona-onboarding-agent`, sjc, 1 machine shared-cpu-2x/2GB, `--ha=false`): https://persona-onboarding-agent.fly.dev
- DB: Supabase session pooler (us-east-1); migrations 0001–0004 applied.
- Vercel env (Production): PERSONA_AGENT_BASE_URL, PERSONA_INTERNAL_SECRET, GOOGLE_OAUTH_CLIENT_ID,
  GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REDIRECT_URL. Fly secrets: the 10 agent.env names.
- Hosted smoke: smoke.sh 7/7; `/`, `/about`, `/privacy` 200; agent `GET /v1/sessions/{id}/ice`
  → stun+turn+turns; hosted browser voice call (real-agent-call.spec, desktop + mobile, real
  Deepgram/Cartesia/Claude over Cloudflare TURN) PASS.
- Redeploy agent: `bash scripts/deploy/fly-agent.sh --apply --deploy-only` (~20s downtime, 1 machine).
  Redeploy web: `bash scripts/deploy/vercel-web.sh --apply --scope darran-s-projects --deploy-only`.
- Known: agent-side aioice logs `TransactionFailed 401` on TURN ChannelBind (media still flows);
  `transport_cleanup` teardown step can hang → capped at 5s/step. DB us-east-1 vs agent sjc (~65ms/query).

## Order (done)
supabase-db.sh --apply → fly-agent.sh --apply (sjc) → vercel-web.sh --apply → set
PERSONA_AGENT_BASE_URL (Vercel) + GOOGLE_OAUTH_REDIRECT_URL → vercel redeploy → smoke.sh →
Google Cloud redirect URI / homepage / privacy / authorized domain.

## Google Cloud values (CONFIRMED — Vercel production alias assigned)
Vercel project `persona-onboarding-darran` (`persona-onboarding.vercel.app` is a third party's).
- Authorized redirect URI: `https://persona-onboarding-darran.vercel.app/api/oauth/google/callback`
- Authorized JavaScript origin (optional): `https://persona-onboarding-darran.vercel.app`
- Branding → Application home page: `https://persona-onboarding-darran.vercel.app/about`
- Branding → Privacy policy link: `https://persona-onboarding-darran.vercel.app/privacy`
- Branding → Authorized domain: `vercel.app` is on the Public Suffix List, so Google requires
  the full host `persona-onboarding-darran.vercel.app` (Google accepts PSL entries+1 label).
- Fly agent (server-side only, not in Google): `https://persona-onboarding-agent.fly.dev`

## Log
- Sun 10:35PM PT — DB fixed (pooler); running supabase-db.sh --apply
- Sun 10:35PM PT — supabase-db.sh --apply: 4 applied (0001-0004), re-status 4 applied/0 drifted
- Sun 10:36PM PT — fly auth ok (personal org); running fly-agent.sh --apply (sjc, --ha=false)
- Sun 10:43PM PT — Fly app created (sjc, 1 machine), /health ok db ok; call path ImportError libxcb → Dockerfile fix, redeploying
- Sun 10:43PM PT — vercel-web.sh --apply (project persona-onboarding-darran, root apps/web)
- Sun 10:44PM PT — Vercel web live at persona-onboarding-darran.vercel.app (/, /about, /privacy 200); setting GOOGLE_OAUTH_REDIRECT_URL + redeploy
- Sun 10:46PM PT — Fly agent v2 (opencv libs fix) healthy, warmup ok; Vercel redeployed with GOOGLE_OAUTH_REDIRECT_URL
- Sun 10:48PM PT — reconcile smoke.sh PASS (7/7): agent /health db ok (sha 67efd12), web / (build 406fb5c), session+turn via proxy, agent session+turn, ICE+TURN, OAuth start redirect
- Sun 10:48PM PT — remaining Darran: Google Console test users + Branding homepage/privacy/authorized domain (URLs in this file); OAuth app may stay Testing
- Sun 10:51PM PT — smoke.sh 7/7 PASS; hosted browser call connects+captions but hangup DELETE hung → teardown timeouts fix, redeploying agent
- Sun 10:55PM PT — agent hotfix (hangup wait 1s) deployed; smoke.sh 7/7, pages 200, hosted browser call desktop+mobile PASS
- Sun 10:55PM PT — DEPLOY-STATE finalized (Live section, Google values confirmed); removed auto-generated vercel.json
- Sun 10:56PM PT — claim: Vercel web-only redeploy for /privacy contact + 30-day retention (Darran approved 10:56pm); agent untouched
- Sun 10:58PM PT — web-only redeploy done (572108f): /privacy shows mailto darranshivdat1@gmail.com + 30-day retention, verified live; agent unchanged
- Sun 11:11PM PT — 11:1xpm PT EM executor: agent redeploy 13ef15d (prod text chat was FakeLlm; now Claude extract + reaction-only phrasing)
- Sun 11:15PM PT — 11:15PM PT EM executor: agent ddd8ac5 LIVE (1st attempt crashed: product-facts.md missing from image → fixed Dockerfile/.dockerignore). smoke 7/7; live text chat now Claude-backed.
- Sun 11:21PM PT — 11:21PM PT EM executor: agent redeploy 5273e0d (need-vs-refusal, post-call copy, pitch filter)
- Sun 11:22PM PT — 11:22PM PT EM executor: agent 56ed4f5 LIVE; smoke 7/7
- Sun 11:39PM PT — 11:39PM PT EM executor: agent redeploy f42fb63 (NAME-001 name read-back/spelling)
- Sun 11:43PM PT — 11:43PM PT EM executor: agent 3f61b67 (NAME-001) LIVE; smoke 7/7 from box. NOTE: Vercel Security Checkpoint (x-vercel-mitigated: challenge) is challenging the Mac's IP after heavy automated traffic — curl/Playwright from the Mac get 403; box IP fine.
- Sun 11:56PM PT — 11:56PM PT EM executor: agent+web redeploy 9819060 (GRAD-001, GUARD-001, AUDIT-001; qa fast/flow/e2e/audit green)
- Mon 12:06AM PT — 12:06AM PT EM executor: agent 3521105 + web 7957763 LIVE (GRAD/GUARD/AUDIT). smoke 7/7 from box; live home-screen chat + /edit verified.
- Mon 12:59AM PT — 12:59AM PT EM executor: agent hotfix 146cf19: GUARD-001's voice max_tokens=120 truncated record_slots (live probe: every caller turn got 'Are you still there?' since 12:06am deploy)
- Mon 1:10AM PT — 01:08 PT hotfix 7a4ea2a verified live (health sha, real voice turn → tool call → spoken line). LAT-001 integration next.
- Mon 1:21AM PT — 01:24 PT EM executor holds deploy: merged LAT-002 (817ebc5) onto main; running qa:e2e + qa:audit, then agent+web redeploy + latency probe. Other owners: do not --apply until LIVE.
- Mon 1:35AM PT — 01:40 PT agent+web LAT-001/002 deployed (agent sha 415f15c, web e2fc98f) and probed; live probe found spelled name split by Smart Turn -> 'An'; redeploying agent with fix be8e932.
- Mon 1:47AM PT — 01:47 PT EM reconcile: be8e932 redeploy confirmed LIVE (agent /health sha d4de37d, Fly machine started checks passing); smoke.sh 7/7 PASS; post-fix latency probes ~2.0–2.5s user-stop→first-audio (was ~8s pre-LAT); qa:audit LIVE PASS 01:45; STATE → LIVE.
- Mon 2:22AM PT — EM executor (voice polish) holds deploy: merged em/voice-polish (9f04f1e); qa fast/flow/harness/e2e/audit/visual + real-agent call smoke green; agent+web redeploy then smoke/audit/latency probe. PERSONA_VOICE_QUICK_ACK stays unset (OFF).
- Mon 2:28AM PT — EM executor (voice polish): agent+web redeploy b7f24f8 LIVE (agent /health sha b7f24f8, Fly v13 checks passing; web build b7f24f8 aliased). smoke.sh 7/7 + LIVE button audit 215 rows PASS. PERSONA_VOICE_QUICK_ACK unset (OFF). Note: web deploy needs `--scope darran-s-projects --deploy-only`. STATE → LIVE.
- Mon 12:47PM PT — EM executor (reset/home fix) holds deploy: merged em/reset-fix; qa fast/flow/e2e/audit (LOCAL 246 rows) green; agent+web redeploy, then live smoke + audit. Fly secrets untouched (PERSONA_VOICE_QUICK_ACK=1 stays on).
- Mon 12:58PM PT — EM executor (reset/home fix): agent+web 64b0652 deployed; smoke.sh 7/7 + LIVE button audit 246 rows PASS (Start over full flow on resume + home). Follow-up agent redeploy 49d8010 (spelling-check "yes" confirms unchanged name), /health sha 49d8010, live replay of the 12:30pm transcript OK. Web unchanged since 64b0652. Fly secrets untouched (quick ack still set). STATE → LIVE.
