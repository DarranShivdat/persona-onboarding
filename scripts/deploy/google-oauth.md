# Google OAuth client for Gmail connect (manual, ~10 min)

Creates the OAuth client behind `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`.
Everything here is in the Google Cloud console; nothing is scriptable from this repo. Names
only — paste values into `.persona-deploy/{agent,web}.env` (gitignored), never into git.

The code that consumes it: `apps/web/lib/oauth/google.ts` (start + code exchange, scopes),
`apps/web/app/api/oauth/google/{start,callback}` (routes), `services/agent/agent/gmail/*`
(refresh-token refresh, AES-GCM at rest with `PERSONA_TOKEN_ENCRYPT_KEY`).

## 1. Project + Gmail API
1. console.cloud.google.com → project picker → **New project** `persona-onboarding`
   (or reuse one Darran owns). No billing account is needed.
2. **APIs & Services → Library → Gmail API → Enable.**

## 2. OAuth consent screen (Google Auth Platform)
1. **APIs & Services → OAuth consent screen** (a.k.a. Google Auth Platform → Branding).
2. User type **External**. App name `Persona`, support email = Darran's, developer contact =
   Darran's. App domain / logo: leave empty (adding a logo triggers brand verification).
3. **Data access → Add or remove scopes**, add exactly:

   | Scope | Why | Google class |
   |---|---|---|
   | `openid` | ID token → which account connected (EC-22 wrong account) | non-sensitive |
   | `email` | show the connected address on the Gmail card | non-sensitive |
   | `profile` | display name | non-sensitive |
   | `https://www.googleapis.com/auth/gmail.readonly` | "read" capability | restricted |
   | `https://www.googleapis.com/auth/gmail.modify` | "organize" (labels/archive; implies read) | restricted |
   | `https://www.googleapis.com/auth/gmail.send` | "send" capability | sensitive |

   Must match `SCOPES` in `apps/web/lib/oauth/google.ts`. Missing scopes on the screen don't
   break the flow in testing mode, but the consent page must list what we ask for.
4. **Audience → Publishing status: Testing.** Do NOT click "Publish app": restricted Gmail
   scopes then require Google verification + a security assessment (weeks).
5. **Audience → Test users → Add users**: every reviewer's Google address (Zach, the CTO,
   Darran, anyone stress-testing). Max 100. Anyone NOT on this list gets
   `Error 403: access_denied` — that's the #1 failure mode on review day.

Testing-mode facts to expect (not bugs):
- Consent shows **"Google hasn't verified this app"** → testers click *Continue*.
- Refresh tokens for apps in Testing **expire after 7 days**; a reviewer reconnecting after a
  week is normal (`token_status` goes stale, the Gmail card offers reconnect).

## 3. OAuth client (Web application)
1. **APIs & Services → Credentials → Create credentials → OAuth client ID →
   Web application**, name `persona-onboarding-web`.
2. **Authorized JavaScript origins**: `https://<vercel-domain>` (e.g.
   `https://persona-onboarding-darran.vercel.app`). Optional for our server-side flow, harmless.
3. **Authorized redirect URIs** — exact string match, scheme + host + path, no trailing slash:
   - `https://<vercel-domain>/api/oauth/google/callback`
   - optional local: `http://localhost:3200/api/oauth/google/callback` (local-stack) — Google
     allows plain http only for `localhost`.
4. Create → copy **Client ID** and **Client secret** (the secret is shown once; download the
   JSON to your password manager, not the repo).

## 4. Where the values go (names only)

| Name | `.persona-deploy/agent.env` (Fly) | `.persona-deploy/web.env` (Vercel) |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | yes | yes |
| `GOOGLE_OAUTH_CLIENT_SECRET` | yes (secret) | yes (secret) |
| `GOOGLE_OAUTH_REDIRECT_URL` | — | `https://<vercel-domain>/api/oauth/google/callback` |

Pin `GOOGLE_OAUTH_REDIRECT_URL` so preview deployments can't drift to a URI Google rejects.
Never set `GOOGLE_OAUTH_AUTH_URL` / `_TOKEN_URL` / `_ISSUER` in prod (test doubles;
`check-env.py --target web` rejects them).

Local (optional): add `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` to
`.persona-local/keys.env`; `scripts/local-stack.sh smoke` then also checks the OAuth redirect.

## 5. Verify
- `bash scripts/deploy/smoke.sh https://<vercel-domain> https://<fly-app>.fly.dev` → check 7
  `PASS OAuth start → accounts.google.com (redirect_uri https://<vercel-domain>/api/oauth/google/callback)`.
  A `WARN redirect_uri host differs` line means `GOOGLE_OAUTH_REDIRECT_URL` ≠ the web domain.
- Manually, as a **test user**: open the site → Connect Gmail → consent → back on the chat
  with the Gmail card filled.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error 400: redirect_uri_mismatch` | URI in step 3.3 ≠ `GOOGLE_OAUTH_REDIRECT_URL` (http vs https, `www`, trailing `/`, preview domain) | make them byte-identical; redeploy web if the env changed |
| `Error 403: access_denied` "has not completed the Google verification process" | account not in Test users | add it (step 2.5); takes effect immediately |
| smoke check 7 `oauth_unconfigured` | `GOOGLE_OAUTH_CLIENT_ID/SECRET` missing on Vercel | `vercel-web.sh --apply --env-only`, then redeploy |
| Gmail filled, but later "reconnect" | 7-day testing-mode refresh-token expiry, or `PERSONA_TOKEN_ENCRYPT_KEY` rotated | reconnect; never rotate the key casually |
