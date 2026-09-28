# Deploy state (INFRA-002b) — single source of truth; read before running any `--apply`

**Rule:** whoever runs `scripts/deploy/* --apply` first sets `OWNER`/`STATE` here and commits,
so the hourly reconcile and the EM never double-deploy. Only the OWNER may run `--apply`.

| Field | Value |
|---|---|
| STATE | **BLOCKED** (not started — nothing created in Supabase/Fly/Vercel) |
| OWNER | EM executor (claimed Sun Sep 27 8:34pm PT) |
| Go-ahead | Darran, Sun Sep 27 8:09pm PT |

## Env files (gitignored, mode 600, values never committed/printed)
- `.persona-deploy/agent.env` (read by supabase-db.sh / fly-agent.sh) = `services/agent/.env`
- `.persona-deploy/web.env` (read by vercel-web.sh) = `apps/web/.env.local`
- Blank until first deploy: `PERSONA_AGENT_BASE_URL`, `GOOGLE_OAUTH_REDIRECT_URL`.

## Key verification (Sun 9:51pm PT)
Anthropic PASS · Deepgram PASS · Cartesia PASS · Cloudflare TURN keys SET ·
Google OAuth client SET · Vercel CLI logged in (`npx vercel whoami` ok) ·
**Postgres FAIL** — `PERSONA_DATABASE_URL` still points at the direct host
`db.<ref>.supabase.co:5432` (IPv6-only; Mac has no IPv6). Password field is still a
classic bracketed placeholder (not a real DB password). Host must be the **Session pooler**
(`*.pooler.supabase.com`, user `postgres.<ref>`) with the real password. Fly:
`fly auth whoami` still fails (no access token).

## Blockers (Darran)
1. Supabase → Project Settings → Database: copy the **Session pooler** connection string
   (IPv4, port 5432, user `postgres.<ref>`, real password — not a `[YOUR-…]` placeholder)
   into `services/agent/.env` as `PERSONA_DATABASE_URL` (replace the direct
   `db.<ref>.supabase.co` host and placeholder password).
2. `fly auth login` on the Mac (flyctl installed, no token). Vercel CLI is logged in.

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
