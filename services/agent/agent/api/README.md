# agent/api — HTTP surface used by apps/web (packet FLOW-003)

- `POST /v1/sessions` -> create session, returns id (web stores it in a cookie)
- `GET  /v1/sessions/{id}` -> state snapshot (slots, node, deferred, call status)
- `POST /v1/sessions/{id}/turns` -> text turn: extraction (Anthropic SDK) -> brain -> phrasing
- `POST /v1/sessions/{id}/gmail` -> server-to-server from the Next.js OAuth callback
  with the verified address (shared secret); fills `gmail`, notifies a live call
- `POST /v1/sessions/{id}/call` -> SmallWebRTC offer/answer (acquires the call lease)
- `DELETE /v1/sessions/{id}/call/{call_id}` -> release the call lease (`{"reason": ...}` optional)
- `GET  /v1/sessions/{id}/events` -> SSE stream (UI pushes: gmail card, transcript, state)
- `GET  /health`

Rate limits per IP + per session; all mutating routes require the session token.

## Contract details (FLOW-003)

- Run: `uvicorn agent.api.app:create_app_from_env --factory` with `PERSONA_DATABASE_URL`,
  `PERSONA_INTERNAL_SECRET` (gmail route; unset -> 503, fail closed),
  `PERSONA_CALL_LEASE_TTL_S` (default 120). Migrations: `infra/supabase/migrations/*.sql`.
- `POST /v1/sessions` -> `201 {id, token, reply, state, push_ui, trace_id}`. The session
  runs the brain's `open` event (greeting). Only `sha256(token)` is stored.
- Session token on every `/v1/sessions/{id}/*` route (reads too — slots hold PII):
  `Authorization: Bearer <token>` or `X-Session-Token`; `?token=` is accepted on the two
  GET routes because `EventSource` cannot set headers.
- Turn: `{text, version?}` -> `{reply, state, push_ui, trace_id}`. Path: load -> LLM extract
  (injected `TurnLlm`; `FakeLlm` offline default) -> `brain.apply` -> phrase ->
  `UPDATE sessions ... WHERE id AND version = n` + `session_events` in one transaction.
  A lost race (or stale `version`) -> `409 {"error": "version_conflict"}`; reload and retry.
- Gmail: header `X-Persona-Internal-Secret`; body `{email, google_sub, scopes}`; the only
  path with `oauth_verified=True`. Upserts `gmail_connections` in the same transaction.
- Call: `201 {call_id, lease_expires_at, answer: null, status: "lease_acquired"}`; a second
  acquire while the lease is live -> `409 {"error": "call_in_progress"}`. The SDP answer and
  lease renewal land in VOICE-001.
- SSE: `event: <type>` / `id: <session_events.id>` / `data: <json>`. Types: `transcript`
  `{role, text, channel}`, `state` (snapshot), `gmail_connect_card`, `gmail_connected`,
  `start_call`, `end_call`, `call_state` `{state, call_id}`, `graduate` `{deferred}`.
  Without `Last-Event-ID` the stream replays the session's pushes from the start (rebuilds
  the chat on reload); with it, only later pushes. Keepalive comment every 15 s.
- Errors: 401 bad token/secret, 404 unknown session, 409 conflict/lease, 422 bad body,
  429 rate limited.
