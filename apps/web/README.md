# apps/web — Next.js (Vercel)

Scaffold only; FE-001 builds the shell from `docs/design/spec.md`.

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
