# Deploy state (INFRA-002b) — single source of truth; read before running any `--apply`

**Rule:** whoever runs `scripts/deploy/* --apply` first sets `OWNER`/`STATE` here and commits,
so the hourly reconcile and the EM never double-deploy. Only the OWNER may run `--apply`.

| Field | Value |
|---|---|
| STATE | **BLOCKED** (not started — nothing created in Supabase/Fly/Vercel) |
| OWNER | EM executor (claimed Sun Sep 27 8:45pm PT) |
| Go-ahead | Darran, Sun Sep 27 8:09pm PT |

## Env files (gitignored, mode 600, values never committed/printed)
- `.persona-deploy/agent.env` (read by supabase-db.sh / fly-agent.sh) = `services/agent/.env`
- `.persona-deploy/web.env` (read by vercel-web.sh) = `apps/web/.env.local`
- Blank until first deploy: `PERSONA_AGENT_BASE_URL`, `GOOGLE_OAUTH_REDIRECT_URL`.

## Key verification (Sun 8:40pm PT)
Anthropic PASS · Deepgram PASS · Cartesia PASS · Cloudflare TURN mint PASS ·
**Postgres FAIL** — `PERSONA_DATABASE_URL` still contains the Supabase template placeholder
password, and the direct host `db.<ref>.supabase.co` is IPv6-only (Darran's Mac has no IPv6).

## Blockers (Darran)
1. Supabase → Project Settings → Database: copy the **Session pooler** connection string
   (IPv4, port 5432, user `postgres.<ref>`) with the real DB password (reset it if unknown).
2. `fly auth login` on the Mac (flyctl installed, no token). Vercel CLI is logged in.

## Order once unblocked
supabase-db.sh --apply → fly-agent.sh --apply (sjc) → vercel-web.sh --apply → set
PERSONA_AGENT_BASE_URL (Vercel) + GOOGLE_OAUTH_REDIRECT_URL → vercel redeploy → smoke.sh →
Google Cloud redirect URI / homepage / privacy / authorized domain.
