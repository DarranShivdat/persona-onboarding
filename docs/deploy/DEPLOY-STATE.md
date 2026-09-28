# Deploy state (INFRA-002b) — single source of truth; read before running any `--apply`

**Rule:** whoever runs `scripts/deploy/* --apply` first sets `OWNER`/`STATE` here and commits,
so the hourly reconcile and the EM never double-deploy. Only the OWNER may run `--apply`.

| Field | Value |
|---|---|
| STATE | **IN PROGRESS — step 2/6 Fly agent redeploying (image fix) (owner EM executor)** |
| OWNER | EM executor (claimed Sun Sep 27 8:34pm PT; re-claimed 10:36pm PT) — reconcile: do NOT `--apply` |
| Go-ahead | Darran, Sun Sep 27 8:09pm PT |

## Env files (gitignored, mode 600, values never committed/printed)
- `.persona-deploy/agent.env` (read by supabase-db.sh / fly-agent.sh) = `services/agent/.env`
- `.persona-deploy/web.env` (read by vercel-web.sh) = `apps/web/.env.local`
- Blank until first deploy: `PERSONA_AGENT_BASE_URL`, `GOOGLE_OAUTH_REDIRECT_URL`.

## Key verification (Sun 10:36pm PT)
Anthropic PASS · Deepgram PASS · Cartesia PASS · Cloudflare TURN mint PASS ·
**Postgres PASS** — Session pooler `aws-0-us-east-1.pooler.supabase.com:5432` (IPv4, user
`postgres.<ref>`, password percent-encoded, `sslmode=require`); `select 1` OK from box and Mac.
Fly: `fly auth whoami` — no token yet (Darran running `fly auth login`).

## Order once unblocked
supabase-db.sh --apply → fly-agent.sh --apply (sjc) → vercel-web.sh --apply → set
PERSONA_AGENT_BASE_URL (Vercel) + GOOGLE_OAUTH_REDIRECT_URL → vercel redeploy → smoke.sh →
Google Cloud redirect URI / homepage / privacy / authorized domain.

## Google Cloud values (predicted; confirm after `vercel-web.sh --apply` prints the domain)
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
