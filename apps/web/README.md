# apps/web — Next.js (Vercel)

Shell from `docs/design/spec.md` (FE-001). `/` runs the real `ApiSessionDriver` (FE-002)
when `PERSONA_AGENT_BASE_URL` is set, else `MockSessionDriver`; `/?state=` is always mock.

- `lib/session/types.ts`: `SessionDriver` contract (ARCHITECTURE §6) + `applyPush` render reducer.
- `lib/session/mock-driver.ts` + `fixtures.ts`: replays fixed spec states; `/?state=<name>`
  opens any of them (used by `harness/visual`).
- `lib/session/api-driver.ts`: real driver. Talks only to the same-origin proxy
  `app/api/session/*` (create/resume, `turns`, `events` SSE, `call` lease), folds SSE pushes
  (de-duped by event id, reconnect from the last id) into a snapshot via the render-only
  mapping in `agent-state.ts`. Refresh / return visits: `page.tsx` resolves the session from
  the httpOnly `persona_session` cookie (`<id>.<token>`) server-side; the SSE replay
  rebuilds the thread (EC-08/30/31).
- Env (server-only, never `NEXT_PUBLIC_`): `PERSONA_AGENT_BASE_URL` — agent API origin.
- `app/tokens.css` is generated from `docs/design/tokens.json` (`npm -w apps/web run tokens`;
  `prebuild` fails if stale).
- `<meta name="build-sha">` comes from `VERCEL_GIT_COMMIT_SHA` / `PERSONA_BUILD_SHA` / git.

Surfaces (one page, one session):
- **Chat** — the text-adaptive onboarding conversation (server-driven; renders the
  agent API's turns + UI pushes such as the Gmail card).
- **Phone simulator** — browser "call" UI (ringing, connected timer, mute, hang up,
  live captions, speaking indicator) over Pipecat SmallWebRTC to the agent service.
- **Gmail connect card** — Google OAuth (testing mode); can be pushed mid-call.
- **Graduation** — hand-off into the "main experience" with deferred prompts.

Rules: no transition logic in the browser (the agent brain owns progress); the
session id lives in an httpOnly cookie; all agent calls go through Next route
handlers (server-side secret), except the WebRTC offer which carries a
short-lived session token.
