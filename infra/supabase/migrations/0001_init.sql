-- Persona onboarding: Postgres is the single source of truth for session state.
-- Single writer: services/agent. apps/web never writes these tables directly.

create type session_status as enum ('active', 'graduated', 'abandoned');
create type channel as enum ('text', 'voice');
create type slot_status as enum ('empty', 'candidate', 'filled', 'skipped');

create table sessions (
  id                uuid primary key default gen_random_uuid(),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  version           integer not null default 0,          -- optimistic concurrency (brain bumps per turn)
  status            session_status not null default 'active',
  node              text not null default 'greet',
  active_channel    channel,
  flow_version      integer not null,
  -- slots: {"agent_name": {"value": "...", "status": "filled", "source": "text",
  --          "confidence": 0.93, "validated_by": "agent_name", "attempts": 1}, ...}
  slots             jsonb not null default '{}'::jsonb,
  node_attempts     jsonb not null default '{}'::jsonb,
  deferred_prompts  text[] not null default '{}',
  graduated_at      timestamptz,
  -- voice lease: exactly one live call per session (double-dial / multi-tab lock)
  call_lease_holder text,
  call_lease_expires_at timestamptz
);

create table session_events (
  id          bigserial primary key,
  session_id  uuid not null references sessions(id) on delete cascade,
  at          timestamptz not null default now(),
  channel     channel,
  kind        text not null,       -- user_utterance | bot_utterance | extraction | transition |
                                   -- tool_call | ui_push | call_started | call_ended | error
  payload     jsonb not null default '{}'::jsonb,
  trace_id    text                 -- agent.obs trace id (Langfuse when enabled)
);
create index on session_events (session_id, at);

create table calls (
  id           uuid primary key default gen_random_uuid(),
  session_id   uuid not null references sessions(id) on delete cascade,
  started_at   timestamptz not null default now(),
  ended_at     timestamptz,
  end_reason   text,               -- user_hangup | network_timeout | bot_end | taken_over | error
  reconnects   integer not null default 0
);

create table gmail_connections (
  session_id        uuid primary key references sessions(id) on delete cascade,
  google_sub        text not null,
  email             text not null,
  scopes            text[] not null,
  refresh_token_enc bytea,          -- encrypted at rest (app-level key); null if scopes are identity-only
  connected_at      timestamptz not null default now()
);
