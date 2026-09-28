# Deploy RUNBOOK — Mon Sep 28, target: hosted URL by 12:00 PT

Ordered ~30-minute checklist for the first production deploy (ADR 0001: agent on Fly `sjc`,
web on Vercel, Postgres on Supabase, Cloudflare Realtime TURN on both call legs). Run from the
repo root on `main`. Every `scripts/deploy/*.sh` is a **dry run by default** — run it once
without `--apply`, read what it will do, then re-run with `--apply`.

Rules: values never go in git, chat, or logs. Env files live in the gitignored
`.persona-deploy/`. Only `check-env.py --emit` outputs values, and only into a pipe.
Account logins (`fly auth login`, `vercel login`, dashboards) are Darran's.

| # | Step | Who | ~min |
|---|---|---|---|
| 0 | Pre-flight: local stack green | EM/any | 5 (before Monday) |
| 1 | Vendor keys + spend caps → env files | Darran | 8 |
| 2 | `check-env.py` both targets | EM | 1 |
| 3 | `supabase-db.sh --apply` | EM | 3 |
| 4 | `fly-agent.sh --apply` | EM | 6 |
| 5 | `vercel-web.sh --apply` | EM | 5 |
| 6 | Google redirect URI + test users | Darran | 3 |
| 7 | Cloudflare TURN check | EM | 1 |
| 8 | `smoke.sh` + one real call | EM + Darran | 4 |

---

## 0. Pre-flight (the night before; no cloud)
```bash
npm ci && npm run qa:fast
scripts/local-stack.sh up          # scratch PG + migrations + agent :8200 + next :3200
scripts/local-stack.sh smoke       # same smoke.sh as prod; expect SMOKE PASS (OAuth SKIP offline)
scripts/local-stack.sh down
for s in supabase-db fly-agent vercel-web; do bash scripts/deploy/$s.sh >/dev/null || echo "FAIL $s"; done
```
- [ ] qa:fast green · [ ] local smoke PASS · [ ] three dry runs exit 0
- [ ] Tools: `brew install flyctl libpq` (psql); Node 22 (`npx vercel@latest` is fetched on use).

## 1. Keys → env files (Darran, ~8 min)
```bash
mkdir -p .persona-deploy && chmod 700 .persona-deploy
cp services/agent/.env.example .persona-deploy/agent.env
cp apps/web/.env.example       .persona-deploy/web.env
chmod 600 .persona-deploy/*.env
```
Fill by name (how-to per name: `docs/deploy/ENV.md`):

| Name | agent.env | web.env | Source |
|---|---|---|---|
| `PERSONA_DATABASE_URL` | ✓ | | step 3 (Supabase session pooler, `?sslmode=require`) |
| `PERSONA_INTERNAL_SECRET` | ✓ | ✓ **same value** | `openssl rand -hex 32` |
| `PERSONA_TOKEN_ENCRYPT_KEY` | ✓ | | ENV.md generator; never rotate casually |
| `ANTHROPIC_API_KEY` | ✓ | | console.anthropic.com |
| `DEEPGRAM_API_KEY` | ✓ | | console.deepgram.com |
| `CARTESIA_API_KEY` | ✓ | | play.cartesia.ai |
| `CLOUDFLARE_TURN_KEY_ID`, `CLOUDFLARE_TURN_API_TOKEN` | ✓ | | `scripts/deploy/cloudflare-turn.md` |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | ✓ | ✓ | `scripts/deploy/google-oauth.md` |
| `GOOGLE_OAUTH_REDIRECT_URL` | | ✓ | `https://<vercel-domain>/api/oauth/google/callback` |
| `PERSONA_AGENT_BASE_URL` | | ✓ | `https://persona-onboarding-agent.fly.dev` (no trailing `/`) |
| `LANGFUSE_*` + `PERSONA_TRACING=langfuse` | optional | | Langfuse project keys |

The Vercel domain is predictable (`https://persona-onboarding.vercel.app` if the project name
is free); if step 5 gives a different one, fix `GOOGLE_OAUTH_REDIRECT_URL` + step 6 and re-run
`vercel-web.sh --apply --env-only` + `--deploy-only`.

### Spend caps (set while you're in each console)
| Vendor | Where | Set |
|---|---|---|
| Anthropic | Console → Settings → Limits (workspace) | monthly spend limit **$50** (+ email alert at $20) |
| Deepgram | Console → Billing | prepaid credit only (no auto-recharge); alert on low balance |
| Cartesia | play.cartesia.ai → Billing | plan with hard credit limit, **no overage / auto top-up** |
| Fly.io | Dashboard → Billing | one `shared-cpu-1x` 512 MB machine (~$3–5/mo); billing alert **$10**; never scale count > 1 |
| Vercel | Settings → Billing → Spend Management | Hobby = capped; on Pro set spend limit **$20** + pause projects |
| Supabase | Org → Billing | Free plan, or Pro with **Spend Cap ON** |
| Cloudflare | Billing → Notifications | usage alert **$5** (no hard cap; 1 TB/mo TURN free) |
| Langfuse | Cloud → Billing | Hobby (free) tier |
| Google Cloud | — | no billing account needed for OAuth |
Our own guard: `PERSONA_VOICE_MAX_CALL_SECS=900` (per-call hard cap, `infra/fly.toml`).

## 2. Env contract (names only, local)
```bash
python3 scripts/check-env.py --target agent --env-file .persona-deploy/agent.env
python3 scripts/check-env.py --target web   --env-file .persona-deploy/web.env
```
- [ ] both `RESULT: OK`, no `FORBIDDEN` (test doubles like `PERSONA_VOICE_FAKE_VENDORS` must be unset).

## 3. Database — Supabase (~3 min + project creation)
```bash
bash scripts/deploy/supabase-db.sh            # read the manual steps (project, region, Data API off)
bash scripts/deploy/supabase-db.sh --apply    # migrate (idempotent) + --status
```
- [ ] status lists `0001…0004` as `applied`, `0 drifted`. Re-running is a no-op.
- If it says "schema exists but no tracking rows": the DB was built by the test helper — use
  `scripts/db-migrate.sh "$URL" --baseline` only if you're sure it matches the files.

## 4. Agent — Fly (~6 min, remote build)
```bash
fly auth login                                 # Darran, once
bash scripts/deploy/fly-agent.sh               # dry run: review
bash scripts/deploy/fly-agent.sh --apply       # create app, import secrets, deploy, /health
```
- [ ] `curl -fsS https://persona-onboarding-agent.fly.dev/health` → `"ok":true,"db":"ok"`, `git_sha` = HEAD.
- [ ] `fly logs -a persona-onboarding-agent` boot env report: no disabled `voice`, `turn`, `gmail_*`.
- App name taken? `--app <name>` and update `PERSONA_AGENT_BASE_URL` in web.env.

## 5. Web — Vercel (~5 min)
```bash
npx vercel@latest login                        # Darran, once
bash scripts/deploy/vercel-web.sh              # dry run: review
bash scripts/deploy/vercel-web.sh --apply      # link, env (prod+preview), prod deploy
```
- [ ] **Before the first deploy completes**: Vercel → project → Settings → Build & Deployment:
  Root Directory `apps/web`, Framework Next.js, Node 22.x, "Include files outside the root
  directory" ON (the CLI can't set these). If the first build failed, fix and
  `vercel-web.sh --apply --deploy-only`.
- [ ] Note the production domain. `NEXT_PUBLIC_*` is baked at build → env changes need a redeploy.

## 6. Google OAuth (Darran, ~3 min) — `scripts/deploy/google-oauth.md`
- [ ] Authorized redirect URI **exactly** `https://<vercel-domain>/api/oauth/google/callback`
  (= `GOOGLE_OAUTH_REDIRECT_URL`).
- [ ] Publishing status **Testing**; every reviewer's Google account in **Test users**.

## 7. TURN — `scripts/deploy/cloudflare-turn.md`
- [ ] Key created in step 1 and imported in step 4; verified by smoke check 6 below.

## 8. Smoke + a real call (~4 min)
```bash
bash scripts/deploy/smoke.sh https://<vercel-domain> https://persona-onboarding-agent.fly.dev
```
- [ ] `SMOKE PASS` — agent health+db, web 200, session via proxy, text turn, direct agent turn,
  ICE **with TURN**, OAuth start → accounts.google.com with our callback.
- [ ] Manual, as a test user: name the agent → take the call (laptop) → say your name →
  connect Gmail → one need → graduate. Repeat the call on a phone over cellular (TURN path).
- [ ] Hang up mid-call and reload: the conversation resumes at the same step (EC-04/EC-08).
- [ ] Post the URL.

---

## Rollback
| Component | Command | Notes |
|---|---|---|
| Agent | `fly releases -a persona-onboarding-agent` → `fly deploy -a persona-onboarding-agent -c infra/fly.toml --image <previous image ref>` | ~1–2 min; in-flight calls drop, state survives in Postgres |
| Agent secrets | fix `.persona-deploy/agent.env` → `bash scripts/deploy/fly-agent.sh --apply --secrets-only` → `fly secrets deploy -a persona-onboarding-agent` | |
| Web | `npx vercel@latest rollback` (previous prod) or `npx vercel@latest rollback <deployment-url>` | instant alias swap; `vercel ls` lists deployments |
| Web env | fix `web.env` → `vercel-web.sh --apply --env-only` → `--deploy-only` | env is read at build for `NEXT_PUBLIC_*` |
| DB | forward-only migrations; Supabase → Database → Backups | trial data is disposable: recreate project + step 3 |
| Kill switch (spend) | `fly scale count 0 -a persona-onboarding-agent` | web shows the agent as unavailable; `fly scale count 1` to restore |

## When something fails
| Symptom | Likely cause | Fix |
|---|---|---|
| smoke 1 `db=error` | wrong pooler (:6543) / missing `sslmode=require` / password | session pooler :5432 URL; `--secrets-only` + `fly secrets deploy` |
| smoke 3/4 401/502 | `PERSONA_INTERNAL_SECRET` differs agent vs web, or `PERSONA_AGENT_BASE_URL` wrong | make identical; redeploy web |
| smoke 6 `no turn:` | TURN names missing on Fly | cloudflare-turn.md §2 |
| smoke 7 `redirect_uri_mismatch` / `oauth_unconfigured` | google-oauth.md troubleshooting | |
| call never connects on cellular | TURN not on BOTH legs | check agent boot report `turn` enabled |
| Vercel build fails at root | Root Directory not `apps/web` / files outside root off | step 5 settings |

## Local stack (reference)
`scripts/local-stack.sh up|status|env|smoke|down [--keep-db|--purge]` — agent :8200, web :3200,
scratch PG :55432 db `persona_local`, all state in gitignored `.persona-local/`. Offline by
default (fake voice vendors, FakeLlm); real keys via `.persona-local/keys.env`. `eval
"$(scripts/local-stack.sh env)"` exports `PERSONA_E2E_AGENT_URL` / `PERSONA_WEB_URL` for the
real-agent Playwright specs (`e2e/real-agent-call.spec.ts`, `e2e/ec-08-refresh-resume.spec.ts`;
specs that `seed()` need the stub agent and fail against a real one by design).
