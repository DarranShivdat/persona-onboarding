# infra

| Component | Host | Notes |
|---|---|---|
| `apps/web` | Vercel | preview URL per branch = design-review gate target |
| `services/agent` | Fly.io (`fly.toml`) — alt: Railway / Pipecat Cloud | long-lived; WebRTC media; INFRA-001 decides after a UDP/TURN spike |
| Postgres | Supabase | `supabase/migrations/*.sql`; agent is the single writer |
| Tracing/evals | Langfuse Cloud (or self-hosted) | behind `agent.obs` / `harness/evals` interfaces |

Nothing is deployed from this scaffold. No remote is configured.
