-- Close the gap between 0001 `sessions` and agent/brain/state.py `SessionState`,
-- plus the per-session bearer token used by agent/api (FLOW-003).

alter table sessions
  add column explained           text[]  not null default '{}',   -- nodes whose `why` was already given
  add column call_offer_resolved boolean not null default false,  -- call offered and answered
  add column token_hash          text;                            -- sha256 of the session bearer token

-- Fast Last-Event-ID replay for the SSE stream (id is monotonic per insert).
create index on session_events (session_id, id);
