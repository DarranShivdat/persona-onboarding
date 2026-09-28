# persona-onboarding

**▶ Live demo: https://persona-onboarding-darran.vercel.app**

**Reviewers, start here: [`docs/REVIEWER.md`](docs/REVIEWER.md)** (5-minute happy path, Gmail
test-user note, what's cut, caveats). Walkthrough script: [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md).

**Start over / `?reset=1`:** the session is kept in a cookie, so a reload resumes it. Use the
**Start over** button in the header (with a confirm step) or open `/?reset=1` to begin a fresh
onboarding.

> Gmail connect uses Google OAuth in **testing mode**: only invited Google accounts can connect;
> choose *Continue* on the "unverified app" notice and tick the Gmail boxes (or *Select all*).

---

Hosted, conversational onboarding for an AI assistant (Persona CTO trial): adaptive text
chat + a browser "phone call" that collect the agent's name, the user's name, a connected
Gmail, and what they need help with — resilient to hangups and stress testing.

- Architecture: `docs/ARCHITECTURE.md` · Roadmap/packets: `docs/ROADMAP.md`,
  `docs/orchestration/packets/` · Orchestration: `AGENTS.md`, `docs/orchestration/LOCAL-SUPERVISOR.md`
- Worker rules: `CLAUDE.md` · Flow spec: `packages/flow/flow.yaml` · Edge cases: `harness/edge-cases.yaml`

```
apps/web          Next.js (Vercel): chat, phone simulator, Gmail card, graduation   [scaffold]
services/agent    Python: brain (flow engine), voice (Pipecat), api, obs            [skeleton]
packages/flow     flow.yaml + schema (state machine as data)
harness           qa tiers, edge-case catalog, evals interface (Langfuse-ready), visual diff
infra             Dockerfile, fly.toml, Supabase migrations
scripts           persona-supervisor, claude-worker (opus|fable, --role impl|design|frontend), mock harness
.claude/skills    vendored Anthropic skills: frontend-design, webapp-testing, claude-api
```

Quick checks: `npm run qa:fast` · `npm run qa` · `npm run qa:harness`
(Python for tests: `PERSONA_PYTHON=/path/to/python` with pyyaml + pytest.)
